from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from siliconflow.domain import (
    BomLine, CapacityBucket, ComplianceRuleSet, Customer, LogisticsLane, Order, Part, PlanConfig, PlanInput,
    Provenance, Resource, Route, RouteStep, Site, Sku, SupplyCommitment, YieldRange,
)
from siliconflow.sample_data import build_sample, wk
from siliconflow.services.actions import OutboxAdapter
from siliconflow.services.platform import Platform
from siliconflow.services.policy import ExecutionPolicy


@pytest.fixture(scope="session")
def sample() -> PlanInput:
    return build_sample()


def mini(*, cap=100, weeks=2, yield_base=900, part_qty=10_000, orders=None, levers=None, rules=True, **cfg) -> PlanInput:
    """Smallest interesting instance: 1 part, 1 packaging resource, 1 route, hand-computable."""
    start = date(2026, 10, 26)
    prov = Provenance(source="erp", observed_at=datetime(2026, 10, 24, tzinfo=timezone.utc))
    sku = Sku(id="S", export_class="C", bom=[BomLine(kind="die", qty_per_unit=1, allowed_parts=["P"])])
    orders = orders or [Order(id="O1", customer_id="C1", sku_id="S", qty=1000, requested_week=start, committed_week=date(2026, 12, 7),
                              latest_week=date(2026, 12, 28), destination="US", unit_price=100, penalty_per_unit_week=1)]
    return PlanInput(
        tenant_id="t", plan_start=start, as_of=datetime(2026, 10, 24, 7, tzinfo=timezone.utc),
        customers=[Customer(id="C1", name="C1"), Customer(id="C2", name="C2")], parts=[Part(id="P", kind="die")], skus=[sku],
        sites=[Site(id="X")], resources=[Resource(id="R", site_id="X", process="pkg", stage="packaging")],
        routes=[Route(id="RT", sku_id="S", steps=[RouteStep(resource_id="R")], lead_weeks=1, yield_pm=YieldRange(low=yield_base, base=yield_base, high=yield_base))],
        capacity=[CapacityBucket(resource_id="R", week=wk(w), nominal=cap, reserved=cap, provenance=prov) for w in range(1, 1 + weeks)],
        commitments=[SupplyCommitment(id="PO", part_id="P", qty=part_qty, promised_week=wk(0), provenance=prov)],
        lanes=[LogisticsLane(destination="US", transit_weeks=1)], orders=orders, levers=levers or [],
        compliance=ComplianceRuleSet(version="v1", effective=date(2026, 1, 1), country_groups={"US": "G1"},
                                     decisions={"C": {"G1": "allowed"}}) if rules else None,
        config=PlanConfig(horizon_weeks=16, solver_seconds_per_level=5, **cfg),
    )


@pytest.fixture
def make_mini():
    return mini


@pytest.fixture
def platform(tmp_path):
    pf = Platform("sqlite://", ExecutionPolicy(mode="execute"), OutboxAdapter(tmp_path / "outbox"), sleep=lambda _: None,
                  analysis_profiles=["no_spend", "balanced"], analysis_binding=False)
    yield pf
    pf.close()
