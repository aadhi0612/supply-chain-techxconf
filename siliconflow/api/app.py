"""HTTP API. Thin: validation + auth + error mapping; all behaviour lives in services.Platform."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import Settings
from ..domain import PlanInput
from ..events import SupplyEvent
from ..ingest import extract as extract_mod
from ..ingest.importers import ImportError_, layer_tables
from ..services.actions import OutboxAdapter, WebhookAdapter
from ..services.platform import Platform, Principal, ServiceError
from ..services.policy import ExecutionPolicy
from ..store.ledger import LocalLedger


class ImportBody(BaseModel):
    plan: PlanInput
    reason: str = "dataset import"


class EventsBody(BaseModel):
    events: list[SupplyEvent] = Field(min_length=1)
    title: str | None = None
    create_incident: bool = True


class DocumentBody(BaseModel):
    source: str
    text: str = Field(min_length=1, max_length=200_000)
    kind: str = "notice"


class ConfirmProposalBody(BaseModel):
    edits: dict | None = None
    acknowledge_ambiguities: bool = False
    note: str = ""


class ConfirmBatchBody(BaseModel):
    ids: list[str] = Field(min_length=1)
    acknowledge_ambiguities: bool = False
    note: str = ""
    mode: str = Field("sync", pattern="^(sync|async)$")


class NoteBody(BaseModel):
    note: str = Field(min_length=1)


class SelectBody(BaseModel):
    option_id: str


class DecisionBody(BaseModel):
    decision: str
    role: str
    comment: str = ""


class ConfirmActionBody(BaseModel):
    outcome: str
    qty: int | None = None
    date: str | None = None
    note: str = ""


class ReasonBody(BaseModel):
    reason: str = Field(min_length=1)
    mode: str = Field("sync", pattern="^(sync|async)$")


class ResolveBody(BaseModel):
    accept: str
    note: str = ""


class BulkResolveBody(BaseModel):
    accept: str
    ids: list[str] | None = None
    event_id: str | None = None
    note: str = ""


class TamperBody(BaseModel):
    seq: int = Field(ge=1)
    rebuild: bool = False


class LedgerSwitchBody(BaseModel):
    available: bool


class OutcomeBody(BaseModel):
    kind: str
    subject: str
    planned: dict
    actual: dict
    incident_id: str | None = None


def build_platform(settings: Settings | None = None) -> Platform:
    st = settings or Settings.from_env()
    adapter = WebhookAdapter(st.webhook_url, st.webhook_token) if st.webhook_url else OutboxAdapter(st.outbox_dir)
    extractors = [extract_mod.RuleBasedExtractor()]
    if st.use_llm:
        try:
            extractors.append(extract_mod.ClaudeExtractor())
        except Exception:  # optional dependency / credentials problem must not stop the service
            pass
    return Platform(st.db_url, ExecutionPolicy(mode=st.mode), adapter, extractors, ledger=LocalLedger(st.ledger_path))


def create_app(platform: Platform | None = None, demo: bool | None = None) -> FastAPI:
    st = Settings.from_env()
    pf = platform or build_platform(st)
    demo = st.demo if demo is None else demo
    if demo:
        from ..demo import seed
        seed(pf)
    app = FastAPI(title="SiliconFlow", version="0.1.0",
                  description="Constraint-aware semiconductor supply allocation and recovery control plane.")
    app.state.platform = pf

    @app.exception_handler(ServiceError)
    async def _svc(_: Request, exc: ServiceError):
        return JSONResponse(status_code=exc.status, content={"error": type(exc).__name__, "detail": str(exc)})

    @app.exception_handler(ImportError_)
    async def _imp(_: Request, exc: ImportError_):
        return JSONResponse(status_code=422, content={"error": "ImportError", "detail": exc.errors})

    def principal(authorization: Annotated[str | None, Header()] = None, x_api_key: Annotated[str | None, Header()] = None) -> Principal:
        key = x_api_key or (authorization[7:] if authorization and authorization.lower().startswith("bearer ") else None)
        if not key:
            raise HTTPException(401, "missing API key (Authorization: Bearer <key> or X-API-Key)")
        try:
            return pf.authenticate(key)
        except ServiceError as e:
            raise HTTPException(401, str(e))

    P = Annotated[Principal, Depends(principal)]

    ui_dir = Path(__file__).resolve().parents[2] / "ui"
    if (ui_dir / "index.html").exists():
        app.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")

        @app.get("/", include_in_schema=False)
        def root():
            return RedirectResponse("/ui/")

    if demo:
        from ..demo import demo_users

        @app.get("/v1/demo/users", include_in_schema=False)
        def demo_user_list():
            """DEV ONLY (SILICONFLOW_DEMO=1): lets the console sign in as any role."""
            return demo_users()

        @app.post("/v1/demo/tamper", include_in_schema=False)
        def demo_tamper(body: TamperBody, p: P):
            """DEV ONLY: edit one audit row in place (rebuild=true also recomputes later hashes, like an insider rewriting history)."""
            from ..services import traceability
            pf._need_write(p)
            return traceability.demo_tamper(pf, p.tenant_id, body.seq, body.rebuild)

        @app.post("/v1/demo/untamper", include_in_schema=False)
        def demo_untamper(p: P):
            from ..services import traceability
            pf._need_write(p)
            return traceability.demo_untamper(pf, p.tenant_id)

        @app.post("/v1/demo/ledger", include_in_schema=False)
        def demo_ledger(body: LedgerSwitchBody, p: P):
            """DEV ONLY: simulate a ledger outage to show that work continues and anchoring catches up."""
            pf._need_write(p)
            if not hasattr(pf.ledger, "available"):
                raise HTTPException(400, "this ledger adapter has no outage switch")
            pf.ledger.available = body.available
            return {"ledger_available": pf.ledger.available}

    @app.get("/healthz")
    def health():
        return {"status": "ok", "mode": pf.policy.mode, "demo": demo, "time": datetime.now(timezone.utc).isoformat()}

    @app.get("/v1/me")
    def me(p: P):
        return {"user_id": p.user_id, "tenant": p.tenant_id, "roles": sorted(p.roles), "mode": pf.policy.mode}

    @app.get("/v1/plan")
    def plan(p: P, profile: str = Query("no_spend", pattern="^(no_spend|balanced|max_fulfilment|robust)$")):
        return pf.current_plan(p.tenant_id, profile)

    # ---- dataset
    @app.post("/v1/dataset", status_code=201)
    def import_dataset(body: ImportBody, p: P):
        return pf.import_plan(p, body.plan, body.reason)

    @app.post("/v1/dataset/tables", status_code=201)
    async def import_tables(p: P, orders: UploadFile | None = File(None), commitments: UploadFile | None = File(None),
                            inventory: UploadFile | None = File(None), capacity: UploadFile | None = File(None)):
        """Replace operational tables (CSV) on top of the current master data."""
        tables = {}
        for name, f in (("orders", orders), ("commitments", commitments), ("inventory", inventory), ("capacity", capacity)):
            if f is not None:
                tables[name] = (await f.read()).decode("utf-8-sig")
        if not tables:
            raise HTTPException(422, "upload at least one of orders, commitments, inventory, capacity")
        _, cur = pf.current(p.tenant_id)
        return pf.import_plan(p, layer_tables(cur, tables), "csv tables: " + ", ".join(sorted(tables)))

    @app.get("/v1/dataset")
    def current(p: P):
        v, plan = pf.current(p.tenant_id)
        return {"version": v, "plan": plan.model_dump(mode="json")}

    @app.get("/v1/dataset/quality")
    def quality(p: P):
        return pf.data_quality(p.tenant_id)

    @app.get("/v1/snapshots")
    def snapshots(p: P):
        return pf.list_snapshots(p.tenant_id)

    @app.get("/v1/snapshots/diff")
    def snapshot_diff(p: P, a: int, b: int):
        return pf.diff(pf.get_snapshot(p.tenant_id, a), pf.get_snapshot(p.tenant_id, b))

    # ---- events / documents
    @app.post("/v1/events", status_code=201)
    def events(body: EventsBody, p: P, mode: str = Query("sync", pattern="^(sync|async)$")):
        return pf.ingest_events(p, list(body.events), title=body.title, create_incident=body.create_incident, mode=mode)

    @app.post("/v1/documents", status_code=201)
    def documents(body: DocumentBody, p: P):
        return pf.ingest_document(p, body.source, body.text, body.kind)

    @app.get("/v1/proposals")
    def proposals(p: P, status: str | None = None):
        return pf.list_proposals(p.tenant_id, status)

    @app.post("/v1/proposals/confirm", status_code=201)
    def confirm_batch(body: ConfirmBatchBody, p: P):
        return pf.confirm_proposals(p, body.ids, acknowledge_ambiguities=body.acknowledge_ambiguities, note=body.note, mode=body.mode)

    @app.post("/v1/proposals/{pid}/confirm", status_code=201)
    def confirm(pid: str, body: ConfirmProposalBody, p: P):
        return pf.confirm_proposal(p, pid, edits=body.edits, acknowledge_ambiguities=body.acknowledge_ambiguities, note=body.note)

    @app.post("/v1/proposals/{pid}/reject")
    def reject(pid: str, body: NoteBody, p: P):
        return pf.reject_proposal(p, pid, body.note)

    @app.get("/v1/conflicts")
    def conflicts(p: P, status: str | None = "open"):
        return pf.list_conflicts(p.tenant_id, status)

    @app.post("/v1/conflicts/resolve")
    def resolve_bulk(body: BulkResolveBody, p: P):
        return pf.resolve_conflicts(p, body.accept, ids=body.ids, event_id=body.event_id, note=body.note)

    @app.post("/v1/conflicts/{cid}/resolve")
    def resolve(cid: str, body: ResolveBody, p: P):
        return pf.resolve_conflict(p, cid, body.accept, body.note)

    # ---- incidents
    @app.get("/v1/incidents")
    def incidents(p: P, state: str | None = None):
        return pf.list_incidents(p.tenant_id, state)

    @app.get("/v1/incidents/{iid}")
    def incident(iid: str, p: P, full: bool = True):
        return pf.get_incident(p.tenant_id, iid, full)

    @app.post("/v1/incidents/{iid}/select")
    def select(iid: str, body: SelectBody, p: P):
        return pf.select_option(p, iid, body.option_id)

    @app.post("/v1/incidents/{iid}/decisions")
    def decide(iid: str, body: DecisionBody, p: P):
        return pf.decide(p, iid, body.decision, body.role, body.comment)

    @app.post("/v1/incidents/{iid}/execute")
    def execute(iid: str, p: P):
        return pf.execute(p, iid)

    @app.post("/v1/incidents/{iid}/replan", status_code=201)
    def replan(iid: str, body: ReasonBody, p: P):
        return pf.replan(p, iid, body.reason, body.mode)

    # ---- actions
    @app.post("/v1/actions/{aid}/mark-sent")
    def mark_sent(aid: str, p: P, note: str = ""):
        return pf.mark_sent(p, aid, note)

    @app.post("/v1/actions/{aid}/confirmation")
    def confirmation(aid: str, body: ConfirmActionBody, p: P):
        return pf.confirm_action(p, aid, body.outcome, qty=body.qty, date_=body.date, note=body.note)

    @app.post("/v1/actions/{aid}/revert")
    def revert(aid: str, body: ReasonBody, p: P):
        return pf.revert_action(p, aid, body.reason)

    # ---- learning / governance
    @app.post("/v1/outcomes", status_code=201)
    def outcome(body: OutcomeBody, p: P):
        return pf.record_outcome(p, body.kind, body.subject, body.planned, body.actual, body.incident_id)

    @app.get("/v1/calibration")
    def calibration(p: P):
        return pf.calibration_report(p.tenant_id)

    @app.get("/v1/metrics")
    def metrics(p: P):
        return pf.metrics(p.tenant_id)

    @app.get("/v1/audit")
    def audit(p: P, entity_type: str | None = None, entity_id: str | None = None, limit: int = Query(200, le=1000)):
        return pf.audit(p.tenant_id, entity_type, entity_id, limit)

    @app.get("/v1/audit/verify")
    def audit_verify(p: P):
        return pf.verify_audit(p.tenant_id)

    # ---- traceability: anchoring, proofs, per-record history
    @app.post("/v1/audit/anchor")
    def audit_anchor(p: P):
        return pf.anchor_audit(p)

    @app.get("/v1/audit/anchors")
    def audit_anchors(p: P):
        return pf.list_anchors(p.tenant_id)

    @app.get("/v1/audit/proof/{seq}")
    def audit_proof(seq: int, p: P):
        return pf.audit_proof(p.tenant_id, seq)

    @app.get("/v1/trace")
    def trace_entities(p: P):
        return pf.trace_entities(p.tenant_id)

    @app.get("/v1/trace/{kind}/{entity_id}")
    def trace(kind: str, entity_id: str, p: P):
        return pf.trace(p.tenant_id, kind, entity_id)

    return app
