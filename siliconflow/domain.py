"""Canonical, typed domain model.

Everything the planning engine consumes lives in :class:`PlanInput`. It is a plain,
JSON-serialisable value object so that a snapshot of it can be stored, hashed, diffed
and replayed ("what did the system believe when this plan was approved?").

Time is expressed as the Monday of an ISO week (``date``); the engine converts to integer
week indexes relative to ``PlanInput.plan_start``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


WeekDate = Annotated[date, Field(description="Any date; normalised to the Monday of its ISO week")]


def _norm_week(v):
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, str):
        v = date.fromisoformat(v[:10])
    return monday_of(v) if isinstance(v, date) else v


# --------------------------------------------------------------------------- provenance
class Provenance(Model):
    source: str = "import"  # e.g. supplier_portal | erp | aps | email | spreadsheet
    observed_at: datetime = Field(default_factory=utcnow)
    confidence: float = Field(1.0, ge=0, le=1)
    method: str = "import"  # import | api | extraction | manual
    document_id: str | None = None
    note: str | None = None

    @field_validator("observed_at")
    @classmethod
    def _tz(cls, v: datetime) -> datetime:
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- catalogue
class Customer(Model):
    id: str
    name: str
    tier: int = Field(2, ge=1, le=5, description="1 = most strategic")


class Part(Model):
    id: str
    kind: str  # logic_die | hbm | substrate | interposer | ...
    name: str = ""
    supplier_id: str | None = None


class BomLine(Model):
    kind: str
    qty_per_unit: int = Field(ge=1)
    allowed_parts: list[str] = Field(min_length=1, description="Parts qualified for this SKU")


class Sku(Model):
    id: str
    name: str = ""
    family: str = ""
    revision: str = "A"
    export_class: str = "UNCLASSIFIED"  # customer-maintained classification key (e.g. ECCN-like)
    bom: list[BomLine] = Field(min_length=1)


class Site(Model):
    id: str
    name: str = ""
    supplier_id: str | None = None
    country: str = ""


class Resource(Model):
    """A capacity-constrained process at a site (e.g. an advanced-packaging line)."""

    id: str
    site_id: str
    process: str
    stage: str  # packaging | test | assembly | ...


class YieldRange(Model):
    """Good-unit yield in permille (920 = 92.0 %)."""

    low: int = Field(ge=1, le=1000)
    base: int = Field(ge=1, le=1000)
    high: int = Field(ge=1, le=1000)

    @model_validator(mode="after")
    def _order(self):
        if not (self.low <= self.base <= self.high):
            raise ValueError("yield must satisfy low <= base <= high")
        return self


class RouteStep(Model):
    resource_id: str
    offset_weeks: int = Field(0, ge=0)
    slots_per_unit: int = Field(1, ge=1)


class Route(Model):
    id: str
    sku_id: str
    name: str = ""
    steps: list[RouteStep] = Field(min_length=1)
    lead_weeks: int = Field(ge=0, description="Weeks from start of first step until units are ship-ready")
    yield_pm: YieldRange
    extra_cost_per_unit: float = Field(0, ge=0)
    qualification: Literal["qualified", "pending", "disqualified"] = "qualified"
    qualified_from: WeekDate | None = None
    is_primary: bool = True
    part_constraints: dict[str, list[str]] = Field(
        default_factory=dict, description="kind -> parts compatible with this route (subset of BOM)"
    )

    norm_weeks = field_validator("qualified_from", mode="before")(_norm_week)


class CapacityBucket(Model):
    resource_id: str
    week: WeekDate
    nominal: int = Field(ge=0, description="Supplier nominal capacity (informational)")
    reserved: int = Field(ge=0, description="Units reserved for and usable by this tenant")
    status: Literal["confirmed", "tentative"] = "confirmed"
    provenance: Provenance = Field(default_factory=Provenance)

    norm_weeks = field_validator("week", mode="before")(_norm_week)

    @model_validator(mode="after")
    def _chk(self):
        if self.reserved > self.nominal:
            raise ValueError("reserved capacity cannot exceed nominal capacity")
        return self


CommitmentStatus = Literal["confirmed", "acknowledged", "unconfirmed", "delayed", "cancelled"]


class SupplyCommitment(Model):
    id: str
    part_id: str
    qty: int = Field(ge=0)
    promised_week: WeekDate
    status: CommitmentStatus = "confirmed"
    po_ref: str | None = None
    confidence: float = Field(1.0, ge=0, le=1)
    provenance: Provenance = Field(default_factory=Provenance)

    norm_weeks = field_validator("promised_week", mode="before")(_norm_week)


LotStatus = Literal["available", "reserved", "wip", "hold", "quarantine", "in_transit"]


class InventoryLot(Model):
    id: str
    part_id: str
    qty: int = Field(ge=0)
    status: LotStatus = "available"
    available_week: WeekDate
    release_week: WeekDate | None = None  # for holds with an expected release
    location: str = ""
    provenance: Provenance = Field(default_factory=Provenance)

    norm_weeks = field_validator("available_week", "release_week", mode="before")(_norm_week)


class LogisticsLane(Model):
    destination: str
    transit_weeks: int = Field(ge=0)


class Order(Model):
    id: str
    customer_id: str
    sku_id: str
    qty: int = Field(ge=1)
    requested_week: WeekDate
    committed_week: WeekDate
    latest_week: WeekDate = Field(description="Contractual hard limit for delivery (arrival)")
    priority: int = Field(2, ge=1, le=5)
    protected: bool = Field(False, description="Contractual allocation obligation: planned first")
    unit_price: float = Field(0, ge=0)
    penalty_per_unit_week: float = Field(0, ge=0)
    allow_partial: bool = True
    min_ship_qty: int = Field(0, ge=0)
    destination: str
    end_user: str = ""
    end_use: str = ""

    norm_weeks = field_validator("requested_week", "committed_week", "latest_week", mode="before")(_norm_week)

    @model_validator(mode="after")
    def _chk(self):
        if self.latest_week < self.committed_week:
            raise ValueError("latest_week must be >= committed_week")
        if self.min_ship_qty > self.qty:
            raise ValueError("min_ship_qty must be <= qty")
        return self


# --------------------------------------------------------------------------- compliance
class ComplianceRuleSet(Model):
    """Customer-maintained, versioned decision table. Illustrative content is NOT legal advice."""

    version: str
    effective: date
    source_ref: str = ""
    country_groups: dict[str, str] = Field(default_factory=dict)  # country -> group
    default_group: str = "UNKNOWN"
    decisions: dict[str, dict[str, Literal["allowed", "license_required", "prohibited"]]] = Field(
        default_factory=dict, description="export_class -> country_group -> decision"
    )
    denied_parties: list[str] = Field(default_factory=list)
    end_use_required_groups: list[str] = Field(default_factory=list)
    reviewed_by: str | None = None


class License(Model):
    id: str
    export_class: str
    destination: str
    end_user: str = ""
    qty_limit: int = Field(ge=0)
    valid_from: date
    valid_to: date


# --------------------------------------------------------------------------- recovery levers
LeverType = Literal["expedite_supply", "spot_capacity", "accelerate_qualification", "extend_window"]


class Lever(Model):
    id: str
    type: LeverType
    description: str = ""
    target_id: str  # part_id | resource_id | route_id | order_id
    qty_cap: int = Field(0, ge=0)  # expedite / spot capacity: max units per use
    available_week: WeekDate | None = None  # when supply/capacity/qualification would land
    capacity_weeks: int = Field(1, ge=1)  # spot_capacity: number of weeks over which cap applies each
    unit_cost: float = Field(0, ge=0)
    fixed_cost: float = Field(0, ge=0)
    extend_weeks: int = Field(0, ge=0)  # extend_window
    relationship_cost: float = Field(0, ge=0)
    approver_roles: list[str] = Field(default_factory=lambda: ["procurement"])
    counterparty: str = ""  # supplier / customer for the drafted request
    needs_external_confirmation: bool = True

    norm_weeks = field_validator("available_week", mode="before")(_norm_week)


# --------------------------------------------------------------------------- config
class ObjectiveLevel(str, Enum):
    protected_on_time = "protected_on_time_units"
    weighted_on_time = "weighted_on_time_units"
    delivered = "delivered_units"
    cost = "total_cost"
    disruption = "disruption"


class PlanConfig(Model):
    horizon_weeks: int = Field(26, ge=4, le=104)
    priority_weights: dict[int, int] = Field(default_factory=lambda: {1: 8, 2: 4, 3: 2, 4: 1, 5: 1})
    unconfirmed_haircut_base: float = Field(
        -1.0, description="If >=0, overrides per-commitment confidence for unconfirmed supply in base"
    )
    stale_after_days: int = 7
    solver_seconds_per_level: float = 10.0
    solver_gap: float = Field(0.0005, ge=0, le=0.05, description="Relative optimality gap tolerated per objective level")
    solver_workers: int = 4  # objective values are deterministic at any setting; 1 => bit-identical allocations
    spend_requires_finance_above: float = 50_000.0
    recovery_spend_cap: float | None = Field(None, ge=0, description="Hard cap on lever spend per plan")
    source_precedence: list[str] = Field(
        default_factory=lambda: ["supplier_portal", "edi", "erp", "aps", "email", "spreadsheet", "manual"]
    )


class PlanInput(Model):
    tenant_id: str = "default"
    plan_start: WeekDate
    as_of: datetime = Field(default_factory=utcnow)
    customers: list[Customer] = Field(default_factory=list)
    parts: list[Part] = Field(default_factory=list)
    skus: list[Sku] = Field(default_factory=list)
    sites: list[Site] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)
    routes: list[Route] = Field(default_factory=list)
    capacity: list[CapacityBucket] = Field(default_factory=list)
    commitments: list[SupplyCommitment] = Field(default_factory=list)
    inventory: list[InventoryLot] = Field(default_factory=list)
    lanes: list[LogisticsLane] = Field(default_factory=list)
    orders: list[Order] = Field(default_factory=list)
    compliance: ComplianceRuleSet | None = None
    licenses: list[License] = Field(default_factory=list)
    levers: list[Lever] = Field(default_factory=list)
    config: PlanConfig = Field(default_factory=PlanConfig)

    norm_weeks = field_validator("plan_start", mode="before")(_norm_week)

    # ---- helpers
    def wk(self, d: date) -> int:
        return (monday_of(d) - self.plan_start).days // 7

    def date_of(self, week: int) -> date:
        return self.plan_start + timedelta(weeks=week)

    def transit(self, destination: str) -> int:
        for lane in self.lanes:
            if lane.destination == destination:
                return lane.transit_weeks
        raise KeyError(destination)

    def sku(self, sku_id: str) -> Sku:
        return next(s for s in self.skus if s.id == sku_id)

    def order(self, order_id: str) -> Order:
        return next(o for o in self.orders if o.id == order_id)

    def content_hash(self) -> str:
        """Stable hash of everything that can influence a plan (excludes ``as_of``)."""
        payload = self.model_dump(mode="json", exclude={"as_of"})
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def copy_deep(self) -> "PlanInput":
        return PlanInput.model_validate(self.model_dump(mode="json"))
