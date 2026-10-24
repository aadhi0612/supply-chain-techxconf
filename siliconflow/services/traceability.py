"""Traceability: per-record history, ledger anchoring of the audit chain, inclusion proofs, and the demo-only tamper lab.

The audit chain stays the system of record. Anchoring publishes only a Merkle root (a digest) to a ledger kept outside the
platform database, so history can be checked by someone who does not trust the platform operator. Verification always
recomputes hashes from row content and compares against the root *fetched from the ledger*, never against stored copies.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..store import audit as audit_log
from ..store import merkle
from ..store.db import Action, Anchor, Approval, AuditHead, AuditLog, EventRecord, Incident, Snapshot, User
from ..store.ledger import LedgerUnavailable
from .platform import Conflicting, Invalid, NotFound, Principal, ServiceError


class Unavailable(ServiceError):
    status = 503


# kind -> (collection in PlanInput, fact-path prefix used by events.py)
KINDS = {"order": ("orders", "order"), "commitment": ("commitments", "commitment"), "resource": ("resources", "capacity"),
         "lot": ("inventory", "lot"), "route": ("routes", "route"), "lane": ("lanes", "lane"), "license": ("licenses", "license")}


def _subject(ev: dict) -> tuple[str, str] | None:
    t = ev.get("type")
    if t == "capacity_change":
        return "resource", ev["resource_id"]
    if t == "commitment_change":
        return "commitment", ev["commitment_id"]
    if t == "commitment_add":
        return "commitment", ev["commitment"]["id"]
    if t == "lot_status":
        return "lot", ev["lot_id"]
    if t in ("yield_change", "route_qualification"):
        return "route", ev["route_id"]
    if t == "demand_change":
        return "order", ev["order_id"]
    if t == "logistics_delay":
        return "lane", ev["destination"]
    if t == "license_add":
        return "license", ev["license"]["id"]
    return None


def _entity_key(kind: str, row: dict) -> str:
    return row.get("destination") if kind == "lane" else row.get("id")


# ---------------------------------------------------------------------------------------------- anchoring
def _audit_rows(s, tenant: str, lo: int, hi: int) -> list[AuditLog]:
    return list(s.execute(select(AuditLog).where(AuditLog.tenant_id == tenant, AuditLog.seq >= lo, AuditLog.seq <= hi).order_by(AuditLog.seq)).scalars())


def anchor(pf, p: Principal) -> dict:
    """Publish the Merkle root of every audit row not yet anchored. Runs outside the audit transaction so a ledger outage never blocks work."""
    pf._need_write(p)
    tenant = p.tenant_id
    with pf.tx() as s:
        chain = audit_log.verify_chain(s, tenant)
        if not chain["ok"]:
            raise Conflicting(f"audit chain is broken at #{chain.get('broken_at')}; refusing to anchor tampered history")
        head = s.get(AuditHead, tenant)
        last = s.execute(select(func.max(Anchor.seq_to)).where(Anchor.tenant_id == tenant)).scalar() or 0
        if head is None or head.last_seq <= last:
            return {"anchored": False, "reason": "nothing new to anchor", "anchored_through": last}
        lo, hi = last + 1, head.last_seq
        hashes = [r.hash for r in _audit_rows(s, tenant, lo, hi)]
        next_id = (s.execute(select(func.max(Anchor.id)).where(Anchor.tenant_id == tenant)).scalar() or 0) + 1
    root = merkle.root(hashes)
    try:
        block = pf.ledger.publish(tenant, root, lo, hi)
    except LedgerUnavailable as e:
        raise Unavailable(f"{e}; nothing was anchored, work continues and the next anchor will cover these rows") from e
    try:
        with pf.tx() as s:
            s.add(Anchor(tenant_id=tenant, id=next_id, seq_from=lo, seq_to=hi, root=root, ledger=pf.ledger.name, ledger_index=block["index"],
                         ledger_ts=block["ts"], created_by=p.user_id))
    except IntegrityError:
        raise Conflicting("another anchor was recorded concurrently; retry")
    return {"anchored": True, "anchor_id": next_id, "seq_from": lo, "seq_to": hi, "entries": len(hashes), "root": root,
            "ledger": pf.ledger.name, "ledger_index": block["index"], "ledger_ts": block["ts"]}


def list_anchors(pf, tenant: str) -> list[dict]:
    with pf.tx() as s:
        return [{"id": a.id, "seq_from": a.seq_from, "seq_to": a.seq_to, "entries": a.seq_to - a.seq_from + 1, "root": a.root, "ledger": a.ledger,
                 "ledger_index": a.ledger_index, "ledger_ts": a.ledger_ts, "created_by": a.created_by}
                for a in s.execute(select(Anchor).where(Anchor.tenant_id == tenant).order_by(Anchor.id.desc())).scalars()]


def verify(pf, tenant: str) -> dict:
    """Chain check plus, for every anchor, recompute the root from row *content* and compare with the ledger's copy."""
    with pf.tx() as s:
        chain = audit_log.verify_chain(s, tenant)
        anchors = list(s.execute(select(Anchor).where(Anchor.tenant_id == tenant).order_by(Anchor.id)).scalars())
        head = s.get(AuditHead, tenant)
        checks = []
        for a in anchors:
            rows = _audit_rows(s, tenant, a.seq_from, a.seq_to)
            checks.append((a, [audit_log.row_digest(r) for r in rows], len(rows) == a.seq_to - a.seq_from + 1))
        head_seq = head.last_seq if head else 0
    failures, verified, reachable = [], 0, True
    ledger_state = {"name": pf.ledger.name, "reachable": True}
    for a, leaves, complete in checks:
        if not complete:
            failures.append({"anchor_id": a.id, "seq_from": a.seq_from, "seq_to": a.seq_to, "why": "audit rows in this range are missing"})
            continue
        try:
            block = pf.ledger.get(a.ledger_index)
        except LedgerUnavailable:
            reachable = False
            continue
        if block is None or block["tenant"] != tenant or (block["seq_from"], block["seq_to"]) != (a.seq_from, a.seq_to):
            failures.append({"anchor_id": a.id, "seq_from": a.seq_from, "seq_to": a.seq_to, "why": "the ledger has no matching block for this anchor"})
        elif block["root"] != a.root:
            failures.append({"anchor_id": a.id, "seq_from": a.seq_from, "seq_to": a.seq_to, "why": "the stored anchor differs from the ledger's copy"})
        elif merkle.root(leaves) != block["root"]:
            failures.append({"anchor_id": a.id, "seq_from": a.seq_from, "seq_to": a.seq_to,
                             "why": f"audit rows #{a.seq_from}-#{a.seq_to} no longer match the root published to the ledger (history was changed after anchoring)"})
        else:
            verified += 1
    try:
        lv = pf.ledger.verify()
        ledger_state.update({"ok": lv["ok"], "blocks": lv["blocks"]})
        if not lv["ok"]:
            failures.append({"anchor_id": None, "why": f"the ledger's own chain is broken at block {lv['broken_at']}"})
    except LedgerUnavailable:
        reachable = False
    ledger_state["reachable"] = reachable
    through = max((a.seq_to for a in anchors), default=0)
    unanchored = max(0, head_seq - through)
    out = dict(chain)
    out["chain_ok"] = chain["ok"]
    out["ok"] = chain["ok"] and not failures
    out["anchors"] = {"count": len(anchors), "verified": verified, "anchored_through": through, "unanchored_entries": unanchored,
                      "unanchored_since": through + 1 if unanchored else None, "failures": failures, "ledger_reachable": reachable}
    out["ledger"] = ledger_state
    return out


def proof(pf, tenant: str, seq: int) -> dict:
    with pf.tx() as s:
        a = s.execute(select(Anchor).where(Anchor.tenant_id == tenant, Anchor.seq_from <= seq, Anchor.seq_to >= seq)).scalar_one_or_none()
        row = s.execute(select(AuditLog).where(AuditLog.tenant_id == tenant, AuditLog.seq == seq)).scalar_one_or_none()
        if row is None:
            raise NotFound(f"no audit entry #{seq}")
        entry = {"seq": row.seq, "ts": row.ts.isoformat(), "actor": row.actor, "action": row.action, "entity_type": row.entity_type,
                 "entity_id": row.entity_id, "data": row.data, "stored_hash": row.hash}
        if a is None:
            return {"seq": seq, "anchored": False, "entry": entry, "valid": None,
                    "reason": "this entry has not been anchored yet; anchor the audit log first"}
        rows = _audit_rows(s, tenant, a.seq_from, a.seq_to)
        leaves = [audit_log.row_digest(r) for r in rows]
        anchor_info = {"id": a.id, "seq_from": a.seq_from, "seq_to": a.seq_to, "ledger": a.ledger, "ledger_index": a.ledger_index}
    if len(leaves) != anchor_info["seq_to"] - anchor_info["seq_from"] + 1:
        return {"seq": seq, "anchored": True, "entry": entry, "anchor": anchor_info, "valid": False, "reason": "audit rows in the anchored range are missing"}
    idx = seq - anchor_info["seq_from"]
    leaf = leaves[idx]
    path = merkle.proof(leaves, idx)
    try:
        block = pf.ledger.get(anchor_info["ledger_index"])
    except LedgerUnavailable as e:
        raise Unavailable(f"{e}; cannot check this proof against the ledger right now") from e
    ledger_root = block["root"] if block else None
    computed = merkle.root(leaves)
    valid = bool(block) and leaf == entry["stored_hash"] and merkle.verify_path(leaf, path, ledger_root) and computed == ledger_root
    if valid:
        reason = "the entry recomputed from its content hashes into the root the ledger published"
    elif block is None:
        reason = "the ledger has no block for this anchor"
    elif leaf != entry["stored_hash"]:
        reason = "the entry's content no longer matches the hash stored for it (edited after the fact)"
    else:
        reason = "the entry or its neighbours no longer reproduce the root the ledger published (history was rewritten after anchoring)"
    return {"seq": seq, "anchored": True, "entry": entry, "anchor": anchor_info, "recomputed_leaf": leaf, "path": path, "computed_root": computed,
            "ledger_root": ledger_root, "ledger_ts": block["ts"] if block else None, "valid": valid, "reason": reason}


# ---------------------------------------------------------------------------------------------- record history
def entities(pf, tenant: str) -> dict:
    with pf.tx() as s:
        snap = pf._latest(s, tenant)
        if snap is None:
            raise NotFound("no dataset imported yet")
        plan = snap.plan
    return {kind: sorted(filter(None, (_entity_key(kind, r) for r in plan.get(coll, [])))) for kind, (coll, _) in KINDS.items()}


def trace(pf, tenant: str, kind: str, entity_id: str) -> dict:
    if kind not in KINDS:
        raise Invalid(f"unknown kind {kind!r}; use one of {sorted(KINDS)}")
    coll, ppre = KINDS[kind]
    with pf.tx() as s:
        snaps = list(s.execute(select(Snapshot).where(Snapshot.tenant_id == tenant).order_by(Snapshot.version)).scalars())
        if not snaps:
            raise NotFound("no dataset imported yet")

        def find(plan: dict):
            return next((r for r in plan.get(coll, []) if _entity_key(kind, r) == entity_id), None)
        current = find(snaps[-1].plan)
        first = find(snaps[0].plan)
        if current is None and first is None:
            raise NotFound(f"{kind} {entity_id!r} not found")
        names = {u.id: u.name for u in s.execute(select(User).where(User.tenant_id == tenant)).scalars()}
        anchors = list(s.execute(select(Anchor).where(Anchor.tenant_id == tenant)).scalars())
        through = max((a.seq_to for a in anchors), default=0)
        audit_by_event = {r.entity_id: r for r in s.execute(select(AuditLog).where(AuditLog.tenant_id == tenant, AuditLog.action == "event.apply")).scalars()}
        records = list(s.execute(select(EventRecord).where(EventRecord.tenant_id == tenant).order_by(EventRecord.applied_at, EventRecord.id)).scalars())
        incidents = list(s.execute(select(Incident).where(Incident.tenant_id == tenant)).scalars())
        approvals = list(s.execute(select(Approval).where(Approval.tenant_id == tenant).order_by(Approval.created_at)).scalars())
        actions = list(s.execute(select(Action).where(Action.tenant_id == tenant)).scalars())
        superseded_by: dict[str, str] = {r.event["supersedes"]: r.id for r in records if r.event.get("supersedes")}
        imp = s.execute(select(AuditLog).where(AuditLog.tenant_id == tenant, AuditLog.action == "snapshot.import").order_by(AuditLog.seq)).scalars().first()
        history = []
        for r in records:
            if _subject(r.event) != (kind, entity_id):
                continue
            ev = r.event
            mine = [c for c in r.result.get("changes", []) if str(c["path"]).startswith(f"{ppre}[{entity_id}")]
            a = audit_by_event.get(r.id)
            inc = next((i for i in incidents if r.id in (i.event_ids or [])), None)
            incident = None
            if inc:
                incident = {"id": inc.id, "title": inc.title, "state": inc.state, "selected_option": inc.selected_option, "plan_hash": inc.plan_hash,
                            "selected_by": names.get(inc.selected_by or ""),
                            "approvals": [{"by": names.get(x.user_id, x.user_id), "role": x.role, "decision": x.decision, "at": x.created_at.isoformat(),
                                           "comment": x.comment, "plan_hash": x.plan_hash} for x in approvals if x.incident_id == inc.id],
                            "actions": [{"type": x.type, "target": x.target, "status": x.status} for x in actions if x.incident_id == inc.id]}
            history.append({
                "event_id": r.id, "type": ev["type"], "applied_at": r.applied_at.isoformat(), "observed_at": ev.get("observed_at"),
                "actor": r.applied_by, "actor_name": names.get(r.applied_by, r.applied_by), "source": ev.get("source"), "confidence": ev.get("confidence"),
                "method": ev.get("method"), "note": ev.get("note"), "forced": bool(ev.get("force")), "supersedes": ev.get("supersedes"),
                "superseded_by": superseded_by.get(r.id), "changes": mine or r.result.get("changes", []),
                "snapshot_after": r.snapshot_after, "audit_seq": a.seq if a else None, "audit_hash": a.hash if a else None,
                "anchored": bool(a and a.seq <= through), "event": ev, "incident": incident})
    baseline = None
    if first is not None:
        baseline = {"snapshot": snaps[0].version, "at": snaps[0].created_at.isoformat(), "imported_by": names.get(snaps[0].created_by, snaps[0].created_by),
                    "reason": snaps[0].reason, "value": {k: v for k, v in first.items() if k != "provenance"}, "provenance": first.get("provenance"),
                    "audit_seq": imp.seq if imp else None, "anchored": bool(imp and imp.seq <= through)}
    return {"kind": kind, "id": entity_id, "current": current and {k: v for k, v in current.items() if k != "provenance"},
            "current_provenance": current.get("provenance") if current else None, "baseline": baseline, "history": history,
            "anchored_through": through}


# ---------------------------------------------------------------------------------------------- demo tamper lab
def demo_tamper(pf, tenant: str, seq: int, rebuild: bool, actor: str = "mallory") -> dict:
    """DEV ONLY (demo mode). Edit one audit row in place. With rebuild, also recompute every later hash and the head, i.e. an insider
    rewriting history so the chain still verifies. Originals are kept so `demo_untamper` can restore them."""
    with pf.tx() as s:
        head = s.get(AuditHead, tenant)
        row = s.execute(select(AuditLog).where(AuditLog.tenant_id == tenant, AuditLog.seq == seq)).scalar_one_or_none()
        if head is None or row is None:
            raise NotFound(f"no audit entry #{seq}")
        saved = pf._tamper_backup.setdefault(tenant, {"rows": {}, "head": (head.last_seq, head.last_hash), "rebuilt": False})
        saved["rebuilt"] = saved["rebuilt"] or rebuild
        rows = _audit_rows(s, tenant, seq, head.last_seq)
        for r in rows:
            saved["rows"].setdefault(r.seq, (r.actor, r.prev_hash, r.hash))
        row.actor = actor
        if rebuild:
            prev = row.prev_hash
            for r in rows:
                r.prev_hash = prev
                r.hash = audit_log.row_digest(r)
                prev = r.hash
            head.last_hash = prev
    return {"tampered_seq": seq, "actor_now": actor, "chain_rebuilt": rebuild}


def demo_untamper(pf, tenant: str) -> dict:
    saved = pf._tamper_backup.get(tenant)
    if not saved:
        return {"restored": 0}
    with pf.tx() as s:
        head = s.get(AuditHead, tenant)
        if saved["rebuilt"] and head.last_seq != saved["head"][0]:
            raise Conflicting("new audit entries were written after the history was rewritten; restore is no longer possible")
        for r in _audit_rows(s, tenant, min(saved["rows"]), max(saved["rows"])):
            if r.seq in saved["rows"]:
                r.actor, r.prev_hash, r.hash = saved["rows"][r.seq]
        if saved["rebuilt"]:
            head.last_seq, head.last_hash = saved["head"]
    pf._tamper_backup.pop(tenant, None)
    return {"restored": len(saved["rows"])}
