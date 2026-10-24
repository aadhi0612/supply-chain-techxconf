"""Synthetic but structurally realistic dataset + the packaging-cut / substrate-hold incident.

All names, numbers and the compliance table are ILLUSTRATIVE. They exist to exercise the
engine end to end and are not an industry forecast or legal guidance.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .domain import (
    BomLine, CapacityBucket, ComplianceRuleSet, Customer, InventoryLot, Lever, License, LogisticsLane, Order,
    Part, PlanConfig, PlanInput, Provenance, Resource, Route, RouteStep, Site, Sku, SupplyCommitment,
    YieldRange,
)
from .events import CapacityChange, LotStatusChange

PLAN_START = date(2026, 10, 26)


def wk(n: int) -> date:
    return PLAN_START + timedelta(weeks=n)


def _prov(src: str, days_ago: int = 1, conf: float = 1.0) -> Provenance:
    return Provenance(source=src, observed_at=datetime(2026, 10, 24, 6, tzinfo=timezone.utc) - timedelta(days=days_ago),
                      confidence=conf)


def build_sample() -> PlanInput:
    parts = [
        Part(id="LOGIC-N3", kind="logic_die", name="AX logic die (N3-class)", supplier_id="FoundryCo"),
        Part(id="HBM-A", kind="hbm", name="HBM3E 12-Hi (vendor A)", supplier_id="MemCo"),
        Part(id="HBM-B", kind="hbm", name="HBM3E 12-Hi (vendor B)", supplier_id="MemCo2"),
        Part(id="SUB-1", kind="substrate", name="Substrate type 1 (primary)", supplier_id="SubstrateCo"),
        Part(id="SUB-2", kind="substrate", name="Substrate type 2 (alt-route)", supplier_id="SubstrateCo2"),
    ]
    bom = [
        BomLine(kind="logic_die", qty_per_unit=1, allowed_parts=["LOGIC-N3"]),
        BomLine(kind="hbm", qty_per_unit=8, allowed_parts=["HBM-A", "HBM-B"]),
        BomLine(kind="substrate", qty_per_unit=1, allowed_parts=["SUB-1", "SUB-2"]),
    ]
    skus = [Sku(id="AX-900", name="AX-900 accelerator module", family="AX", export_class="CLASS-A", bom=bom)]
    sites = [
        Site(id="OSAT-A", name="OSAT A (Taiwan)", supplier_id="OSAT-A", country="TW"),
        Site(id="OSAT-B", name="OSAT B (Malaysia)", supplier_id="OSAT-B", country="MY"),
        Site(id="OSAT-C", name="OSAT C (US)", supplier_id="OSAT-C", country="US"),
        Site(id="TEST-1", name="Test house 1", supplier_id="TestCo", country="TW"),
    ]
    resources = [
        Resource(id="PKG-A", site_id="OSAT-A", process="2.5D-L", stage="packaging"),
        Resource(id="PKG-B", site_id="OSAT-B", process="2.5D-L", stage="packaging"),
        Resource(id="PKG-C", site_id="OSAT-C", process="2.5D-L", stage="packaging"),
        Resource(id="TEST-1", site_id="TEST-1", process="SLT", stage="test"),
    ]
    routes = [
        Route(id="R-A", sku_id="AX-900", name="OSAT-A packaging + test", lead_weeks=5, is_primary=True,
              steps=[RouteStep(resource_id="PKG-A", offset_weeks=0), RouteStep(resource_id="TEST-1", offset_weeks=3)],
              yield_pm=YieldRange(low=880, base=920, high=940), part_constraints={"substrate": ["SUB-1"]}),
        Route(id="R-B", sku_id="AX-900", name="OSAT-B alternate (qualified)", lead_weeks=6, is_primary=False,
              steps=[RouteStep(resource_id="PKG-B", offset_weeks=0), RouteStep(resource_id="TEST-1", offset_weeks=4)],
              yield_pm=YieldRange(low=830, base=880, high=910), extra_cost_per_unit=400,
              part_constraints={"substrate": ["SUB-2"]}),
        Route(id="R-C", sku_id="AX-900", name="OSAT-C US (qualification pending)", lead_weeks=6, is_primary=False,
              qualification="pending", steps=[RouteStep(resource_id="PKG-C", offset_weeks=0), RouteStep(resource_id="TEST-1", offset_weeks=4)],
              yield_pm=YieldRange(low=800, base=860, high=900), extra_cost_per_unit=900,
              part_constraints={"substrate": ["SUB-1"]}),
    ]
    cap: list[CapacityBucket] = []
    for w in range(1, 11):  # packaging A weeks 1..10
        cap.append(CapacityBucket(resource_id="PKG-A", week=wk(w), nominal=1800, reserved=1550, provenance=_prov("supplier_portal")))
    for w in range(2, 13):
        cap.append(CapacityBucket(resource_id="PKG-B", week=wk(w), nominal=400, reserved=120, provenance=_prov("supplier_portal")))
    for w in range(9, 20):
        cap.append(CapacityBucket(resource_id="PKG-C", week=wk(w), nominal=600, reserved=200, status="tentative",
                                  provenance=_prov("email", conf=0.6)))
    for w in range(3, 20):
        cap.append(CapacityBucket(resource_id="TEST-1", week=wk(w), nominal=2000, reserved=1700, provenance=_prov("erp")))
    commits = [
        SupplyCommitment(id="PO-LOGIC-1", part_id="LOGIC-N3", qty=4500, promised_week=wk(0), provenance=_prov("erp")),
        SupplyCommitment(id="PO-LOGIC-2", part_id="LOGIC-N3", qty=4500, promised_week=wk(2), provenance=_prov("erp")),
        SupplyCommitment(id="PO-LOGIC-3", part_id="LOGIC-N3", qty=4500, promised_week=wk(4), provenance=_prov("erp")),
        SupplyCommitment(id="PO-HBMA-1", part_id="HBM-A", qty=26000, promised_week=wk(0), provenance=_prov("supplier_portal")),
        SupplyCommitment(id="PO-HBMA-2", part_id="HBM-A", qty=26000, promised_week=wk(2), provenance=_prov("supplier_portal")),
        SupplyCommitment(id="PO-HBMA-3", part_id="HBM-A", qty=24000, promised_week=wk(4), status="acknowledged", confidence=0.95, provenance=_prov("supplier_portal")),
        SupplyCommitment(id="PO-HBMB-1", part_id="HBM-B", qty=24000, promised_week=wk(3), status="unconfirmed", confidence=0.6, provenance=_prov("email", 3, 0.6)),
        SupplyCommitment(id="PO-SUB1-1", part_id="SUB-1", qty=7500, promised_week=wk(1), provenance=_prov("supplier_portal")),
        SupplyCommitment(id="PO-SUB1-2", part_id="SUB-1", qty=1500, promised_week=wk(5), provenance=_prov("supplier_portal")),
    ]
    inv = [
        InventoryLot(id="LOT-S-77", part_id="SUB-1", qty=2000, status="available", available_week=wk(0), location="OSAT-A", provenance=_prov("erp")),
        InventoryLot(id="LOT-S-90", part_id="SUB-2", qty=1000, status="available", available_week=wk(0), location="OSAT-B", provenance=_prov("erp")),
        InventoryLot(id="LOT-L-12", part_id="LOGIC-N3", qty=600, status="available", available_week=wk(0), location="OSAT-A", provenance=_prov("erp")),
    ]
    customers = [
        Customer(id="HYPERION", name="Hyperion Cloud", tier=1), Customer(id="NIMBUS", name="Nimbus AI", tier=1),
        Customer(id="ORION", name="Orion Systems", tier=2), Customer(id="VEGA", name="Vega Labs", tier=3),
        Customer(id="ATLAS", name="Atlas Compute", tier=2), Customer(id="HELIX", name="Helix HPC", tier=2),
    ]
    px, pen = 30000.0, 600.0
    CUST_NAMES = {c.id: c.name for c in customers}

    def order(i, cust, qty, due, dest, prio, latest_slack=3, protected=False, partial=True, end_user="", end_use="datacenter"):
        return Order(id=f"ORD-{i}", customer_id=cust, sku_id="AX-900", qty=qty, requested_week=wk(due), committed_week=wk(due),
                     latest_week=wk(due + latest_slack), priority=prio, protected=protected, unit_price=px,
                     penalty_per_unit_week=pen, allow_partial=partial, destination=dest, end_user=end_user or CUST_NAMES[cust], end_use=end_use)

    orders = [
        order(1001, "HYPERION", 4000, 11, "US", 1, protected=True),
        order(1002, "NIMBUS", 3000, 12, "JP", 1),
        order(1003, "ORION", 2000, 13, "DE", 2),
        order(1004, "VEGA", 1000, 14, "SG", 3, partial=False),
        order(1005, "ATLAS", 1500, 12, "XX", 2),
        order(1006, "HELIX", 1200, 12, "IN", 2),
    ]
    rules = ComplianceRuleSet(
        version="demo-2026.10", effective=date(2026, 10, 1), source_ref="ILLUSTRATIVE - customer trade-compliance owner must maintain",
        country_groups={"US": "G1", "JP": "G1", "DE": "G1", "SG": "G2", "IN": "G2", "XX": "G3"}, default_group="UNKNOWN",
        decisions={"CLASS-A": {"G1": "allowed", "G2": "license_required", "G3": "prohibited"}},
        denied_parties=["Acme Shell Trading"], end_use_required_groups=["G2", "G3"], reviewed_by="compliance@example.com",
    )
    licenses = [License(id="LIC-0042", export_class="CLASS-A", destination="SG", end_user="Vega Labs", qty_limit=1000,
                        valid_from=date(2026, 6, 1), valid_to=date(2027, 5, 31))]
    levers = [
        Lever(id="LV-HBM-EXP", type="expedite_supply", target_id="HBM-A", qty_cap=16000, available_week=wk(5), unit_cost=45,
              description="Expedite 16k HBM-A stacks from MemCo", counterparty="MemCo", approver_roles=["procurement"]),
        Lever(id="LV-SUB2-EXP", type="expedite_supply", target_id="SUB-2", qty_cap=1500, available_week=wk(4), unit_cost=140,
              description="Expedite SUB-2 for the alternate route", counterparty="SubstrateCo2", approver_roles=["procurement"]),
        Lever(id="LV-PKG-SPOT", type="spot_capacity", target_id="PKG-B", qty_cap=300, capacity_weeks=5, available_week=wk(4),
              fixed_cost=150000, description="Spot packaging block at OSAT-B (+300/wk x5)", counterparty="OSAT-B", approver_roles=["procurement", "finance"]),
        Lever(id="LV-QUAL-C", type="accelerate_qualification", target_id="R-C", available_week=wk(9), fixed_cost=180000,
              description="Pull in OSAT-C route qualification to week 9", counterparty="OSAT-C", approver_roles=["quality", "finance"]),
        Lever(id="LV-WIN-1003", type="extend_window", target_id="ORD-1003", extend_weeks=2, relationship_cost=25000,
              description="Negotiate +2 week delivery window with Orion Systems", counterparty="ORION", approver_roles=["sales"]),
    ]
    return PlanInput(
        tenant_id="demo", plan_start=PLAN_START, as_of=datetime(2026, 10, 24, 7, tzinfo=timezone.utc),
        customers=customers, parts=parts, skus=skus, sites=sites, resources=resources, routes=routes, capacity=cap,
        commitments=commits, inventory=inv,
        lanes=[LogisticsLane(destination=d, transit_weeks=t) for d, t in
               [("US", 1), ("JP", 1), ("DE", 2), ("SG", 1), ("IN", 2), ("XX", 2)]],
        orders=orders, compliance=rules, licenses=licenses, levers=levers, config=PlanConfig(horizon_weeks=26),
    )


def sample_incident_events():
    """Two same-day supplier signals: a 25% packaging cut (portal) and a substrate lot hold (quality system)."""
    t = datetime(2026, 10, 24, 9, tzinfo=timezone.utc)
    return [
        CapacityChange(source="supplier_portal", observed_at=t, resource_id="PKG-A", from_week=wk(4), to_week=wk(9),
                       mode="reduce_pct", value=25, note="OSAT-A notice: reserved window reduced 25% weeks 4-9"),
        LotStatusChange(source="supplier_portal", observed_at=t + timedelta(hours=2), lot_id="LOT-S-77", status="hold",
                        note="Quality hold on substrate lot LOT-S-77 pending failure analysis"),
    ]
