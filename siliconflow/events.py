"""Supply-network events and the reconciliation rules that apply them to a PlanInput.

Reconciliation principle (asymmetric trust): a less authoritative source may *worsen* the
plan immediately (conservative), but may not *improve* it until a more authoritative source
or a human confirms. Older observations never overwrite newer ones. Every divergence is kept
as a Conflict so nothing is silently merged.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal, Union

from pydantic import Field, field_validator, model_validator

from .domain import (
    CapacityBucket, ComplianceRuleSet, License, LotStatus, Model, PlanInput, Provenance, SupplyCommitment,
    CommitmentStatus, monday_of, utcnow, _norm_week,
)


class EventBase(Model):
    id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    source: str
    observed_at: datetime = Field(default_factory=utcnow)
    confidence: float = Field(1.0, ge=0, le=1)
    method: str = "api"
    document_id: str | None = None
    note: str | None = None
    force: bool = False  # human override of precedence (resolution flow only)
    supersedes: str | None = None  # id of an earlier event this one corrects; `note` must then say why

    @model_validator(mode="after")
    def _correction_needs_reason(self):
        if self.supersedes and not (self.note or "").strip():
            raise ValueError("an event that supersedes another must explain why in `note`")
        return self

    @field_validator("observed_at")
    @classmethod
    def _tz(cls, v):
        from datetime import timezone
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)

    def prov(self) -> Provenance:
        return Provenance(source=self.source, observed_at=self.observed_at, confidence=self.confidence,
                          method=self.method, document_id=self.document_id, note=self.note)


class CapacityChange(EventBase):
    type: Literal["capacity_change"] = "capacity_change"
    resource_id: str
    from_week: date
    to_week: date
    mode: Literal["set", "reduce_pct", "add"]
    value: int = Field(ge=0)

    norm_weeks = field_validator("from_week", "to_week", mode="before")(_norm_week)


class CommitmentChange(EventBase):
    type: Literal["commitment_change"] = "commitment_change"
    commitment_id: str
    qty: int | None = Field(None, ge=0)
    promised_week: date | None = None
    status: CommitmentStatus | None = None

    norm_weeks = field_validator("promised_week", mode="before")(_norm_week)


class CommitmentAdd(EventBase):
    type: Literal["commitment_add"] = "commitment_add"
    commitment: SupplyCommitment


class LotStatusChange(EventBase):
    type: Literal["lot_status"] = "lot_status"
    lot_id: str
    status: LotStatus
    release_week: date | None = None

    norm_weeks = field_validator("release_week", mode="before")(_norm_week)


class YieldChange(EventBase):
    type: Literal["yield_change"] = "yield_change"
    route_id: str
    base: int = Field(ge=1, le=1000)
    low: int | None = Field(None, ge=1, le=1000)
    high: int | None = Field(None, ge=1, le=1000)


class LogisticsDelay(EventBase):
    type: Literal["logistics_delay"] = "logistics_delay"
    destination: str
    delta_weeks: int = Field(ge=-8, le=26)


class DemandChange(EventBase):
    type: Literal["demand_change"] = "demand_change"
    order_id: str
    qty: int | None = Field(None, ge=1)
    committed_week: date | None = None
    latest_week: date | None = None
    cancel: bool = False

    norm_weeks = field_validator("committed_week", "latest_week", mode="before")(_norm_week)


class ComplianceRulesChange(EventBase):
    type: Literal["compliance_rules"] = "compliance_rules"
    ruleset: ComplianceRuleSet


class LicenseAdd(EventBase):
    type: Literal["license_add"] = "license_add"
    license: License


class RouteQualification(EventBase):
    type: Literal["route_qualification"] = "route_qualification"
    route_id: str
    qualification: Literal["qualified", "pending", "disqualified"]
    qualified_from: date | None = None

    norm_weeks = field_validator("qualified_from", mode="before")(_norm_week)


SupplyEvent = Annotated[
    Union[CapacityChange, CommitmentChange, CommitmentAdd, LotStatusChange, YieldChange, LogisticsDelay,
          DemandChange, ComplianceRulesChange, LicenseAdd, RouteQualification],
    Field(discriminator="type"),
]


class FactChange(Model):
    path: str
    old: object = None
    new: object = None


class ConflictDraft(Model):
    entity_type: str
    entity_id: str
    field: str
    current_value: object
    current_source: str
    proposed_value: object
    proposed_source: str
    applied: bool  # True: proposed (worse) value applied conservatively; False: current kept
    event_id: str
    reason: str


class ApplyResult(Model):
    changes: list[FactChange] = Field(default_factory=list)
    conflicts: list[ConflictDraft] = Field(default_factory=list)
    ignored: list[str] = Field(default_factory=list)
    subjects: list[tuple[str, str]] = Field(default_factory=list)  # (entity_type, id) touched

    @property
    def changed(self) -> bool:
        return bool(self.changes)


class EventError(ValueError):
    pass


def rank(source: str, precedence: list[str]) -> int:
    return len(precedence) - precedence.index(source) if source in precedence else 0


def _verdict(existing: Provenance, ev: EventBase, precedence: list[str]) -> str:
    if ev.force:
        return "apply"
    if ev.observed_at < existing.observed_at:
        return "stale"
    return "apply" if rank(ev.source, precedence) >= rank(existing.source, precedence) else "lower"


def apply_event(plan: PlanInput, ev: EventBase) -> tuple[PlanInput, ApplyResult]:
    """Return a *new* PlanInput with `ev` applied. The input is never mutated."""
    new = plan.copy_deep()
    res = ApplyResult()
    prec = new.config.source_precedence
    handler = _HANDLERS[ev.type]
    handler(new, ev, res, prec)
    if res.changed:
        new.as_of = max(new.as_of, ev.observed_at)
    return new, res


# ------------------------------------------------------------------------------ handlers
def _capacity(new: PlanInput, ev: CapacityChange, res: ApplyResult, prec):
    if ev.resource_id not in {r.id for r in new.resources}:
        raise EventError(f"unknown resource {ev.resource_id}")
    if ev.to_week < ev.from_week:
        raise EventError("to_week before from_week")
    w = ev.from_week
    existing = {(b.resource_id, b.week): b for b in new.capacity}
    while w <= ev.to_week:
        b = existing.get((ev.resource_id, w))
        path = f"capacity[{ev.resource_id}@{w}].reserved"
        if b is None:
            if ev.mode in ("set", "add"):
                nb = CapacityBucket(resource_id=ev.resource_id, week=w, nominal=ev.value, reserved=ev.value,
                                    status="confirmed", provenance=ev.prov())
                new.capacity.append(nb)
                res.changes.append(FactChange(path=path, old=None, new=ev.value))
                res.subjects.append(("resource", ev.resource_id))
            w = monday_of(date.fromordinal(w.toordinal() + 7))
            continue
        old = b.reserved
        target = {"set": ev.value, "add": old + ev.value, "reduce_pct": old * (100 - min(ev.value, 100)) // 100}[ev.mode]
        target = max(0, target)
        if target == old:
            res.ignored.append(f"{path}: no change")
        else:
            v = _verdict(b.provenance, ev, prec)
            worse = target < old
            if v == "stale":
                res.ignored.append(f"{path}: stale observation ignored")
            elif v == "lower" and not worse:
                res.conflicts.append(ConflictDraft(
                    entity_type="capacity", entity_id=f"{ev.resource_id}@{w}", field="reserved",
                    current_value=old, current_source=b.provenance.source, proposed_value=target,
                    proposed_source=ev.source, applied=False, event_id=ev.id,
                    reason="less authoritative source claims better capacity; not applied until confirmed"))
            else:
                if v == "lower":
                    res.conflicts.append(ConflictDraft(
                        entity_type="capacity", entity_id=f"{ev.resource_id}@{w}", field="reserved",
                        current_value=old, current_source=b.provenance.source, proposed_value=target,
                        proposed_source=ev.source, applied=True, event_id=ev.id,
                        reason="less authoritative source reports a reduction; applied conservatively pending confirmation"))
                b.nominal = max(b.nominal, target)  # widen first: nominal >= reserved is validated on assignment
                b.reserved = target
                b.provenance = ev.prov()
                res.changes.append(FactChange(path=path, old=old, new=target))
                res.subjects.append(("resource", ev.resource_id))
        w = monday_of(date.fromordinal(w.toordinal() + 7))


def _commitment(new: PlanInput, ev: CommitmentChange, res: ApplyResult, prec):
    c = next((x for x in new.commitments if x.id == ev.commitment_id), None)
    if c is None:
        raise EventError(f"unknown commitment {ev.commitment_id}")
    v = _verdict(c.provenance, ev, prec)
    if v == "stale":
        res.ignored.append(f"commitment[{c.id}]: stale observation ignored")
        return
    status_order = {"confirmed": 0, "acknowledged": 1, "unconfirmed": 2, "delayed": 3, "cancelled": 4}
    proposals: list[tuple[str, object, object, bool]] = []  # field, old, new, worse
    if ev.qty is not None and ev.qty != c.qty:
        proposals.append(("qty", c.qty, ev.qty, ev.qty < c.qty))
    if ev.promised_week is not None and ev.promised_week != c.promised_week:
        proposals.append(("promised_week", c.promised_week, ev.promised_week, ev.promised_week > c.promised_week))
    if ev.status is not None and ev.status != c.status:
        proposals.append(("status", c.status, ev.status, status_order[ev.status] > status_order[c.status]))
    if not proposals:
        res.ignored.append(f"commitment[{c.id}]: no change")
        # corroboration of the same value by any source refreshes confirmation freshness
        return
    applied_any = False
    for field, old, nv, worse in proposals:
        path = f"commitment[{c.id}].{field}"
        if v == "lower" and not worse:
            res.conflicts.append(ConflictDraft(
                entity_type="commitment", entity_id=c.id, field=field, current_value=_j(old), current_source=c.provenance.source,
                proposed_value=_j(nv), proposed_source=ev.source, applied=False, event_id=ev.id,
                reason="less authoritative source claims better supply; not applied until confirmed"))
            continue
        if v == "lower":
            res.conflicts.append(ConflictDraft(
                entity_type="commitment", entity_id=c.id, field=field, current_value=_j(old), current_source=c.provenance.source,
                proposed_value=_j(nv), proposed_source=ev.source, applied=True, event_id=ev.id,
                reason="less authoritative source reports worse supply; applied conservatively pending confirmation"))
        setattr(c, field, nv)
        res.changes.append(FactChange(path=path, old=_j(old), new=_j(nv)))
        applied_any = True
    if applied_any:
        c.provenance = ev.prov()
        c.confidence = ev.confidence if ev.status in ("unconfirmed", "acknowledged") else c.confidence
        res.subjects.append(("part", c.part_id))


def _commitment_add(new: PlanInput, ev: CommitmentAdd, res: ApplyResult, prec):
    if any(c.id == ev.commitment.id for c in new.commitments):
        raise EventError(f"commitment {ev.commitment.id} already exists")
    if ev.commitment.part_id not in {p.id for p in new.parts}:
        raise EventError(f"unknown part {ev.commitment.part_id}")
    c = ev.commitment.model_copy(update={"provenance": ev.prov()})
    new.commitments.append(c)
    res.changes.append(FactChange(path=f"commitment[{c.id}]", old=None, new=c.model_dump(mode="json", exclude={"provenance"})))
    res.subjects.append(("part", c.part_id))


def _lot(new: PlanInput, ev: LotStatusChange, res: ApplyResult, prec):
    lot = next((x for x in new.inventory if x.id == ev.lot_id), None)
    if lot is None:
        raise EventError(f"unknown lot {ev.lot_id}")
    v = _verdict(lot.provenance, ev, prec)
    if v == "stale":
        res.ignored.append(f"lot[{lot.id}]: stale observation ignored")
        return
    restricting = {"hold", "quarantine", "reserved", "wip"}
    worse = ev.status in restricting and lot.status not in restricting
    same = ev.status == lot.status and ev.release_week == lot.release_week
    if same:
        res.ignored.append(f"lot[{lot.id}]: no change")
        return
    if v == "lower" and not worse and lot.status in restricting:
        res.conflicts.append(ConflictDraft(
            entity_type="lot", entity_id=lot.id, field="status", current_value=lot.status, current_source=lot.provenance.source,
            proposed_value=ev.status, proposed_source=ev.source, applied=False, event_id=ev.id,
            reason="a hold can only be released by an equal or more authoritative source"))
        return
    if v == "lower":
        res.conflicts.append(ConflictDraft(
            entity_type="lot", entity_id=lot.id, field="status", current_value=lot.status, current_source=lot.provenance.source,
            proposed_value=ev.status, proposed_source=ev.source, applied=True, event_id=ev.id,
            reason="hold reported by less authoritative source; applied conservatively"))
    old = lot.status
    lot.status, lot.release_week, lot.provenance = ev.status, ev.release_week, ev.prov()
    res.changes.append(FactChange(path=f"lot[{lot.id}].status", old=old, new=ev.status))
    res.subjects.append(("part", lot.part_id))


def _yield(new: PlanInput, ev: YieldChange, res: ApplyResult, prec):
    r = next((x for x in new.routes if x.id == ev.route_id), None)
    if r is None:
        raise EventError(f"unknown route {ev.route_id}")
    low = ev.low if ev.low is not None else min(r.yield_pm.low, ev.base)
    high = ev.high if ev.high is not None else max(r.yield_pm.high, ev.base)
    old = r.yield_pm.model_dump()
    if old == {"low": low, "base": ev.base, "high": high}:
        res.ignored.append(f"route[{r.id}].yield: no change")
        return
    from .domain import YieldRange
    try:
        r.yield_pm = YieldRange(low=min(low, ev.base), base=ev.base, high=max(high, ev.base))
    except ValueError as e:  # pragma: no cover
        raise EventError(str(e)) from e
    res.changes.append(FactChange(path=f"route[{r.id}].yield_pm", old=old, new=r.yield_pm.model_dump()))
    res.subjects.append(("route", r.id))


def _logistics(new: PlanInput, ev: LogisticsDelay, res: ApplyResult, prec):
    lane = next((x for x in new.lanes if x.destination == ev.destination), None)
    if lane is None:
        raise EventError(f"unknown lane {ev.destination}")
    nv = max(0, lane.transit_weeks + ev.delta_weeks)
    if nv == lane.transit_weeks:
        res.ignored.append(f"lane[{ev.destination}]: no change")
        return
    res.changes.append(FactChange(path=f"lane[{ev.destination}].transit_weeks", old=lane.transit_weeks, new=nv))
    lane.transit_weeks = nv
    res.subjects.append(("lane", ev.destination))


def _demand(new: PlanInput, ev: DemandChange, res: ApplyResult, prec):
    o = next((x for x in new.orders if x.id == ev.order_id), None)
    if o is None:
        raise EventError(f"unknown order {ev.order_id}")
    if ev.cancel:
        new.orders = [x for x in new.orders if x.id != o.id]
        res.changes.append(FactChange(path=f"order[{o.id}]", old="active", new="cancelled"))
        res.subjects.append(("order", o.id))
        return
    for field in ("qty", "committed_week", "latest_week"):
        nv = getattr(ev, field)
        if nv is not None and nv != getattr(o, field):
            old = getattr(o, field)
            setattr(o, field, nv)
            res.changes.append(FactChange(path=f"order[{o.id}].{field}", old=_j(old), new=_j(nv)))
            res.subjects.append(("order", o.id))
    if o.latest_week < o.committed_week:
        raise EventError("latest_week before committed_week")
    if not res.changed:
        res.ignored.append(f"order[{o.id}]: no change")


def _rules(new: PlanInput, ev: ComplianceRulesChange, res: ApplyResult, prec):
    old = new.compliance.version if new.compliance else None
    if old == ev.ruleset.version and new.compliance == ev.ruleset:
        res.ignored.append("compliance: no change")
        return
    new.compliance = ev.ruleset
    res.changes.append(FactChange(path="compliance.version", old=old, new=ev.ruleset.version))
    res.subjects.append(("compliance", ev.ruleset.version))


def _license(new: PlanInput, ev: LicenseAdd, res: ApplyResult, prec):
    if any(x.id == ev.license.id for x in new.licenses):
        raise EventError(f"license {ev.license.id} already exists")
    new.licenses.append(ev.license)
    res.changes.append(FactChange(path=f"license[{ev.license.id}]", old=None, new=ev.license.model_dump(mode="json")))
    res.subjects.append(("compliance", ev.license.id))


def _route_qual(new: PlanInput, ev: RouteQualification, res: ApplyResult, prec):
    r = next((x for x in new.routes if x.id == ev.route_id), None)
    if r is None:
        raise EventError(f"unknown route {ev.route_id}")
    if (r.qualification, r.qualified_from) == (ev.qualification, ev.qualified_from):
        res.ignored.append(f"route[{r.id}].qualification: no change")
        return
    res.changes.append(FactChange(path=f"route[{r.id}].qualification", old=r.qualification, new=ev.qualification))
    r.qualification, r.qualified_from = ev.qualification, ev.qualified_from
    res.subjects.append(("route", r.id))


def _j(v):
    return v.isoformat() if isinstance(v, (date, datetime)) else v


_HANDLERS = {
    "capacity_change": _capacity, "commitment_change": _commitment, "commitment_add": _commitment_add,
    "lot_status": _lot, "yield_change": _yield, "logistics_delay": _logistics, "demand_change": _demand,
    "compliance_rules": _rules, "license_add": _license, "route_qualification": _route_qual,
}
