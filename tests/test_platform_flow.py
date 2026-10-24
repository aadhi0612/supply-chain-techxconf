"""Service-level closed loop: event -> incident -> options -> approvals -> actions -> confirmation -> audit."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from siliconflow.events import CapacityChange, YieldChange
from siliconflow.sample_data import build_sample, sample_incident_events, wk
from siliconflow.services.actions import OutboxAdapter
from siliconflow.services.platform import Conflicting, Forbidden, Invalid, NotFound, Platform
from siliconflow.services.policy import ExecutionPolicy

from helpers import make_users

T = "demo"


def sample_fast():
    p = build_sample()
    p.config.solver_seconds_per_level = 3
    return p


@pytest.fixture
def world(platform):
    users = make_users(platform)
    platform.import_plan(users["lead"], sample_fast())
    return platform, users


def open_incident(pf, users, events=None):
    r = pf.ingest_events(users["lead"], events or sample_incident_events())
    assert r["incident_id"]
    return r["incident_id"]


def approve_all(pf, users, iid, roles=None):
    inc = pf.get_incident(T, iid, False)
    roles = roles or [r["role"] for r in inc["required_approvals"]]
    for role in roles:
        u = users["planner2"] if role == "planner" else users[role]
        pf.decide(u, iid, "approve", role, "looks right")


def test_happy_path_end_to_end(world, tmp_path):
    pf, u = world
    iid = open_incident(pf, u)
    inc = pf.get_incident(T, iid)
    assert inc["state"] == "options_ready" and inc["material"] and inc["units_on_time_lost_if_no_action"] > 500
    assert all(o["result"]["verified"] for o in inc["analysis"]["options"])
    # exercise the lever path regardless of which option ranks first on a loaded machine (time-limited solves)
    with_levers = [o["id"] for o in inc["analysis"]["options"] if o["result"]["levers"]]
    assert with_levers, "a recovery option that uses levers must exist for this incident"
    pf.select_option(u["lead"], iid, with_levers[0])
    assert pf.get_incident(T, iid, False)["state"] == "pending_approval"
    with pytest.raises(Conflicting):
        pf.execute(u["lead"], iid)  # not approved yet
    approve_all(pf, u, iid)
    assert pf.get_incident(T, iid, False)["state"] == "approved"
    out = pf.execute(u["lead"], iid)
    assert out["state"] in ("executing", "completed")
    kinds = {a["type"] for a in out["actions"]}
    assert {"ALLOCATION_UPDATE", "SUPPLIER_REQUEST", "CUSTOMER_COMMIT_UPDATE", "COMPLIANCE_REVIEW"} <= kinds
    files = list((tmp_path / "outbox" / T).glob("*.json"))
    assert files and json.loads(files[0].read_text())["idempotency_key"]
    n_actions = len(out["actions"])
    again = pf.execute(u["lead"], iid)  # idempotent: no duplicates
    assert len(again["actions"]) == n_actions
    # close the loop with the supplier
    v_before, plan_before = pf.current(T)
    req = next(a for a in out["actions"] if a["type"] == "SUPPLIER_REQUEST" and a["lever_id"] == "LV-SUB2-EXP")
    res = pf.confirm_action(u["lead"], req["id"], "confirmed", qty=1200)
    v_after, plan_after = pf.current(T)
    assert v_after == v_before + 1 and res["snapshot_version"] == v_after
    c = next(c for c in plan_after.commitments if c.po_ref == "LV-SUB2-EXP")
    assert c.qty == 1200 and c.status == "confirmed" and c.provenance.source == "supplier_portal"
    diff = pf.diff(plan_before, plan_after)
    assert any(d["collection"] == "commitments" and d["change"] == "added" for d in diff)
    assert pf.verify_audit(T)["ok"]
    m = pf.metrics(T)
    assert m["recommendation_acceptance_rate"] == 1.0 and m["audit_chain"]["ok"]


def test_four_eyes_and_role_rules(world):
    pf, u = world
    iid = open_incident(pf, u)
    inc = pf.get_incident(T, iid, False)
    pf.select_option(u["lead"], iid, inc["recommended"])
    with pytest.raises(Forbidden):
        pf.decide(u["lead"], iid, "approve", "planner", "")  # proposer cannot approve
    with pytest.raises(Forbidden):
        pf.decide(u["finance"], iid, "approve", "procurement", "")  # does not hold role
    with pytest.raises(Invalid):
        pf.decide(u["planner2"], iid, "reject", "planner", "  ")  # a rejection needs a reason
    pf.decide(u["planner2"], iid, "approve", "planner", "ok")
    # a user holding two roles still cannot satisfy two approvals
    double = pf.authenticate(pf.create_user(T, "double2", ["procurement", "finance"])[1])
    pf.decide(double, iid, "approve", "procurement", "")
    with pytest.raises(Forbidden):
        pf.decide(double, iid, "approve", "finance", "")


def test_rejection_requires_reason_and_allows_reselection(world):
    pf, u = world
    iid = open_incident(pf, u)
    rec = pf.get_incident(T, iid, False)["recommended"]
    pf.select_option(u["lead"], iid, rec)
    pf.decide(u["planner2"], iid, "reject", "planner", "spend too high")
    assert pf.get_incident(T, iid, False)["state"] == "rejected"
    assert pf.metrics(T)["rejection_reasons"] == {"spend too high": 1}
    other = next(o["id"] for o in pf.get_incident(T, iid)["analysis"]["options"] if o["profile"] == "no_spend")
    pf.select_option(u["lead"], iid, other)
    assert pf.get_incident(T, iid, False)["state"] == "pending_approval"
    # approvals given for a different plan do not carry over
    approve_all(pf, u, iid)
    assert pf.get_incident(T, iid, False)["state"] == "approved"


def test_viewer_cannot_write(world):
    pf, u = world
    with pytest.raises(Forbidden):
        pf.ingest_events(u["viewer"], sample_incident_events())
    with pytest.raises(Forbidden):
        pf.import_plan(u["viewer"], sample_fast())


def test_stale_plan_is_blocked_and_replan_supersedes(world):
    pf, u = world
    iid = open_incident(pf, u)
    pf.select_option(u["lead"], iid, next(o["id"] for o in pf.get_incident(T, iid)["analysis"]["options"] if o["profile"] == "no_spend"))
    approve_all(pf, u, iid)
    # reality moves after approval: the packaging capacity the plan relies on disappears
    pf.ingest_events(u["lead"], [CapacityChange(source="supplier_portal", resource_id="PKG-A", from_week=wk(1), to_week=wk(9), mode="set", value=0,
                                                 observed_at=datetime.now(timezone.utc) + timedelta(seconds=5))], create_incident=False)
    with pytest.raises(Conflicting, match="re-plan"):
        pf.execute(u["lead"], iid)
    assert pf.get_incident(T, iid, False)["state"] == "stale"
    assert pf.list_incidents(T, "stale")
    new = pf.replan(u["lead"], iid, "capacity lost")
    assert new["state"] in ("options_ready", "no_impact") and pf.get_incident(T, new["id"], False)["parent_id"] == iid


class FlakyAdapter:
    name = "flaky"

    def __init__(self, fail_first):
        self.fail, self.calls, self.keys = fail_first, 0, []

    def send(self, tenant, action_id, idem, atype, target, payload):
        self.calls += 1
        if self.calls <= self.fail:
            raise ConnectionError("erp unavailable")
        self.keys.append(idem)
        return {"ok": True}


def test_adapter_failures_are_retried_recorded_and_resumable(tmp_path):
    ad = FlakyAdapter(fail_first=6)
    pf = Platform("sqlite://", ExecutionPolicy(mode="execute", max_attempts=3), ad, sleep=lambda _: None, analysis_profiles=["no_spend"], analysis_binding=False)
    u = make_users(pf)
    pf.import_plan(u["lead"], sample_fast())
    iid = open_incident(pf, u)
    pf.select_option(u["lead"], iid, "opt_no_spend")
    approve_all(pf, u, iid)
    out = pf.execute(u["lead"], iid)
    failed = [a for a in out["actions"] if a["status"] == "failed"]
    assert len(failed) == 2 and all("ConnectionError" in a["last_error"] for a in failed) and out["state"] == "executing"
    out2 = pf.execute(u["lead"], iid)  # resumes only the failed ones
    assert not [a for a in out2["actions"] if a["status"] == "failed"]
    assert len(set(ad.keys)) == len(ad.keys), "each action is delivered once with a stable idempotency key"
    pf.close()


def test_draft_mode_requires_human_send_and_shadow_mode_refuses():
    pf = Platform("sqlite://", ExecutionPolicy(mode="draft"), OutboxAdapter("unused"), analysis_profiles=["no_spend"], analysis_binding=False)
    u = make_users(pf)
    pf.import_plan(u["lead"], sample_fast())
    iid = open_incident(pf, u)
    pf.select_option(u["lead"], iid, "opt_no_spend")
    approve_all(pf, u, iid)
    out = pf.execute(u["lead"], iid)
    assert out["actions"] and all(a["status"] == "drafted" for a in out["actions"]) and out["state"] == "executing"
    for a in out["actions"]:
        pf.mark_sent(u["lead"], a["id"], "sent by email")
    assert pf.get_incident(T, iid, False)["state"] == "completed"
    pf.close()
    sh = Platform("sqlite://", ExecutionPolicy(mode="shadow"), analysis_profiles=["no_spend"], analysis_binding=False)
    us = make_users(sh)
    sh.import_plan(us["lead"], sample_fast())
    i2 = open_incident(sh, us)
    sh.select_option(us["lead"], i2, "opt_no_spend")
    approve_all(sh, us, i2)
    with pytest.raises(Conflicting, match="shadow"):
        sh.execute(us["lead"], i2)
    sh.close()


def test_declined_lever_triggers_replan_and_revert_creates_compensation(world):
    pf, u = world
    iid = open_incident(pf, u)
    inc = pf.get_incident(T, iid)
    bal = next(o["id"] for o in inc["analysis"]["options"] if o["result"]["levers"])
    pf.select_option(u["lead"], iid, bal)
    approve_all(pf, u, iid)
    out = pf.execute(u["lead"], iid)
    req = next(a for a in out["actions"] if a["type"] == "SUPPLIER_REQUEST")
    res = pf.confirm_action(u["lead"], req["id"], "declined", note="no capacity")
    assert res["replan_incident_id"] and pf.get_incident(T, res["replan_incident_id"], False)["parent_id"] == iid
    other = next(a for a in out["actions"] if a["type"] == "ALLOCATION_UPDATE")
    comp = pf.revert_action(u["lead"], other["id"], "plan superseded")
    assert comp["type"] == "CANCEL_ALLOCATION_UPDATE" and comp["reverts_action_id"] == other["id"]


def test_document_to_incident_requires_human_confirmation(world):
    pf, u = world
    doc = "OSAT-A will reduce reserved capacity (PKG-A) by 25% in weeks 4-9. Substrate lot LOT-S-77 is on quality hold."
    r = pf.ingest_document(u["lead"], "email", doc)
    ids = [p["id"] for p in r["proposals"]]
    assert len(ids) == 2 and pf.current(T)[0] == 1, "nothing changes before confirmation"
    assert pf.ingest_document(u["lead"], "email", doc)["duplicate"]
    with pytest.raises(Invalid, match="ambigu"):
        pf.confirm_proposals(u["lead"], ids)
    out = pf.confirm_proposals(u["lead"], ids, acknowledge_ambiguities=True)
    assert out["incident_id"] and out["snapshot_version"] == 2
    assert {p["status"] for p in pf.list_proposals(T)} == {"confirmed"}
    # conservative application from a lower-authority source leaves auditable open conflicts
    assert pf.list_conflicts(T)
    eid = pf.list_conflicts(T)[0]["event_id"]
    assert pf.resolve_conflicts(u["lead"], "proposed", event_id=eid, note="portal confirmed")
    assert not [c for c in pf.list_conflicts(T) if c["event_id"] == eid]
    r2 = pf.ingest_document(u["lead"], "email", "PKG-A capacity will be cut by 5% in weeks 1-2.")
    pf.reject_proposal(u["lead"], r2["proposals"][0]["id"], "not our contract")
    assert pf.list_proposals(T, "rejected")


def test_conflict_resolution_restoring_current_value(world):
    pf, u = world
    ev = CapacityChange(source="email", resource_id="PKG-A", from_week=wk(4), to_week=wk(4), mode="reduce_pct", value=50, observed_at=datetime.now(timezone.utc))
    pf.ingest_events(u["lead"], [ev], create_incident=False)
    (c,) = pf.list_conflicts(T)
    assert c["applied"] and c["current_value"] == 1550 and c["proposed_value"] == 775
    pf.resolve_conflict(u["lead"], c["id"], "current", "email was wrong")
    cap = next(b for b in pf.current(T)[1].capacity if b.resource_id == "PKG-A" and b.week == wk(4))
    assert cap.reserved == 1550 and not pf.list_conflicts(T)
    with pytest.raises(Conflicting):
        pf.resolve_conflict(u["lead"], c["id"], "current")


def test_no_impact_event_closes_without_options(world):
    pf, u = world
    r = pf.ingest_events(u["lead"], [YieldChange(source="erp", route_id="R-C", base=820, observed_at=datetime.now(timezone.utc))])
    inc = pf.get_incident(T, r["incident_id"], False)
    assert inc["state"] == "no_impact" and inc["material"] is False


def test_noop_event_creates_no_snapshot_or_incident(world):
    pf, u = world
    from siliconflow.events import LogisticsDelay
    r = pf.ingest_events(u["lead"], [LogisticsDelay(source="erp", destination="JP", delta_weeks=0)])
    assert r["incident_id"] is None and not r["changed"] and pf.current(T)[0] == 1


def test_duplicate_event_id_rejected(world):
    pf, u = world
    ev = YieldChange(source="erp", route_id="R-C", base=820, observed_at=datetime.now(timezone.utc))
    pf.ingest_events(u["lead"], [ev], create_incident=False)
    with pytest.raises(Conflicting):
        pf.ingest_events(u["lead"], [ev], create_incident=False)


def test_invalid_event_rolls_back_entirely(world):
    pf, u = world
    good = YieldChange(source="erp", route_id="R-C", base=820, observed_at=datetime.now(timezone.utc))
    bad = CapacityChange(source="erp", resource_id="NOPE", from_week=wk(1), to_week=wk(1), mode="set", value=1)
    with pytest.raises(Invalid):
        pf.ingest_events(u["lead"], [good, bad])
    assert pf.current(T)[0] == 1


def test_audit_chain_detects_tampering(world):
    pf, u = world
    open_incident(pf, u)
    assert pf.verify_audit(T)["ok"]
    with pf.engine.begin() as c:
        c.execute(text("UPDATE audit_log SET actor='mallory' WHERE seq=2"))
    res = pf.verify_audit(T)
    assert not res["ok"] and res["broken_at"] == 2


def test_audit_chain_detects_truncation(world):
    pf, u = world
    open_incident(pf, u)
    with pf.engine.begin() as c:
        c.execute(text("DELETE FROM audit_log WHERE seq=(SELECT MAX(seq) FROM audit_log)"))
    assert not pf.verify_audit(T)["ok"]


def test_tenant_isolation(world):
    pf, u = world
    iid = open_incident(pf, u)
    other = pf.authenticate(pf.create_user("other", "x", ["planner"])[1])
    with pytest.raises(NotFound):
        pf.get_incident("other", iid)
    with pytest.raises(NotFound):
        pf.select_option(other, iid, "opt_no_spend")
    with pytest.raises(NotFound):
        pf.current("other")
    assert pf.list_incidents("other") == [] and pf.audit("other") != pf.audit(T)
    other_plan = sample_fast()
    other_plan.tenant_id = "demo"  # attempt to smuggle another tenant id: it is overwritten by the principal's tenant
    pf.import_plan(other, other_plan)
    assert pf.current("other")[1].tenant_id == "other" and pf.current(T)[0] == 2


def test_calibration_report_is_advisory(world):
    pf, u = world
    for planned, actual in [({"qty": 100, "week": 2}, {"qty": 100, "week": 2}), ({"qty": 100, "week": 2}, {"qty": 80, "week": 4})]:
        pf.record_outcome(u["lead"], "supplier_commitment", "MemCo", planned, actual)
    pf.record_outcome(u["lead"], "delivery", "ORD-1001", {"units": 1000}, {"units": 900})
    rep = pf.calibration_report(T)
    assert rep["suppliers"]["MemCo"]["fully_on_time"] == 1 and rep["suppliers"]["MemCo"]["reliability_laplace"] == 0.5
    assert rep["delivery_error_pct_mean"] == -10.0 and "Advisory" in rep["note"]
    with pytest.raises(Invalid):
        pf.record_outcome(u["lead"], "nonsense", "x", {}, {})


def test_async_analysis_on_file_database(tmp_path):
    import time
    pf = Platform(f"sqlite:///{tmp_path / 'sf.db'}", ExecutionPolicy(mode="draft"), analysis_profiles=["no_spend"], analysis_binding=False)
    u = make_users(pf)
    pf.import_plan(u["lead"], sample_fast())
    r = pf.ingest_events(u["lead"], sample_incident_events(), mode="async")
    assert pf.get_incident(T, r["incident_id"], False)["state"] in ("analyzing", "options_ready")
    for _ in range(120):
        if pf.get_incident(T, r["incident_id"], False)["state"] != "analyzing":
            break
        time.sleep(0.5)
    assert pf.get_incident(T, r["incident_id"], False)["state"] == "options_ready"
    assert pf.verify_audit(T)["ok"]
    pf.close()
