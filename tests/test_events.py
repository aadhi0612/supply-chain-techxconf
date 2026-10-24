from datetime import datetime, timedelta, timezone

import pytest

from siliconflow.events import (
    CapacityChange, CommitmentChange, DemandChange, EventError, LogisticsDelay, LotStatusChange, YieldChange, apply_event,
)
from siliconflow.sample_data import wk

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)  # newer than sample data (Oct 23 and earlier)


def cap_ev(**kw):
    base = dict(source="supplier_portal", observed_at=NOW, resource_id="PKG-A", from_week=wk(4), to_week=wk(5), mode="reduce_pct", value=25)
    return CapacityChange(**{**base, **kw})


def bucket(plan, rid, w):
    return next(b for b in plan.capacity if b.resource_id == rid and b.week == wk(w))


def test_event_does_not_mutate_input(sample):
    before = sample.content_hash()
    new, res = apply_event(sample, cap_ev())
    assert sample.content_hash() == before and new.content_hash() != before
    assert bucket(new, "PKG-A", 4).reserved == 1162 and bucket(sample, "PKG-A", 4).reserved == 1550
    assert len(res.changes) == 2 and not res.conflicts


def test_stale_observation_is_ignored(sample):
    old = cap_ev(observed_at=NOW - timedelta(days=30))
    new, res = apply_event(sample, old)
    assert not res.changed and any("stale" in i for i in res.ignored)
    assert new.content_hash() == sample.content_hash()


def test_lower_authority_cannot_improve_without_confirmation(sample):
    # PKG-A data came from supplier_portal; an email claiming MORE capacity must not be applied
    new, res = apply_event(sample, cap_ev(source="email", mode="set", value=1700))
    assert not res.changed and res.conflicts and not res.conflicts[0].applied
    assert bucket(new, "PKG-A", 4).reserved == 1550


def test_lower_authority_can_worsen_conservatively_but_is_flagged(sample):
    new, res = apply_event(sample, cap_ev(source="email"))
    assert bucket(new, "PKG-A", 4).reserved == 1162
    assert len(res.conflicts) == 2 and all(c.applied for c in res.conflicts)


def test_force_overrides_precedence(sample):
    new, res = apply_event(sample, cap_ev(source="manual", mode="set", value=1700, force=True))
    assert bucket(new, "PKG-A", 4).reserved == 1700 and bucket(new, "PKG-A", 4).nominal >= 1700


def test_unknown_entities_raise(sample):
    with pytest.raises(EventError):
        apply_event(sample, cap_ev(resource_id="NOPE"))
    with pytest.raises(EventError):
        apply_event(sample, CommitmentChange(source="erp", commitment_id="NOPE", qty=1))
    with pytest.raises(EventError):
        apply_event(sample, DemandChange(source="erp", order_id="NOPE", qty=1))


def test_commitment_slip_and_quantity(sample):
    ev = CommitmentChange(source="supplier_portal", observed_at=NOW, commitment_id="PO-HBMA-1", promised_week=wk(3), qty=20000, status="delayed")
    new, res = apply_event(sample, ev)
    c = next(c for c in new.commitments if c.id == "PO-HBMA-1")
    assert (c.promised_week, c.qty, c.status) == (wk(3), 20000, "delayed") and len(res.changes) == 3


def test_lower_source_better_news_on_commitment_is_conflict(sample):
    ev = CommitmentChange(source="email", observed_at=NOW, commitment_id="PO-HBMA-1", promised_week=wk(-1))
    new, res = apply_event(sample, ev)
    assert not res.changed and res.conflicts[0].field == "promised_week"


def test_lot_hold_and_release_asymmetry(sample):
    held, _ = apply_event(sample, LotStatusChange(source="supplier_portal", observed_at=NOW, lot_id="LOT-S-77", status="hold"))
    assert next(l for l in held.inventory if l.id == "LOT-S-77").status == "hold"
    # an email may not release a hold placed by the portal
    rel, res = apply_event(held, LotStatusChange(source="email", observed_at=NOW + timedelta(hours=1), lot_id="LOT-S-77", status="available"))
    assert not res.changed and res.conflicts and next(l for l in rel.inventory if l.id == "LOT-S-77").status == "hold"


def test_yield_logistics_demand(sample):
    new, _ = apply_event(sample, YieldChange(source="erp", route_id="R-A", base=850))
    r = next(r for r in new.routes if r.id == "R-A")
    assert (r.yield_pm.low, r.yield_pm.base) == (850, 850)
    new, _ = apply_event(sample, LogisticsDelay(source="erp", destination="JP", delta_weeks=2))
    assert new.transit("JP") == 3
    new, res = apply_event(sample, DemandChange(source="erp", order_id="ORD-1001", cancel=True))
    assert all(o.id != "ORD-1001" for o in new.orders)
    new, _ = apply_event(sample, DemandChange(source="erp", order_id="ORD-1001", qty=3500, committed_week=wk(12), latest_week=wk(15)))
    assert new.order("ORD-1001").qty == 3500


def test_noop_event_changes_nothing(sample):
    _, res = apply_event(sample, LogisticsDelay(source="erp", destination="JP", delta_weeks=0))
    assert not res.changed and res.ignored
