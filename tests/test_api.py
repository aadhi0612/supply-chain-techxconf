"""HTTP surface: auth, RBAC, validation, and the full flow over the wire."""
import pytest
from fastapi.testclient import TestClient

from siliconflow.api.app import create_app
from siliconflow.sample_data import build_sample, sample_incident_events


@pytest.fixture
def api(platform):
    uids = {}
    for name, roles in [("lead", ["planner"]), ("planner2", ["planner"]), ("viewer", ["viewer"]), ("procurement", ["procurement"]),
                        ("finance", ["finance"]), ("sales", ["sales"]), ("compliance", ["compliance"])]:
        uids[name] = {"Authorization": "Bearer " + platform.create_user("demo", name, roles)[1]}
    return TestClient(create_app(platform)), uids


def sample_json():
    p = build_sample()
    p.config.solver_seconds_per_level = 3
    return p.model_dump(mode="json")


def test_auth_required_and_health_open(api):
    c, h = api
    assert c.get("/healthz").json()["status"] == "ok"
    assert c.get("/v1/dataset").status_code == 401
    assert c.get("/v1/dataset", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_viewer_is_read_only_and_bad_payloads_are_422(api):
    c, h = api
    assert c.post("/v1/dataset", json={"plan": sample_json()}, headers=h["viewer"]).status_code == 403
    assert c.post("/v1/dataset", json={"plan": {"plan_start": "2026-10-26", "bogus": 1}}, headers=h["lead"]).status_code == 422
    bad = sample_json()
    bad["orders"][0]["sku_id"] = "NOPE"
    r = c.post("/v1/dataset", json={"plan": bad}, headers=h["lead"])
    assert r.status_code == 422 and "order_unknown_sku" in r.json()["detail"]
    assert c.get("/v1/dataset", headers=h["viewer"]).status_code == 404


def test_full_flow_over_http(api):
    c, h = api
    r = c.post("/v1/dataset", json={"plan": sample_json()}, headers=h["lead"])
    assert r.status_code == 201 and r.json()["version"] == 1
    assert c.get("/v1/dataset/quality", headers=h["viewer"]).json()["metrics"]["orders_compliance_held"] == 2

    events = [e.model_dump(mode="json") for e in sample_incident_events()]
    r = c.post("/v1/events", json={"events": events}, headers=h["lead"])
    assert r.status_code == 201, r.text
    iid = r.json()["incident_id"]
    inc = c.get(f"/v1/incidents/{iid}", headers=h["viewer"]).json()
    assert inc["state"] == "options_ready" and inc["analysis"]["options"]
    assert c.get("/v1/incidents", headers=h["viewer"]).json()[0]["id"] == iid

    assert c.post(f"/v1/incidents/{iid}/select", json={"option_id": "opt_nope"}, headers=h["lead"]).status_code == 404
    assert c.post(f"/v1/incidents/{iid}/select", json={"option_id": inc["recommended"]}, headers=h["lead"]).status_code == 200
    required = [x["role"] for x in c.get(f"/v1/incidents/{iid}", headers=h["lead"]).json()["required_approvals"]]
    # proposer cannot approve; an unrelated role cannot approve
    assert c.post(f"/v1/incidents/{iid}/decisions", json={"decision": "approve", "role": "planner"}, headers=h["lead"]).status_code == 403
    assert c.post(f"/v1/incidents/{iid}/decisions", json={"decision": "approve", "role": "finance"}, headers=h["sales"]).status_code == 403
    assert c.post(f"/v1/incidents/{iid}/execute", headers=h["lead"]).status_code == 409
    for role in required:
        who = "planner2" if role == "planner" else role
        assert c.post(f"/v1/incidents/{iid}/decisions", json={"decision": "approve", "role": role}, headers=h[who]).status_code == 200
    ex = c.post(f"/v1/incidents/{iid}/execute", headers=h["lead"])
    assert ex.status_code == 200 and ex.json()["actions"]
    req = next(a for a in ex.json()["actions"] if a["type"] == "SUPPLIER_REQUEST")
    r = c.post(f"/v1/actions/{req['id']}/confirmation", json={"outcome": "confirmed"}, headers=h["lead"])
    assert r.status_code == 200 and r.json()["snapshot_version"] == 3

    snaps = c.get("/v1/snapshots", headers=h["viewer"]).json()
    assert [s["version"] for s in snaps] == [3, 2, 1]
    assert c.get("/v1/snapshots/diff", params={"a": 1, "b": 2}, headers=h["viewer"]).json()
    assert c.get("/v1/audit/verify", headers=h["viewer"]).json()["ok"]
    assert any(e["action"] == "incident.approved" for e in c.get("/v1/audit", params={"entity_type": "incident"}, headers=h["viewer"]).json())
    m = c.get("/v1/metrics", headers=h["viewer"]).json()
    assert m["recommendation_acceptance_rate"] == 1.0


def test_csv_table_upload_layers_on_master_data(api):
    c, h = api
    c.post("/v1/dataset", json={"plan": sample_json()}, headers=h["lead"])
    hdr = "id,customer_id,sku_id,qty,requested_week,committed_week,latest_week,destination\n"
    only = "ONLY,HYPERION,AX-900,100,2026-12-07,2026-12-07,2026-12-21,US\n"
    # dropping ORD-1003 would orphan the lever that targets it: integrity validation refuses the upload
    r = c.post("/v1/dataset/tables", files={"orders": ("orders.csv", hdr + only, "text/csv")}, headers=h["lead"])
    assert r.status_code == 422 and "lever_unknown_target" in r.json()["detail"]
    keep = "ORD-1003,ORION,AX-900,2000,2026-12-14,2026-12-14,2026-12-28,DE\n"
    r = c.post("/v1/dataset/tables", files={"orders": ("orders.csv", hdr + only + keep, "text/csv")}, headers=h["lead"])
    assert r.status_code == 201 and r.json()["version"] == 2
    plan = c.get("/v1/dataset", headers=h["lead"]).json()["plan"]
    assert [o["id"] for o in plan["orders"]] == ["ONLY", "ORD-1003"] and plan["commitments"]
    bad = c.post("/v1/dataset/tables", files={"orders": ("o.csv", "id,qty\nX,abc\n", "text/csv")}, headers=h["lead"])
    assert bad.status_code == 422
    assert c.post("/v1/dataset/tables", headers=h["lead"]).status_code == 422


def test_document_and_proposal_endpoints(api):
    c, h = api
    c.post("/v1/dataset", json={"plan": sample_json()}, headers=h["lead"])
    r = c.post("/v1/documents", json={"source": "email", "text": "Lot LOT-S-77 is on quality hold."}, headers=h["lead"])
    assert r.status_code == 201
    (prop,) = r.json()["proposals"]
    assert c.post("/v1/proposals/confirm", json={"ids": [prop["id"]]}, headers=h["viewer"]).status_code == 403
    r = c.post("/v1/proposals/confirm", json={"ids": [prop["id"]]}, headers=h["lead"])
    assert r.status_code == 201 and r.json()["incident_id"]
    assert c.get("/v1/proposals", params={"status": "confirmed"}, headers=h["lead"]).json()


def test_openapi_documents_every_route(api):
    c, _ = api
    spec = c.get("/openapi.json").json()
    assert len(spec["paths"]) >= 25


def test_ui_is_served_and_root_redirects(api):
    c, _ = api
    r = c.get("/ui/")
    assert r.status_code == 200 and "Know what" in r.text and "SiliconFlow" in r.text
    assert c.get("/", follow_redirects=False).headers["location"] == "/ui/"


def test_me_and_plan_endpoints(api):
    c, h = api
    me = c.get("/v1/me", headers=h["lead"]).json()
    assert me["tenant"] == "demo" and me["roles"] == ["planner"] and me["mode"] == "execute"
    assert c.get("/v1/plan", headers=h["viewer"]).status_code == 404  # no dataset yet
    c.post("/v1/dataset", json={"plan": sample_json()}, headers=h["lead"])
    p = c.get("/v1/plan", headers=h["viewer"]).json()
    assert p["result"]["verified"] and p["plan_start"] == "2026-10-26" and p["gates"]["ORD-1005"]["status"] == "blocked"
    assert c.get("/v1/plan?profile=nope", headers=h["viewer"]).status_code == 422
    assert c.get("/v1/plan", headers=h["viewer"]).json()["result"]["totals"] == p["result"]["totals"]  # cached, stable


def test_console_is_served_and_demo_keys_are_off_by_default(api):
    c, _ = api
    r = c.get("/ui/console/")
    assert r.status_code == 200 and "SiliconFlow Console" in r.text
    assert c.get("/ui/console/js/main.js").status_code == 200
    assert c.get("/v1/demo/users").status_code in (404, 405)  # route does not exist unless SILICONFLOW_DEMO=1


def test_demo_mode_seeds_users_and_data_idempotently(tmp_path):
    from siliconflow.demo import demo_users, seed
    from siliconflow.services.platform import Platform
    pf = Platform(f"sqlite:///{tmp_path / 'd.db'}")
    app = create_app(pf, demo=True)
    c = TestClient(app)
    users = c.get("/v1/demo/users").json()
    assert {u["roles"][0] for u in users} >= {"planner", "finance", "viewer"} and users == demo_users()
    h = {"Authorization": "Bearer " + users[0]["api_key"]}
    assert c.get("/v1/dataset", headers=h).json()["version"] == 1
    seed(pf)  # second seeding changes nothing
    assert c.get("/v1/snapshots", headers=h).json()[0]["version"] == 1 and len(c.get("/v1/audit", headers=h, params={"limit": 1000}).json()) >= 9
    pf.close()
