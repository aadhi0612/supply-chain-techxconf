"""Command line: serve the API, bootstrap a tenant, or run the end-to-end demo incident."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time


NOTICE = """Subject: Reserved capacity adjustment - OSAT-A
Dear partner, following a tool-qualification outage OSAT-A will reduce your reserved 2.5D-L packaging capacity (PKG-A) by 25% in weeks 4-9.
Separately, substrate lot LOT-S-77 is on quality hold pending failure analysis.
"""


def _hr(t: str):
    print(f"\n{'=' * 8} {t} {'=' * (70 - len(t))}")


def cmd_serve(a):
    import os

    import uvicorn
    if a.demo:
        os.environ["SILICONFLOW_DEMO"] = "1"
        os.environ.setdefault("SILICONFLOW_MODE", "execute")
        print("DEMO MODE: sample data seeded, public API keys sf_demo_<name> enabled. Never use outside local development.")
    uvicorn.run("siliconflow.api.main:app", host=a.host, port=a.port)


def cmd_init(a):
    from .api.app import build_platform
    pf = build_platform()
    uid, key = pf.create_user(a.tenant, a.name, a.roles.split(","))
    print(json.dumps({"user_id": uid, "tenant": a.tenant, "api_key": key, "note": "store this key now; only its hash is kept"}, indent=2))


def cmd_demo(a):
    from .sample_data import build_sample
    from .services.actions import OutboxAdapter
    from .services.platform import Platform
    from .services.policy import ExecutionPolicy
    from .engine.planner import run_plan

    out = tempfile.mkdtemp(prefix="sf_outbox_")
    pf = Platform(a.db, ExecutionPolicy(mode="execute"), OutboxAdapter(out))
    T = "demo"
    users = {r: pf.authenticate(pf.create_user(T, f"{r.title()} User", [r])[1]) for r in ("planner", "procurement", "finance", "sales", "compliance", "quality")}
    lead = pf.authenticate(pf.create_user(T, "Supply Lead", ["planner"])[1])
    planner = users["planner"]

    _hr("1. Import dataset")
    r = pf.import_plan(lead, build_sample())
    print(f"snapshot v{r['version']} | data quality:", json.dumps(r["data_quality"]["metrics"]))
    _, plan0 = pf.current(T)
    base = run_plan(plan0, "no_spend")
    print(f"baseline plan (pre-incident): on-time {int(base.totals['on_time_units'])} / late {int(base.totals['late_units'])} units; "
          f"verified={base.verified}; compliance-held orders: {[o.order_id for o in base.orders if o.status == 'blocked']}")

    _hr("2. Supplier notice arrives (unstructured)")
    d = pf.ingest_document(planner, "email", NOTICE)
    for pr in d["proposals"]:
        print(f"  proposal {pr['id']} {pr['event']['type']} conf={pr['confidence']} ambiguities={pr['ambiguities']}")
        print(f"     evidence: {pr['quotes'][0][:110]}")
    t0 = time.time()
    res = pf.confirm_proposals(planner, [p["id"] for p in d["proposals"]], acknowledge_ambiguities=True, note="reviewed against portal")
    print(f"  confirmed -> snapshot v{res['snapshot_version']}, incident {res['incident_id']} analysed in {time.time() - t0:.1f}s")
    iid = res["incident_id"]

    inc = pf.get_incident(T, iid)
    A = inc["analysis"]
    _hr("3. Impact if nobody acts (pre-incident plan under the new facts)")
    ref = A["status_quo"]
    for o in ref["impact"]["orders"]:
        print(f"  {o['order_id']:9} {o['dependency']:8} on-time {o['on_time_before']:>5} -> {o['on_time_after']:>5} | unfulfilled {o['unfulfilled_before']:>5} -> {o['unfulfilled_after']:>5} | exposure {o['exposure_delta']:>14,.0f}")
    print(f"  => {ref['impact']['units_on_time_lost']} on-time units lost, exposure delta ${ref['impact']['exposure_delta']:,.0f}")
    if A["binding"]:
        print("  binding constraints:", [(p["label"], p["recovered_on_time_units"]) for p in A["binding"]["pools"]])

    _hr("4. Recovery options")
    for o in A["options"]:
        rng = {r["scenario"]: r["on_time_units"] for r in o["scenario_range"]}
        print(f"  #{o['rank']} {o['title']:36} on-time {int(o['result']['totals']['on_time_units']):>6} | extra spend ${o['incremental_cost_vs_no_spend']:>12,.0f} | "
              f"best/base/worst {rng['best']}/{rng['base']}/{rng['worst']} | levers {[l['lever_id'] for l in o['result']['levers']]} {'<- recommended' if o['recommended'] else ''}")
    rec = next(o for o in A["options"] if o["recommended"])
    for line in rec["explanations"]:
        print("   -", line)

    _hr("5. Select, approve (four-eyes), execute")
    pf.select_option(lead, iid, rec["id"])
    roles = [x["role"] for x in pf.get_incident(T, iid, False)["required_approvals"]]
    print("  required approvals:", roles)
    for role in roles:
        u = users[role] if role != "planner" else planner
        if role == "planner":
            u = pf.authenticate(pf.create_user(T, "Second Planner", ["planner"])[1])
        pf.decide(u, iid, "approve", role, "ok")
    ex = pf.execute(lead, iid)
    print(f"  incident state: {ex['state']}")
    for ac in ex["actions"]:
        print(f"   {ac['type']:22} -> {ac['target']:12} {ac['status']}")

    _hr("6. Close the loop: supplier answers")
    for ac in [x for x in ex["actions"] if x["type"] == "SUPPLIER_REQUEST"]:
        r2 = pf.confirm_action(lead, ac["id"], "confirmed", note="confirmed by supplier portal")
        print(f"  {ac['payload']['lever']} confirmed -> snapshot v{r2['snapshot_version']}")
    pf.record_outcome(lead, "supplier_commitment", "SubstrateCo2", {"qty": 1500, "week": 4}, {"qty": 1500, "week": 4}, iid)

    _hr("7. Supplier portal confirms the cut -> resolve the open conflicts")
    open_c = pf.list_conflicts(T)
    print(f"  {len(open_c)} open conflict(s) from the lower-authority email source; accepting the (already applied) values")
    pf.resolve_conflicts(lead, "proposed", ids=[c["id"] for c in open_c], note="portal confirmed")

    _hr("8. Governance")
    print("  metrics:", json.dumps(pf.metrics(T), default=str)[:600])
    print("  audit chain:", pf.verify_audit(T))
    print(f"  outbox files in {out}")
    pf.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="siliconflow")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--demo", action="store_true", help="dev only: seed sample data and fixed demo users (sf_demo_planner, ...)")
    s.set_defaults(fn=cmd_serve)
    i = sub.add_parser("init-user", help="create a user and print its API key")
    i.add_argument("--tenant", required=True)
    i.add_argument("--name", required=True)
    i.add_argument("--roles", required=True, help="comma separated: planner,procurement,finance,quality,sales,compliance,admin,viewer")
    i.set_defaults(fn=cmd_init)
    d = sub.add_parser("demo", help="run the packaging-cut + substrate-hold incident end to end")
    d.add_argument("--db", default="sqlite://")
    d.set_defaults(fn=cmd_demo)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
