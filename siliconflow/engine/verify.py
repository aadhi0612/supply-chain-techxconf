"""Independent plan verifier.

Re-derives every hard constraint from the raw PlanInput and the *result* only, without
touching the CP-SAT model. A plan that fails here is never shown as an option.
"""
from __future__ import annotations

from collections import defaultdict

from ..domain import PlanInput
from .outcomes import order_outcomes
from .scenarios import Materialized
from .types import ComplianceGate, GateStatus, PlanResult


def verify(plan: PlanInput, mat: Materialized, gates: dict[str, ComplianceGate], res: PlanResult) -> list[str]:
    errs: list[str] = []
    routes = {r.id: r for r in plan.routes}
    orders = {o.id: o for o in plan.orders}
    lev = {lv.id: lv for lv in plan.levers}
    used_lev = {lu.lever_id: lu for lu in res.levers}

    alloc_by_start: dict[tuple[str, str, int], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    part_week: dict[tuple[str, int], int] = defaultdict(int)
    for a in res.allocations:
        alloc_by_start[(a.order_id, a.route_id, a.start_week)][a.kind] += a.qty
        part_week[(a.part_id, a.start_week)] += a.qty
        o = orders[a.order_id]
        line = next((ln for ln in plan.sku(o.sku_id).bom if ln.kind == a.kind), None)
        r = routes[a.route_id]
        if line is None or a.part_id not in line.allowed_parts:
            errs.append(f"{a.order_id}: part {a.part_id} not qualified for BOM line {a.kind}")
        elif r.part_constraints.get(a.kind) is not None and a.part_id not in r.part_constraints[a.kind]:
            errs.append(f"{a.order_id}: part {a.part_id} incompatible with route {r.id}")

    res_week: dict[tuple[str, int], int] = defaultdict(int)
    for s in res.starts:
        o, r = orders.get(s.order_id), routes.get(s.route_id)
        if o is None or r is None:
            errs.append(f"unknown order/route in start {s}")
            continue
        if gates[o.id].status != GateStatus.allow:
            errs.append(f"{o.id}: planned despite compliance gate {gates[o.id].status.value}")
        if r.sku_id != o.sku_id:
            errs.append(f"{o.id}: route {r.id} not qualified for SKU {o.sku_id}")
        first = mat.route_from.get(r.id)
        accel = [lu for lu in res.levers if lu.type == "accelerate_qualification" and lu.target_id == r.id]
        if (first is None or s.start_week < first) and not accel:
            errs.append(f"{o.id}: route {r.id} used at week {s.start_week} before qualification")
        if accel and (first is None or s.start_week < first):
            if lev[accel[0].lever_id].available_week and s.start_week < plan.wk(lev[accel[0].lever_id].available_week):
                errs.append(f"{o.id}: accelerated route {r.id} used before lever availability")
        if s.good * 1000 > mat.yield_pm[r.id] * s.started:
            errs.append(f"{o.id}: good units exceed yield at week {s.start_week}")
        exp_arrival = s.start_week + r.lead_weeks + plan.transit(o.destination)
        if s.arrival_week != exp_arrival:
            errs.append(f"{o.id}: arrival week mismatch ({s.arrival_week} != {exp_arrival})")
        limit = plan.wk(o.latest_week) + (lev[f].extend_weeks if (f := next((lu.lever_id for lu in res.levers if lu.type == "extend_window" and lu.target_id == o.id), None)) else 0)
        if s.arrival_week > limit:
            errs.append(f"{o.id}: arrival week {s.arrival_week} beyond contractual limit {limit}")
        for line in plan.sku(o.sku_id).bom:
            got = alloc_by_start[(o.id, r.id, s.start_week)].get(line.kind, 0)
            if got != line.qty_per_unit * s.started:
                errs.append(f"{o.id}: BOM line {line.kind} allocated {got} != {line.qty_per_unit * s.started}")
        for st in r.steps:
            res_week[(st.resource_id, s.start_week + st.offset_weeks)] += st.slots_per_unit * s.started

    # capacity
    for (rid, t), u in res_week.items():
        spot = sum(
            lev[lu.lever_id].qty_cap
            for lu in res.levers
            if lu.type == "spot_capacity" and lu.target_id == rid
            and (plan.wk(lev[lu.lever_id].available_week) if lev[lu.lever_id].available_week else 0) <= t
            < (plan.wk(lev[lu.lever_id].available_week) if lev[lu.lever_id].available_week else 0) + lev[lu.lever_id].capacity_weeks
        )
        cap = (mat.capacity.get(rid, [0] * mat.H)[t] if 0 <= t < mat.H else 0) + spot
        if u > cap:
            errs.append(f"resource {rid} week {t}: use {u} > capacity {cap}")
    # parts (cumulative)
    for pid in sorted({p for (p, _) in part_week}):
        cum_used = 0
        cum = mat.cum_supply(pid)
        for t in range(mat.H):
            cum_used += part_week.get((pid, t), 0)
            exp = sum(
                used_lev[lid].qty
                for lid in used_lev
                if lev[lid].type == "expedite_supply" and lev[lid].target_id == pid
                and lev[lid].available_week and plan.wk(lev[lid].available_week) <= t
            )
            if cum_used > cum[t] + exp:
                errs.append(f"part {pid} week {t}: cumulative use {cum_used} > supply {cum[t] + exp}")
                break
    # order-level rules
    fresh = {o.order_id: o for o in order_outcomes(plan, gates, res.starts)}
    for oc in res.orders:
        o = orders[oc.order_id]
        if oc.on_time + oc.late > o.qty:
            errs.append(f"{o.id}: delivered more than ordered")
        d = oc.on_time + oc.late
        if not o.allow_partial and d not in (0, o.qty):
            errs.append(f"{o.id}: partial delivery {d} on all-or-nothing order")
        if o.min_ship_qty and 0 < d < o.min_ship_qty:
            errs.append(f"{o.id}: delivery {d} below minimum ship quantity")
        f = fresh[o.id]
        if (f.on_time, f.late) != (oc.on_time, oc.late):
            errs.append(f"{o.id}: reported outcome differs from recomputation")
    return errs
