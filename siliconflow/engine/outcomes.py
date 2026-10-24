"""Pure derivation of per-order outcomes and totals from a set of production starts.

Shared by the solver (to report) and the independent verifier (to cross-check).
"""
from __future__ import annotations

from collections import defaultdict

from ..domain import PlanInput
from .types import ComplianceGate, GateStatus, LeverUse, OrderOutcome, StartRecord


def order_outcomes(
    plan: PlanInput, gates: dict[str, ComplianceGate], starts: list[StartRecord]
) -> list[OrderOutcome]:
    by_order: dict[str, list[StartRecord]] = defaultdict(list)
    for s in starts:
        by_order[s.order_id].append(s)
    out: list[OrderOutcome] = []
    for o in sorted(plan.orders, key=lambda x: x.id):
        committed, latest = plan.wk(o.committed_week), plan.wk(o.latest_week)
        gate = gates[o.id]
        recs = by_order.get(o.id, [])
        on_time = sum(s.good for s in recs if s.arrival_week <= committed)
        late = sum(s.good for s in recs if s.arrival_week > committed)
        unfulfilled = max(0, o.qty - on_time - late)
        sched: dict[int, int] = defaultdict(int)
        routes: dict[str, int] = defaultdict(int)
        for s in recs:
            if s.good:
                sched[s.arrival_week] += s.good
                routes[s.route_id] += s.good
        arrivals = sorted(sched)
        penalty = sum(o.penalty_per_unit_week * max(0, w - committed) * q for w, q in sched.items())
        if gate.status != GateStatus.allow:
            status = "blocked"
        elif unfulfilled == 0 and late == 0:
            status = "on_time"
        elif unfulfilled == 0:
            status = "late"
        elif on_time + late == 0:
            status = "unfulfilled"
        else:
            status = "partial"
        out.append(
            OrderOutcome(
                order_id=o.id, customer_id=o.customer_id, sku_id=o.sku_id, qty=o.qty,
                committed_week=committed, latest_week=latest,
                on_time=on_time, late=late, unfulfilled=unfulfilled,
                first_arrival_week=arrivals[0] if arrivals else None,
                last_arrival_week=arrivals[-1] if arrivals else None,
                weeks_late_max=max([w - committed for w in arrivals if w > committed], default=0),
                schedule=[(w, sched[w]) for w in arrivals], routes=dict(routes),
                revenue_at_risk=round(o.unit_price * unfulfilled, 2), penalty_cost=round(penalty, 2),
                gate=gate.status, status=status,
            )
        )
    return out


def totals(
    plan: PlanInput, outcomes: list[OrderOutcome], starts: list[StartRecord], levers: list[LeverUse]
) -> dict[str, float]:
    routes = {r.id: r for r in plan.routes}
    weights = plan.config.priority_weights
    t: dict[str, float] = defaultdict(float)
    for oc in outcomes:
        o = plan.order(oc.order_id)
        t["demand_units"] += oc.qty
        t["on_time_units"] += oc.on_time
        t["late_units"] += oc.late
        t["unfulfilled_units"] += oc.unfulfilled
        t["weighted_on_time_units"] += weights.get(o.priority, 1) * oc.on_time
        if o.protected:
            t["protected_on_time_units"] += oc.on_time
        t["revenue_at_risk"] += oc.revenue_at_risk
        t["penalty_cost"] += oc.penalty_cost
    t["route_cost"] = round(sum(routes[s.route_id].extra_cost_per_unit * s.started for s in starts), 2)
    t["lever_cost"] = round(sum(lv.cost for lv in levers), 2)
    t["total_cost"] = round(t["route_cost"] + t["lever_cost"] + t["penalty_cost"], 2)
    t["exposure"] = round(t["revenue_at_risk"] + t["penalty_cost"], 2)
    t["started_units"] = sum(s.started for s in starts)
    t["delivered_units"] = t["on_time_units"] + t["late_units"]
    return {k: round(v, 2) for k, v in t.items()}
