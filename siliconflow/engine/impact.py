"""Order-level impact of a change: plan-before vs plan-after, tagged direct / indirect."""
from __future__ import annotations

from pydantic import Field

from ..domain import Model, PlanInput
from .graph import DependencyGraph, subject_nodes
from .types import PlanResult


class OrderImpact(Model):
    order_id: str
    customer_id: str
    dependency: str  # direct | indirect | none
    dependency_path: list[str] = Field(default_factory=list)
    on_time_before: int
    on_time_after: int
    delivered_before: int
    delivered_after: int
    unfulfilled_before: int
    unfulfilled_after: int
    last_arrival_before: int | None
    last_arrival_after: int | None
    exposure_delta: float
    status_before: str
    status_after: str

    @property
    def on_time_lost(self) -> int:
        return max(0, self.on_time_before - self.on_time_after)

    @property
    def degraded(self) -> bool:
        """Worse for the customer: fewer on-time/delivered units or a later final arrival."""
        later = (self.last_arrival_after or 0) > (self.last_arrival_before or 0) and self.delivered_after > 0
        return self.on_time_after < self.on_time_before or self.delivered_after < self.delivered_before or later


class ImpactReport(Model):
    orders: list[OrderImpact]
    orders_affected: int
    units_on_time_lost: int
    units_delivery_lost: int
    exposure_delta: float
    material: bool  # at least one order is worse off


def compute_impact(
    plan_after: PlanInput, subjects: list[tuple[str, str]], before: PlanResult, after: PlanResult
) -> ImpactReport:
    graph = DependencyGraph(plan_after)
    direct = graph.downstream_orders(subject_nodes(subjects))
    rows: list[OrderImpact] = []
    for ob in before.orders:
        oa = next((x for x in after.orders if x.order_id == ob.order_id), None)
        if oa is None:  # order cancelled by the event
            continue
        o = plan_after.order(ob.order_id)
        ex_b = ob.revenue_at_risk + ob.penalty_cost
        ex_a = oa.revenue_at_risk + oa.penalty_cost
        changed = (ob.on_time, ob.late, ob.last_arrival_week) != (oa.on_time, oa.late, oa.last_arrival_week)
        dep = "direct" if ob.order_id in direct else ("indirect" if changed else "none")
        rows.append(OrderImpact(
            order_id=ob.order_id, customer_id=o.customer_id, dependency=dep, dependency_path=direct.get(ob.order_id, []),
            on_time_before=ob.on_time, on_time_after=oa.on_time,
            delivered_before=ob.on_time + ob.late, delivered_after=oa.on_time + oa.late,
            unfulfilled_before=ob.unfulfilled, unfulfilled_after=oa.unfulfilled,
            last_arrival_before=ob.last_arrival_week, last_arrival_after=oa.last_arrival_week,
            exposure_delta=round(ex_a - ex_b, 2), status_before=ob.status, status_after=oa.status,
        ))
    affected = [r for r in rows if r.degraded]
    return ImpactReport(
        orders=rows, orders_affected=len(affected),
        units_on_time_lost=sum(r.on_time_lost for r in rows),
        units_delivery_lost=sum(max(0, r.delivered_before - r.delivered_after) for r in rows),
        exposure_delta=round(sum(r.exposure_delta for r in rows), 2), material=bool(affected),
    )
