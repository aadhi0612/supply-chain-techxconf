"""CP-SAT allocation / recovery model.

Decision variables (per order *o*, qualified route *r*, start week *w*):
    s  units started        (consume BOM parts, packaging/test slots)
    g  good units out       (1000*g <= yield_pm*s)
The model enforces, as hard constraints: BOM + qualified-part compatibility, time-phased
cumulative part supply, time-phased resource capacity, route qualification windows,
contractual delivery windows, compliance gates (blocked orders get no variables) and
all-or-nothing / minimum-ship rules. Objectives are solved lexicographically.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass

from ortools.sat.python import cp_model

from ..domain import Lever, PlanInput, Route
from .outcomes import order_outcomes, totals
from .scenarios import Materialized
from .types import (
    ComplianceGate, LeverUse, LevelResult, PartAllocation, PlanResult, Scenario, StartRecord,
)

L_PROTECTED = "protected_on_time_units"
L_WEIGHTED = "weighted_on_time_units"
L_DELIVERED = "delivered_units"
L_COST = "total_cost"
L_DISRUPT = "disruption"

MAXIMISE = {L_PROTECTED, L_WEIGHTED, L_DELIVERED}


@dataclass(frozen=True)
class Profile:
    name: str
    levels: tuple[str, ...]
    levers_enabled: bool
    scenario: Scenario
    gap: float | None = None  # overrides PlanConfig.solver_gap
    seconds: float | None = None  # overrides PlanConfig.solver_seconds_per_level


PROFILES: dict[str, Profile] = {
    "no_spend": Profile("no_spend", (L_PROTECTED, L_WEIGHTED, L_DELIVERED, L_COST, L_DISRUPT), False, Scenario.base),
    "balanced": Profile("balanced", (L_PROTECTED, L_WEIGHTED, L_DELIVERED, L_COST, L_DISRUPT), True, Scenario.base),
    "max_fulfilment": Profile("max_fulfilment", (L_PROTECTED, L_DELIVERED, L_WEIGHTED, L_COST, L_DISRUPT), True, Scenario.base),
    "robust": Profile("robust", (L_PROTECTED, L_WEIGHTED, L_DELIVERED, L_COST, L_DISRUPT), False, Scenario.worst, gap=0.01, seconds=4.0),
}


@dataclass
class PlanCaps:
    """Upper bounds from an already-chosen plan: used to evaluate it under another scenario."""

    starts: dict[tuple[str, str, int], int]
    levers: dict[str, int]  # lever id -> max qty (expedite) or 1 (binary levers)


def pool_id_part(p: str) -> str:
    return f"part:{p}"


def pool_id_resource(r: str) -> str:
    return f"resource:{r}"


def solve(
    plan: PlanInput,
    mat: Materialized,
    gates: dict[str, ComplianceGate],
    profile: Profile,
    *,
    caps: PlanCaps | None = None,
    relax: frozenset[str] = frozenset(),
    levels: tuple[str, ...] | None = None,
    time_limit: float | None = None,
    snapshot_hash: str = "",
) -> PlanResult:
    t0 = time.monotonic()
    cfg = plan.config
    H = mat.H
    levels = levels or profile.levels
    time_limit = time_limit or profile.seconds or cfg.solver_seconds_per_level
    gap = profile.gap if profile.gap is not None else cfg.solver_gap
    m = cp_model.CpModel()
    routes_by_sku: dict[str, list[Route]] = defaultdict(list)
    for r in sorted(plan.routes, key=lambda x: x.id):
        routes_by_sku[r.sku_id].append(r)
    levers = [lv for lv in sorted(plan.levers, key=lambda x: x.id)] if profile.levers_enabled else []

    # ---------------- lever variables
    lev_y: dict[str, cp_model.IntVar] = {}
    lev_x: dict[str, cp_model.IntVar] = {}
    for lv in levers:
        cap_y = 1
        if caps is not None:
            cap_y = 1 if caps.levers.get(lv.id, 0) > 0 else 0
        lev_y[lv.id] = m.NewIntVar(0, cap_y, f"y_{lv.id}")
        if lv.type == "expedite_supply":
            xmax = lv.qty_cap if caps is None else min(lv.qty_cap, caps.levers.get(lv.id, 0))
            lev_x[lv.id] = m.NewIntVar(0, xmax, f"x_{lv.id}")
            m.Add(lev_x[lv.id] <= lv.qty_cap * lev_y[lv.id])
    accel = {lv.target_id: lv for lv in levers if lv.type == "accelerate_qualification"}
    extend = {lv.target_id: lv for lv in levers if lv.type == "extend_window"}

    # ---------------- production variables
    s_var: dict[tuple[str, str, int], cp_model.IntVar] = {}
    g_var: dict[tuple[str, str, int], cp_model.IntVar] = {}
    arrival: dict[tuple[str, str, int], int] = {}
    part_use: dict[tuple[str, int], list] = defaultdict(list)  # (part, week) -> exprs
    res_use: dict[tuple[str, int], list] = defaultdict(list)
    alloc_vars: list[tuple[str, str, int, str, str, cp_model.IntVar]] = []
    order_g: dict[str, list] = defaultdict(list)
    order_ot: dict[str, list] = defaultdict(list)
    order_beyond: dict[str, list] = defaultdict(list)
    pen_terms, ot_terms_w, ot_terms_p, route_cost_terms, nonprimary = [], [], [], [], []
    plannable = [o for o in sorted(plan.orders, key=lambda x: x.id) if gates[o.id].plannable]

    for o in plannable:
        committed, latest = plan.wk(o.committed_week), plan.wk(o.latest_week)
        ext_lv = extend.get(o.id)
        bound = latest + (ext_lv.extend_weeks if ext_lv else 0)
        transit = plan.transit(o.destination)
        weight = cfg.priority_weights.get(o.priority, 1)
        for r in routes_by_sku.get(o.sku_id, []):
            base_from = mat.route_from.get(r.id)
            acc = accel.get(r.id)
            if base_from is None and acc is None:
                continue
            sku = plan.sku(o.sku_id)
            line_parts: list[tuple[str, int, list[str]]] = []
            usable = True
            for line in sku.bom:
                allowed = [p for p in line.allowed_parts if p in mat.part_arrivals]
                if r.part_constraints.get(line.kind) is not None:
                    allowed = [p for p in allowed if p in r.part_constraints[line.kind]]
                if not allowed:
                    usable = False
                    break
                line_parts.append((line.kind, line.qty_per_unit, allowed))
            if not usable:
                continue
            max_off = max(st.offset_weeks for st in r.steps)
            yld = mat.yield_pm[r.id]
            first = base_from if base_from is not None else max(0, plan.wk(acc.available_week))
            for w in range(max(0, first), H - max_off):
                a = w + r.lead_weeks + transit
                if a > bound:
                    continue
                key = (o.id, r.id, w)
                ub = math.ceil(o.qty * 1000 / yld)
                if caps is not None:
                    ub = min(ub, caps.starts.get(key, 0))
                if ub <= 0:
                    continue
                needs_accel = base_from is None or w < base_from
                s = m.NewIntVar(0, ub, f"s_{o.id}_{r.id}_{w}")
                g = m.NewIntVar(0, o.qty, f"g_{o.id}_{r.id}_{w}")
                m.Add(1000 * g <= yld * s)
                if needs_accel:
                    m.Add(s <= ub * lev_y[acc.id])
                s_var[key], g_var[key], arrival[key] = s, g, a
                order_g[o.id].append(g)
                if a <= committed:
                    order_ot[o.id].append(g)
                    ot_terms_w.append(weight * g)
                    if o.protected:
                        ot_terms_p.append(g)
                lateness = max(0, a - committed)
                if lateness and o.penalty_per_unit_week:
                    pen_terms.append(round(o.penalty_per_unit_week * lateness) * g)
                if a > latest:
                    order_beyond[o.id].append(g)
                if r.extra_cost_per_unit:
                    route_cost_terms.append(round(r.extra_cost_per_unit) * s)
                if not r.is_primary:
                    nonprimary.append(s)
                for st in r.steps:
                    res_use[(st.resource_id, w + st.offset_weeks)].append(st.slots_per_unit * s)
                for kind, q, allowed in line_parts:
                    if len(allowed) == 1:
                        part_use[(allowed[0], w)].append(q * s)
                        alloc_vars.append((o.id, r.id, w, kind, allowed[0], s))  # scaled later by q
                    else:
                        cs = []
                        for p in allowed:
                            c = m.NewIntVar(0, q * ub, f"c_{o.id}_{r.id}_{w}_{kind}_{p}")
                            cs.append(c)
                            part_use[(p, w)].append(c)
                            alloc_vars.append((o.id, r.id, w, kind, p, c))
                        m.Add(sum(cs) == q * s)

    # ---------------- order-level constraints
    for o in plannable:
        gs = order_g.get(o.id, [])
        if not gs:
            continue
        total = sum(gs)
        m.Add(total <= o.qty)
        if not o.allow_partial or o.min_ship_qty:
            yo = m.NewBoolVar(f"all_{o.id}")
            m.Add(total <= o.qty * yo)
            m.Add(total >= (o.qty if not o.allow_partial else o.min_ship_qty) * yo)
        ext_lv = extend.get(o.id)
        if order_beyond.get(o.id):
            if ext_lv is None:
                raise AssertionError("arrival beyond latest without extension lever")
            m.Add(sum(order_beyond[o.id]) <= o.qty * lev_y[ext_lv.id])

    # ---------------- part supply (cumulative) and resource capacity
    part_cum_used: dict[tuple[str, int], cp_model.IntVar] = {}
    parts_used = sorted({p for (p, _) in part_use})
    exp_by_part: dict[str, list[Lever]] = defaultdict(list)
    for lv in levers:
        if lv.type == "expedite_supply":
            exp_by_part[lv.target_id].append(lv)
    exp_cap = {p: sum(lv.qty_cap for lv in lvs) for p, lvs in exp_by_part.items()}
    for p in parts_used:
        cum = mat.cum_supply(p)
        prev = None
        for t in range(H):
            terms = part_use.get((p, t), [])
            cu = m.NewIntVar(0, cum[-1] + exp_cap.get(p, 0), f"cu_{p}_{t}")
            m.Add(cu == (prev if prev is not None else 0) + (sum(terms) if terms else 0))
            part_cum_used[(p, t)] = cu
            prev = cu
            if pool_id_part(p) in relax:
                continue
            extra = []
            for lv in exp_by_part.get(p, []):
                if lv.available_week is not None and plan.wk(lv.available_week) <= t:
                    extra.append(lev_x[lv.id])
            m.Add(cu <= cum[t] + (sum(extra) if extra else 0))
    spot_by_res: dict[str, list[Lever]] = defaultdict(list)
    for lv in levers:
        if lv.type == "spot_capacity":
            spot_by_res[lv.target_id].append(lv)
    res_ids = sorted({r for (r, _) in res_use})
    for rid in res_ids:
        if pool_id_resource(rid) in relax:
            continue
        for t in range(H):
            terms = res_use.get((rid, t), [])
            if not terms:
                continue
            extra = []
            for lv in spot_by_res.get(rid, []):
                w0 = plan.wk(lv.available_week) if lv.available_week else 0
                if w0 <= t < w0 + lv.capacity_weeks:
                    extra.append(lv.qty_cap * lev_y[lv.id])
            m.Add(sum(terms) <= mat.capacity.get(rid, [0] * H)[t] + (sum(extra) if extra else 0))

    # ---------------- objective expressions
    lever_cost_terms = []
    for lv in levers:
        if lv.type == "expedite_supply":
            lever_cost_terms.append(round(lv.unit_cost) * lev_x[lv.id] + round(lv.fixed_cost) * lev_y[lv.id])
        elif lv.type == "spot_capacity":
            lever_cost_terms.append((round(lv.fixed_cost) + round(lv.unit_cost) * lv.qty_cap * lv.capacity_weeks) * lev_y[lv.id])
        elif lv.type == "accelerate_qualification":
            lever_cost_terms.append(round(lv.fixed_cost) * lev_y[lv.id])
        else:
            lever_cost_terms.append(round(lv.relationship_cost + lv.fixed_cost) * lev_y[lv.id])
    expr = {
        L_PROTECTED: sum(ot_terms_p) if ot_terms_p else 0,
        L_WEIGHTED: sum(ot_terms_w) if ot_terms_w else 0,
        L_DELIVERED: sum(g for gs in order_g.values() for g in gs) if g_var else 0,
        L_COST: sum(pen_terms + route_cost_terms + lever_cost_terms) if (pen_terms or route_cost_terms or lever_cost_terms) else 0,
        L_DISRUPT: (sum(nonprimary) if nonprimary else 0) + 500 * (sum(lev_y.values()) if lev_y else 0),
    }

    if cfg.recovery_spend_cap is not None and lever_cost_terms:
        m.Add(sum(lever_cost_terms) <= int(cfg.recovery_spend_cap))

    level_results: list[LevelResult] = []
    status_name = "OPTIMAL"
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = max(1, cfg.solver_workers)
    solver.parameters.random_seed = 7
    solver.parameters.relative_gap_limit = gap
    solver.parameters.max_time_in_seconds = time_limit
    all_vars = list(s_var.values()) + list(g_var.values()) + list(lev_y.values()) + list(lev_x.values())

    if not g_var and not lev_y:
        status_name = "OPTIMAL"
        level_results = [LevelResult(name=n, sense="max" if n in MAXIMISE else "min", value=0, solver_status="OPTIMAL", gap_proven=True) for n in levels]
        solved = False
    else:
        solved = True
        for name in levels:
            e = expr[name]
            if isinstance(e, int):
                level_results.append(LevelResult(name=name, sense="max" if name in MAXIMISE else "min", value=e, solver_status="OPTIMAL", gap_proven=True))
                continue
            (m.Maximize if name in MAXIMISE else m.Minimize)(e)
            solver.parameters.relative_gap_limit = {L_PROTECTED: 0.0, L_COST: max(gap, 0.005), L_DISRUPT: max(gap, 0.05)}.get(name, gap)
            solver.parameters.max_time_in_seconds = min(time_limit, 4.0) if name in (L_COST, L_DISRUPT) else time_limit
            st = solver.Solve(m)
            if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                status_name = "ERROR"
                level_results.append(LevelResult(name=name, sense="max" if name in MAXIMISE else "min", value=0, solver_status=solver.StatusName(st), gap_proven=False))
                break
            val = int(round(solver.ObjectiveValue()))
            if st == cp_model.FEASIBLE and abs(solver.BestObjectiveBound() - val) > max(1.0, gap * abs(val)):
                status_name = "FEASIBLE"
            level_results.append(LevelResult(name=name, sense="max" if name in MAXIMISE else "min", value=val, solver_status=solver.StatusName(st),
                                             gap_proven=st == cp_model.OPTIMAL, bound=round(solver.BestObjectiveBound(), 2)))
            m.Add(e >= val) if name in MAXIMISE else m.Add(e <= val)
            m.ClearHints()
            for v in all_vars:
                m.AddHint(v, solver.Value(v))
        else:
            # final pass: no objective; recover a solution consistent with all fixed levels
            m.ClearObjective()
            st = solver.Solve(m)
            if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                status_name = "ERROR"

    starts: list[StartRecord] = []
    allocations: list[PartAllocation] = []
    lever_uses: list[LeverUse] = []
    extended: list[str] = []
    if solved and status_name != "ERROR":
        for key in sorted(s_var):
            sv = solver.Value(s_var[key])
            if sv > 0:
                starts.append(StartRecord(order_id=key[0], route_id=key[1], start_week=key[2], started=sv, good=solver.Value(g_var[key]), arrival_week=arrival[key]))
        bom = {s.id: {ln.kind: ln.qty_per_unit for ln in s.bom} for s in plan.skus}
        sku_of = {o.id: o.sku_id for o in plan.orders}
        for (oid, rid, w, kind, pid, var) in alloc_vars:
            v = solver.Value(var)
            if v <= 0:
                continue
            qty = v * bom[sku_of[oid]][kind] if var is s_var.get((oid, rid, w)) else v
            allocations.append(PartAllocation(order_id=oid, route_id=rid, start_week=w, kind=kind, part_id=pid, qty=qty))
        for lv in levers:
            if solver.Value(lev_y[lv.id]) <= 0:
                continue
            if lv.type == "expedite_supply":
                q = solver.Value(lev_x[lv.id])
                if q <= 0:
                    continue
                cost = q * lv.unit_cost + lv.fixed_cost
            elif lv.type == "spot_capacity":
                q = lv.qty_cap
                cost = lv.fixed_cost + lv.unit_cost * lv.qty_cap * lv.capacity_weeks
            elif lv.type == "accelerate_qualification":
                q = 1
                if not any(s.route_id == lv.target_id and (mat.route_from.get(lv.target_id) is None or s.start_week < mat.route_from[lv.target_id]) for s in starts):
                    continue
                cost = lv.fixed_cost
            else:
                q = 1
                if not any(s.order_id == lv.target_id and s.arrival_week > plan.wk(plan.order(lv.target_id).latest_week) for s in starts):
                    continue
                cost = lv.relationship_cost + lv.fixed_cost
                extended.append(lv.target_id)
            lever_uses.append(LeverUse(lever_id=lv.id, type=lv.type, qty=q, cost=round(cost, 2), target_id=lv.target_id,
                                       available_week=plan.wk(lv.available_week) if lv.available_week else None))

    outcomes = order_outcomes(plan, gates, starts)
    util, tight = _utilization(plan, mat, starts, allocations, lever_uses)
    res = PlanResult(
        scenario=mat.scenario, profile=profile.name, snapshot_hash=snapshot_hash, solver_status=status_name,
        levels=level_results, starts=starts, allocations=allocations, levers=lever_uses,
        extended_orders=sorted(set(extended)), orders=outcomes, totals=totals(plan, outcomes, starts, lever_uses),
        utilization=util, tight_pools=tight, solve_seconds=round(time.monotonic() - t0, 3),
        solver_params={"workers": cfg.solver_workers, "seed": 7, "time_limit_per_level": time_limit, "levels": list(levels)},
    )
    return res


def _utilization(plan, mat, starts, allocations, lever_uses):
    """Peak utilisation per pool and which pools are binding (usage == limit in a used week)."""
    routes = {r.id: r for r in plan.routes}
    res_use: dict[tuple[str, int], int] = defaultdict(int)
    for s in starts:
        for st in routes[s.route_id].steps:
            res_use[(st.resource_id, s.start_week + st.offset_weeks)] += st.slots_per_unit * s.started
    spot = defaultdict(int)
    exp = defaultdict(list)
    for lu in lever_uses:
        lv = next(x for x in plan.levers if x.id == lu.lever_id)
        if lv.type == "spot_capacity":
            w0 = plan.wk(lv.available_week) if lv.available_week else 0
            for t in range(w0, w0 + lv.capacity_weeks):
                spot[(lv.target_id, t)] += lv.qty_cap
        elif lv.type == "expedite_supply":
            exp[lv.target_id].append((plan.wk(lv.available_week) if lv.available_week else 0, lu.qty))
    util, tight = {}, []
    for rid in sorted({r for (r, _) in res_use}):
        peak, bind = 0.0, False
        for (r, t), u in res_use.items():
            if r != rid:
                continue
            limit = mat.capacity.get(rid, [0] * mat.H)[t] + spot.get((rid, t), 0)
            if limit > 0:
                peak = max(peak, u / limit)
                bind = bind or u >= limit
        util[pool_id_resource(rid)] = round(peak, 3)
        if bind:
            tight.append(pool_id_resource(rid))
    used: dict[tuple[str, int], int] = defaultdict(int)
    for a in allocations:
        used[(a.part_id, a.start_week)] += a.qty
    for pid in sorted({p for (p, _) in used}):
        cum_used, peak, bind = 0, 0.0, False
        cum = mat.cum_supply(pid)
        for t in range(mat.H):
            u = used.get((pid, t), 0)
            cum_used += u
            limit = cum[t] + sum(q for (wk, q) in exp.get(pid, []) if wk <= t)
            if limit > 0:
                peak = max(peak, cum_used / limit)
                if u > 0 and cum_used >= limit:
                    bind = True
        util[pool_id_part(pid)] = round(peak, 3)
        if bind:
            tight.append(pool_id_part(pid))
    return util, tight
