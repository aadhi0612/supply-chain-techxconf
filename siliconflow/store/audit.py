"""Tamper-evident, per-tenant hash-chained audit log (append-only)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import AuditHead, AuditLog

GENESIS = "0" * 64


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev: str, seq: int, ts: str, actor: str, action: str, et: str, eid: str, data: dict) -> str:
    return hashlib.sha256(_canon([prev, seq, ts, actor, action, et, eid, data]).encode()).hexdigest()


def row_digest(r: AuditLog) -> str:
    """Recompute a row's hash from its own content and prev_hash (never trusts the stored `hash`)."""
    ts = r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)
    return _hash(r.prev_hash, r.seq, ts.isoformat(), r.actor, r.action, r.entity_type, r.entity_id, r.data)


def append(s: Session, tenant: str, actor: str, action: str, entity_type: str, entity_id: str, data: dict | None = None) -> AuditLog:
    """Must be called inside the caller's transaction so the audit row commits atomically with the change."""
    head = s.execute(select(AuditHead).where(AuditHead.tenant_id == tenant).with_for_update()).scalar_one_or_none()
    if head is None:
        head = AuditHead(tenant_id=tenant, last_seq=0, last_hash=GENESIS)
        s.add(head)
        s.flush()
    data = json.loads(_canon(data or {}))
    ts = datetime.now(timezone.utc).replace(microsecond=0)
    seq = head.last_seq + 1
    h = _hash(head.last_hash, seq, ts.isoformat(), actor, action, entity_type, entity_id, data)
    row = AuditLog(tenant_id=tenant, seq=seq, ts=ts, actor=actor, action=action, entity_type=entity_type,
                   entity_id=entity_id, data=data, prev_hash=head.last_hash, hash=h)
    s.add(row)
    head.last_seq, head.last_hash = seq, h
    return row


def verify_chain(s: Session, tenant: str) -> dict:
    prev, n = GENESIS, 0
    for r in s.execute(select(AuditLog).where(AuditLog.tenant_id == tenant).order_by(AuditLog.seq)).scalars():
        ts = r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)
        n += 1
        if r.seq != n or r.prev_hash != prev or r.hash != _hash(prev, r.seq, ts.isoformat(), r.actor, r.action, r.entity_type, r.entity_id, r.data):
            return {"ok": False, "entries": n, "broken_at": r.seq}
        prev = r.hash
    head = s.get(AuditHead, tenant)
    if head and (head.last_seq != n or head.last_hash != prev):
        return {"ok": False, "entries": n, "broken_at": n + 1, "detail": "head mismatch (truncation?)"}
    return {"ok": True, "entries": n, "head": prev}
