"""Result types produced by the planning engine."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field

from ..domain import Model


class Scenario(str, Enum):
    best = "best"
    base = "base"
    worst = "worst"


class GateStatus(str, Enum):
    allow = "allow"
    needs_license = "needs_license"
    blocked = "blocked"
    review = "review"  # facts missing; a qualified human must decide


class ComplianceGate(Model):
    order_id: str
    status: GateStatus
    reasons: list[str] = Field(default_factory=list)
    ruleset_version: str | None = None
    license_id: str | None = None
    evidence: dict = Field(default_factory=dict)

    @property
    def plannable(self) -> bool:
        return self.status == GateStatus.allow


class StartRecord(Model):
    order_id: str
    route_id: str
    start_week: int
    started: int
    good: int
    arrival_week: int


class PartAllocation(Model):
    order_id: str
    route_id: str
    start_week: int
    kind: str
    part_id: str
    qty: int


class LeverUse(Model):
    lever_id: str
    type: str
    qty: int
    cost: float
    target_id: str
    available_week: int | None = None


class OrderOutcome(Model):
    order_id: str
    customer_id: str
    sku_id: str
    qty: int
    committed_week: int
    latest_week: int
    on_time: int
    late: int
    unfulfilled: int
    first_arrival_week: int | None
    last_arrival_week: int | None
    weeks_late_max: int
    schedule: list[tuple[int, int]] = Field(default_factory=list)  # (arrival_week, good units)
    routes: dict[str, int] = Field(default_factory=dict)  # route -> good units
    revenue_at_risk: float = 0
    penalty_cost: float = 0
    gate: GateStatus = GateStatus.allow
    status: Literal["on_time", "late", "partial", "unfulfilled", "blocked"] = "on_time"


class LevelResult(Model):
    name: str
    sense: Literal["max", "min"]
    value: int
    solver_status: str
    gap_proven: bool
    bound: float | None = None


class PlanResult(Model):
    scenario: Scenario
    profile: str
    snapshot_hash: str
    solver_status: str  # OPTIMAL | FEASIBLE | INFEASIBLE | ERROR
    levels: list[LevelResult] = Field(default_factory=list)
    starts: list[StartRecord] = Field(default_factory=list)
    allocations: list[PartAllocation] = Field(default_factory=list)
    levers: list[LeverUse] = Field(default_factory=list)
    extended_orders: list[str] = Field(default_factory=list)
    orders: list[OrderOutcome] = Field(default_factory=list)
    totals: dict[str, float] = Field(default_factory=dict)
    utilization: dict[str, float] = Field(default_factory=dict)  # pool -> peak utilisation
    tight_pools: list[str] = Field(default_factory=list)
    solve_seconds: float = 0
    solver_params: dict = Field(default_factory=dict)
    verified: bool = False
    verification_errors: list[str] = Field(default_factory=list)

    def outcome(self, order_id: str) -> OrderOutcome:
        return next(o for o in self.orders if o.order_id == order_id)
