"""SQLAlchemy tables. SQLite by default; any SQLAlchemy URL (e.g. PostgreSQL) works unchanged.

Every row is tenant-scoped. Bodies that are never queried by column are stored as JSON.
Schema is created with ``create_all`` (no migration tool is bundled; add Alembic before the first
production schema change).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, TypeDecorator, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TZDateTime(TypeDecorator):
    """Always store UTC and always return tz-aware datetimes (SQLite drops tzinfo)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed")
        return value.astimezone(timezone.utc).replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


def _col_dt(**kw):
    return mapped_column(TZDateTime(), default=now, **kw)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    roles: Mapped[list] = mapped_column(JSON)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _col_dt()


class Snapshot(Base):
    __tablename__ = "snapshots"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="")
    event_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = _col_dt()


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32), default="notice")
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    uploaded_by: Mapped[str] = mapped_column(String(64), default="")
    received_at: Mapped[datetime] = _col_dt()
    __table_args__ = (UniqueConstraint("tenant_id", "sha256", name="uq_doc_hash"),)


class ProposedEvent(Base):
    __tablename__ = "proposed_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event: Mapped[dict] = mapped_column(JSON)
    quotes: Mapped[list] = mapped_column(JSON)
    ambiguities: Mapped[list] = mapped_column(JSON)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|confirmed|rejected
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    incident_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _col_dt()


class EventRecord(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    event: Mapped[dict] = mapped_column(JSON)
    snapshot_before: Mapped[int] = mapped_column(Integer)
    snapshot_after: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict] = mapped_column(JSON)
    applied_by: Mapped[str] = mapped_column(String(64), default="")
    applied_at: Mapped[datetime] = _col_dt()


class Conflict(Base):
    __tablename__ = "conflicts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    body: Mapped[dict] = mapped_column(JSON)
    event_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|resolved
    resolution: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    created_at: Mapped[datetime] = _col_dt()


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(300))
    state: Mapped[str] = mapped_column(String(24), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_ids: Mapped[list] = mapped_column(JSON)
    snapshot_before: Mapped[int] = mapped_column(Integer)
    snapshot_after: Mapped[int] = mapped_column(Integer)
    detected_at: Mapped[datetime] = mapped_column(TZDateTime())
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # options, impact, binding, explanations
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_option: Mapped[str | None] = mapped_column(String(64), nullable=True)
    selected_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_hash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    required_approvals: Mapped[list] = mapped_column(JSON, default=list)
    plan_ready_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _col_dt()
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=now, onupdate=now)
    __mapper_args__ = {"version_id_col": row_version}


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    incident_id: Mapped[str] = mapped_column(String(64), ForeignKey("incidents.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(16))  # approve|reject
    comment: Mapped[str] = mapped_column(Text, default="")
    option_id: Mapped[str] = mapped_column(String(64))
    plan_hash: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = _col_dt()


class Action(Base):
    __tablename__ = "actions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    incident_id: Mapped[str] = mapped_column(String(64), ForeignKey("incidents.id"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    target: Mapped[str] = mapped_column(String(120), default="")
    external: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict] = mapped_column(JSON)
    lever_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="drafted")  # drafted|sent|failed|confirmed|declined|reverted
    idempotency_key: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    reverts_action_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _col_dt()
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_action_idem"),)


class Outcome(Base):
    __tablename__ = "outcomes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    incident_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))  # supplier_commitment|delivery|yield
    subject: Mapped[str] = mapped_column(String(120))
    planned: Mapped[dict] = mapped_column(JSON)
    actual: Mapped[dict] = mapped_column(JSON)
    recorded_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = _col_dt()


class AuditHead(Base):
    __tablename__ = "audit_heads"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer, default=0)
    last_hash: Mapped[str] = mapped_column(String(64), default="0" * 64)


class AuditLog(Base):
    __tablename__ = "audit_log"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime())
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (Index("ix_audit_entity", "tenant_id", "entity_type", "entity_id"),)


def make_engine(url: str = "sqlite:///siliconflow.db"):
    kw = {}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" in url or url == "sqlite://":
            kw["poolclass"] = StaticPool
    eng = create_engine(url, future=True, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi, _):  # pragma: no cover
            cur = dbapi.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if ":memory:" not in url and url != "sqlite://":
                cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    Base.metadata.create_all(eng)
    return eng


def session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False, future=True)
