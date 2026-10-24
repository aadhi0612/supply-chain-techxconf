"""Why is an order short? Two complementary, fully deterministic answers.

1. Stage ceilings: the nominal 'min of stages' view for one order in isolation (useful, but ignores
   competition between orders and so over-states what is achievable).
2. Binding-constraint probes: re-solve with one binding pool relaxed at a time; the units recovered are
   the pool's marginal value. No single pool recovering units is reported as a *joint* bottleneck.
"""
from __future__ import annotations

from pydantic import Field

from ..domain import Model, PlanInput
from .optimizer import PROFILES, pool_id_part, pool_id_resource
from .planner import run_plan
from .scenarios import materialize
from .types import ComplianceGate, PlanResult, Scenario

PROBE_LEVELS = ("protected_on_time_units", "weighted_on_time_units")


class StageCeiling(Model):
    stage: str
    pool: str
    label: str
    units: int


class PoolValue(Model):
    pool: str
    label: str
    recovered_on_time_units: int
    recovered_delivered_units: int
    per_order_delivered: dict[str, int] = Field(default_factory=dict)


class BindingReport(Model):
    pools: list[PoolValue] = Field(default_factory=list)
    joint_recovered_delivered: int = 0
    joint_recovered_on_time: int = 0
    by_order: dict[str, list[str]] = Field(default_factory=dict)  # order -> pools that recover its units
    joint_only_orders: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


def pool_label(plan: PlanInput, pool: str) -> str:
    kind, ident = pool.split(":", 1)
    if kind == "part":
        p = next((x for x in plan.parts if x.id == ident), None)
        return f"{p.kind.upper()} supply - {p.name or p.id}" if p else pool
    r = next((x for x in plan.resources if x.id == ident), None)
    if r:
        site = next((s for s in plan.sites if s.id == r.site_id), None)
        return f"{r.stage.capitalize()} capacity - {ident} ({site.name if site else r.site_id}, {r.process})"
    return pool


def stage_ceilings(plan: PlanInput, order_id: str, scenario: Scenario = Scenario.base) -> list[StageCeiling]:
    o = plan.order(order_id)
    mat = materialize(plan, scenario)
    routes = [r for r in plan.routes if r.sku_id == o.sku_id and mat.route_from.get(r.id) is not None]
    if not routes:
        return []
    r = sorted(routes, key=lambda x: (not x.is_primary, x.id))[0]
    last_start = plan.wk(o.committed_week) - plan.transit(o.destination) - r.lead_weeks
    y = mat.yield_pm[r.id] / 1000
    out: list[StageCeiling] = []
    if last_start < 0:
        return [StageCeiling(stage="timing", pool="timing", label="Committed date is earlier than the route's lead time", units=0)]
    for line in plan.sku(o.sku_id).bom:
        allowed = r.part_constraints.get(line.kind, line.allowed_parts)
        cum = sum(mat.cum_supply(p)[min(last_start, mat.H - 1)] for p in allowed if p in mat.part_arrivals)
        out.append(StageCeiling(stage=line.kind, pool="+".join(pool_id_part(p) for p in allowed),
                                label=f"{line.kind} supply by start week {last_start}", units=int(cum / line.qty_per_unit * y)))
    for st in r.steps:
        cap = sum(mat.capacity[st.resource_id][t] for t in range(0, min(mat.H, last_start + st.offset_weeks + 1)))
        out.append(StageCeiling(stage=st.resource_id, pool=pool_id_resource(st.resource_id),
                                label=pool_label(plan, pool_id_resource(st.resource_id)), units=int(cap / st.slots_per_unit * y)))
    return sorted(out, key=lambda c: c.units)


def analyze_binding(
    plan: PlanInput, res: PlanResult, profile: str, gates: dict[str, ComplianceGate], max_pools: int = 6
) -> BindingReport:
    rep = BindingReport()
    prof = PROFILES[profile]
    base_total = {oc.order_id: oc.on_time + oc.late for oc in res.orders}
    base_ot_by = {oc.order_id: oc.on_time for oc in res.orders}
    base_ot = res.totals.get("on_time_units", 0)
    base_del = res.totals.get("delivered_units", 0)
    pools = res.tight_pools[:max_pools]
    rep.skipped = res.tight_pools[max_pools:]
    for pool in pools:
        r = run_plan(plan, prof, res.scenario, gates=gates, relax=frozenset([pool]), levels=PROBE_LEVELS, check=False, time_limit=3)
        per = {oc.order_id: max((oc.on_time + oc.late) - base_total[oc.order_id], oc.on_time - base_ot_by[oc.order_id]) for oc in r.orders}
        per = {k: v for k, v in per.items() if v > 10}
        rec_del = int(r.totals.get("delivered_units", 0) - base_del)
        rec_ot = int(r.totals.get("on_time_units", 0) - base_ot)
        noise = max(10, int(0.0025 * max(base_ot, 1)))  # below this is within solver tolerance
        if rec_del > noise or rec_ot > noise:
            rep.pools.append(PoolValue(pool=pool, label=pool_label(plan, pool), recovered_on_time_units=max(0, rec_ot),
                                       recovered_delivered_units=max(0, rec_del), per_order_delivered=per))
    if len(pools) > 1:
        r = run_plan(plan, prof, res.scenario, gates=gates, relax=frozenset(pools), levels=PROBE_LEVELS, check=False, time_limit=3)
        rep.joint_recovered_delivered = int(r.totals.get("delivered_units", 0) - base_del)
        rep.joint_recovered_on_time = int(r.totals.get("on_time_units", 0) - base_ot)
        joint_per = {oc.order_id: max((oc.on_time + oc.late) - base_total[oc.order_id], oc.on_time - base_ot_by[oc.order_id]) for oc in r.orders}
    else:
        joint_per = {}
    rep.pools.sort(key=lambda p: (-p.recovered_delivered_units, -p.recovered_on_time_units, p.pool))
    for pv in rep.pools:
        for oid in pv.per_order_delivered:
            rep.by_order.setdefault(oid, []).append(pv.pool)
    rep.joint_only_orders = sorted(o for o, v in joint_per.items() if v > 10 and o not in rep.by_order)
    return rep


def explain_order(plan: PlanInput, oc, gate: ComplianceGate, binding: BindingReport) -> str:
    """Deterministic sentence(s) for one order. Every number comes from solver output."""
    if gate.status.value != "allow":
        return f"{oc.order_id} is not planned: compliance gate '{gate.status.value}' - {'; '.join(gate.reasons)}"
    if oc.status == "on_time":
        return f"{oc.order_id}: {oc.qty} units arrive by the committed week."
    bits = [f"{oc.order_id}: {oc.on_time} on time, {oc.late} late (up to {oc.weeks_late_max} wk), {oc.unfulfilled} unfulfilled of {oc.qty}."]
    pools = binding.by_order.get(oc.order_id)
    if pools:
        labels = [pool_label(plan, p) for p in pools[:2]]
        bits.append("Binding: " + " and ".join(labels) + ".")
    elif oc.order_id in binding.joint_only_orders:
        bits.append("Joint bottleneck: no single pool recovers these units; relaxing several together would.")
    elif oc.unfulfilled or oc.late:
        bits.append("Not recoverable by relaxing any tight supply/capacity pool: limited by contract window, timing or yield.")
    return " ".join(bits)
