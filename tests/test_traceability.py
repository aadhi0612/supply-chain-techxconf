"""Ledger anchoring, inclusion proofs, tamper detection, ledger outage, corrections (`supersedes`) and per-record history."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from siliconflow.api.app import create_app
from siliconflow.events import CommitmentChange, DemandChange
from siliconflow.sample_data import build_sample, sample_incident_events
from siliconflow.services import traceability
from siliconflow.services.platform import Conflicting, Invalid, NotFound
from siliconflow.store import merkle

from helpers import make_users

T = "demo"


@pytest.fixture
def world(platform):
    users = make_users(platform)
    plan = build_sample()
    plan.config.solver_seconds_per_level = 3
    platform.import_plan(users["lead"], plan)
    return platform, users


def commit_change(qty, **kw):
    return CommitmentChange(source="erp", commitment_id="PO-LOGIC-1", qty=qty, observed_at=datetime(2026, 10, 24, 10, tzinfo=timezone.utc), **kw)


def write(pf, u, *events):
    return pf.ingest_events(u["lead"], list(events), create_incident=False)


# ---------------------------------------------------------------- merkle
def test_merkle_paths_verify_for_every_leaf_and_size():
    for n in range(1, 12):
        hs = [f"{i:064x}" for i in range(n)]
        r = merkle.root(hs)
        for i, h in enumerate(hs):
            assert merkle.verify_path(h, merkle.proof(hs, i), r)
        assert not merkle.verify_path(f"{99:064x}", merkle.proof(hs, 0), r)


# ---------------------------------------------------------------- anchoring and proofs
def test_anchor_roundtrip_and_proof(world):
    pf, u = world
    write(pf, u, commit_change(900))
    res = pf.anchor_audit(u["lead"])
    assert res["anchored"] and res["seq_from"] == 1 and res["entries"] == res["seq_to"]
    v = pf.verify_audit(T)
    assert v["ok"] and v["anchors"]["verified"] == 1 and v["anchors"]["unanchored_entries"] == 0 and v["ledger"]["reachable"]
    p = pf.audit_proof(T, 2)
    assert p["anchored"] and p["valid"] and p["ledger_root"] == res["root"] and p["recomputed_leaf"] == p["entry"]["stored_hash"]
    assert merkle.verify_path(p["recomputed_leaf"], p["path"], res["root"])
    assert pf.anchor_audit(u["lead"]) == {"anchored": False, "reason": "nothing new to anchor", "anchored_through": res["seq_to"]}
    write(pf, u, commit_change(950))
    v2 = pf.verify_audit(T)
    assert v2["ok"] and v2["anchors"]["unanchored_since"] == res["seq_to"] + 1
    assert pf.audit_proof(T, res["seq_to"] + 1)["anchored"] is False
    assert pf.anchor_audit(u["lead"])["seq_from"] == res["seq_to"] + 1
    assert [a["id"] for a in pf.list_anchors(T)] == [2, 1]


def test_viewer_cannot_anchor_but_can_verify(world):
    pf, u = world
    with pytest.raises(Exception) as e:
        pf.anchor_audit(u["viewer"])
    assert getattr(e.value, "status", None) == 403
    assert pf.verify_audit(T)["ok"]


def test_edit_after_anchoring_is_caught_by_chain_and_by_proof(world):
    pf, u = world
    write(pf, u, commit_change(900))
    pf.anchor_audit(u["lead"])
    with pf.engine.begin() as c:
        c.execute(text("UPDATE audit_log SET actor='mallory' WHERE seq=2"))
    v = pf.verify_audit(T)
    assert not v["ok"] and v["broken_at"] == 2 and not v["chain_ok"]
    p = pf.audit_proof(T, 2)
    assert p["valid"] is False and "content no longer matches" in p["reason"]
    with pytest.raises(Conflicting):  # a broken chain is never anchored
        pf.anchor_audit(u["lead"])


def test_insider_who_rebuilds_the_whole_chain_is_caught_by_the_ledger(world):
    pf, u = world
    write(pf, u, commit_change(900))
    pf.anchor_audit(u["lead"])
    traceability.demo_tamper(pf, T, 2, rebuild=True)
    v = pf.verify_audit(T)
    assert v["chain_ok"], "a consistent rewrite passes the platform's own chain check"
    assert not v["ok"] and v["anchors"]["failures"] and "no longer match the root published to the ledger" in v["anchors"]["failures"][0]["why"]
    p = pf.audit_proof(T, 2)
    assert p["valid"] is False and "rewritten after anchoring" in p["reason"]
    traceability.demo_untamper(pf, T)
    assert pf.verify_audit(T)["ok"] and pf.audit_proof(T, 2)["valid"]


def test_untamper_refuses_if_new_entries_were_written_after_a_rewrite(world):
    pf, u = world
    write(pf, u, commit_change(900))
    traceability.demo_tamper(pf, T, 2, rebuild=True)
    write(pf, u, commit_change(901))
    with pytest.raises(Conflicting):
        traceability.demo_untamper(pf, T)


def test_ledger_outage_never_blocks_work_and_is_reported(world):
    pf, u = world
    write(pf, u, commit_change(900))
    pf.ledger.available = False
    with pytest.raises(Exception) as e:
        pf.anchor_audit(u["lead"])
    assert getattr(e.value, "status", None) == 503
    write(pf, u, commit_change(950))  # platform keeps working
    v = pf.verify_audit(T)
    assert v["ok"] and not v["anchors"]["ledger_reachable"] and v["anchors"]["unanchored_since"] == 1
    pf.ledger.available = True
    assert pf.anchor_audit(u["lead"])["anchored"]
    assert pf.verify_audit(T)["anchors"]["unanchored_entries"] == 0


def test_stored_anchor_edited_to_match_a_forged_history_still_fails_against_ledger(world):
    pf, u = world
    write(pf, u, commit_change(900))
    pf.anchor_audit(u["lead"])
    traceability.demo_tamper(pf, T, 2, rebuild=True)
    with pf.tx() as s:  # operator also rewrites their own anchors table to match the forged chain
        rows = traceability._audit_rows(s, T, 1, 3)
        forged = merkle.root([r.hash for r in rows])
        s.execute(text("UPDATE audit_anchors SET root=:r"), {"r": forged})
    v = pf.verify_audit(T)
    assert not v["ok"] and "differs from the ledger" in v["anchors"]["failures"][0]["why"]


# ---------------------------------------------------------------- corrections
def test_supersedes_needs_a_reason_and_a_real_target(world):
    pf, u = world
    with pytest.raises(ValidationError):
        commit_change(10, supersedes="evt_x")
    with pytest.raises(Invalid):
        write(pf, u, commit_change(10, supersedes="evt_does_not_exist", note="fix"))


def test_correction_links_back_and_trace_shows_who_when_and_what(world):
    pf, u = world
    bad = commit_change(5)
    write(pf, u, bad)
    fix = CommitmentChange(source="manual", force=True, commitment_id="PO-LOGIC-1", qty=40_000, supersedes=bad.id,
                           note="ERP typo: 5 entered instead of 40,000", observed_at=datetime(2026, 10, 24, 11, tzinfo=timezone.utc))
    write(pf, u, fix)
    audit = pf.audit(T, "event", fix.id)
    assert audit[0]["data"]["supersedes"] == bad.id and "typo" in audit[0]["data"]["note"]
    t = pf.trace(T, "commitment", "PO-LOGIC-1")
    first, second = t["history"][-2], t["history"][-1]
    assert first["event_id"] == bad.id and first["superseded_by"] == fix.id and first["actor_name"] == "lead" and first["source"] == "erp"
    assert second["supersedes"] == bad.id and second["forced"] and second["note"].startswith("ERP typo")
    assert any(c["new"] == 40_000 for c in second["changes"])
    assert t["current"]["qty"] == 40_000 and t["baseline"]["imported_by"] == "lead" and t["baseline"]["audit_seq"]
    assert not first["anchored"]
    pf.anchor_audit(u["lead"])
    assert all(h["anchored"] for h in pf.trace(T, "commitment", "PO-LOGIC-1")["history"])


def test_trace_order_and_errors(world):
    pf, u = world
    write(pf, u, DemandChange(source="erp", order_id="ORD-1001", qty=1234, observed_at=datetime(2026, 10, 24, 10, tzinfo=timezone.utc)))
    t = pf.trace(T, "order", "ORD-1001")
    assert len(t["history"]) == 1 and t["history"][0]["changes"][0]["path"].startswith("order[ORD-1001]")
    assert pf.trace(T, "order", "ORD-1002")["history"] == []
    with pytest.raises(NotFound):
        pf.trace(T, "order", "NOPE")
    with pytest.raises(Invalid):
        pf.trace(T, "banana", "x")
    assert "ORD-1001" in pf.trace_entities(T)["order"] and "PKG-A" in pf.trace_entities(T)["resource"]


def test_trace_shows_incident_approvals_for_a_resource(world):
    pf, u = world
    iid = pf.ingest_events(u["lead"], sample_incident_events())["incident_id"]
    inc = pf.get_incident(T, iid)
    pf.select_option(u["lead"], iid, inc["recommended"])
    for r in pf.get_incident(T, iid, False)["required_approvals"]:
        pf.decide(u["planner2"] if r["role"] == "planner" else u[r["role"]], iid, "approve", r["role"], "ok")
    t = pf.trace(T, "resource", "PKG-A")
    ev = t["history"][-1]
    assert ev["type"] == "capacity_change" and ev["incident"]["id"] == iid and ev["incident"]["plan_hash"]
    assert {a["role"] for a in ev["incident"]["approvals"]} == {r["role"] for r in pf.get_incident(T, iid, False)["required_approvals"]}
    assert all(a["by"] and a["at"] and a["plan_hash"] == ev["incident"]["plan_hash"] for a in ev["incident"]["approvals"])
    assert pf.trace(T, "lot", "LOT-S-77")["history"][-1]["incident"]["id"] == iid


# ---------------------------------------------------------------- HTTP
def test_http_surface_and_demo_gating(platform):
    for name, roles in [("lead", ["planner"]), ("viewer", ["viewer"])]:
        platform.create_user(T, name, roles)
    users = make_users(platform, "other")
    assert users  # second tenant exists but must not leak
    key = platform.create_user(T, "pat", ["planner"])[1]
    vkey = platform.create_user(T, "vic", ["viewer"])[1]
    h, hv = {"Authorization": "Bearer " + key}, {"Authorization": "Bearer " + vkey}
    plan = build_sample().model_dump(mode="json")
    c = TestClient(create_app(platform, demo=False))
    assert c.post("/v1/dataset", json={"plan": plan}, headers=h).status_code == 201
    assert c.post("/v1/events", json={"events": [{"type": "commitment_change", "source": "erp", "commitment_id": "PO-LOGIC-1", "qty": 7}],
                                      "create_incident": False}, headers=h).status_code == 201
    assert c.post("/v1/audit/anchor", headers=hv).status_code == 403
    a = c.post("/v1/audit/anchor", headers=h)
    assert a.status_code == 200 and a.json()["anchored"]
    assert c.get("/v1/audit/anchors", headers=hv).json()[0]["root"] == a.json()["root"]
    pr = c.get("/v1/audit/proof/1", headers=hv).json()
    assert pr["valid"] and pr["ledger_root"] == a.json()["root"]
    assert c.get("/v1/audit/proof/9999", headers=hv).status_code == 404
    assert c.get("/v1/audit/verify", headers=hv).json()["anchors"]["verified"] == 1
    assert c.get("/v1/trace", headers=hv).json()["commitment"]
    assert c.get("/v1/trace/commitment/PO-LOGIC-1", headers=hv).json()["history"][0]["source"] == "erp"
    assert c.get("/v1/trace/commitment/NOPE", headers=hv).status_code == 404
    # the tamper lab does not exist outside demo mode
    assert c.post("/v1/demo/tamper", json={"seq": 1}, headers=h).status_code in (404, 405)
    assert c.post("/v1/demo/ledger", json={"available": False}, headers=h).status_code in (404, 405)


def test_http_demo_tamper_lab_requires_a_writer(platform):
    c = TestClient(create_app(platform, demo=True))  # seeds demo users and the sample dataset
    pat, vic = {"Authorization": "Bearer sf_demo_pat"}, {"Authorization": "Bearer sf_demo_vic"}
    assert c.post("/v1/audit/anchor", headers=pat).json()["anchored"]
    assert c.post("/v1/demo/tamper", json={"seq": 2}, headers=vic).status_code == 403
    assert c.post("/v1/demo/tamper", json={"seq": 2}, headers=pat).status_code == 200
    assert c.get("/v1/audit/verify", headers=vic).json()["ok"] is False
    assert c.post("/v1/demo/untamper", headers=pat).json()["restored"] >= 1
    assert c.get("/v1/audit/verify", headers=vic).json()["ok"] is True
    assert c.post("/v1/demo/ledger", json={"available": False}, headers=pat).json() == {"ledger_available": False}
    assert c.post("/v1/events", json={"events": [{"type": "commitment_change", "source": "erp", "commitment_id": "PO-LOGIC-1", "qty": 3}],
                                      "create_incident": False}, headers=pat).status_code == 201
    assert c.post("/v1/audit/anchor", headers=pat).status_code == 503
    c.post("/v1/demo/ledger", json={"available": True}, headers=pat)
    assert c.post("/v1/audit/anchor", headers=pat).json()["anchored"]
