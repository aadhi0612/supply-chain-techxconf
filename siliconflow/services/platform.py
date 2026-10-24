"""Application service: the closed loop signal -> fact -> impact -> options -> approval -> action -> outcome.

All state transitions are explicit, audited, tenant-scoped and re-validated against the *current* snapshot.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain import PlanInput, utcnow
from ..engine import compliance
from ..engine.explain import explain_order
from ..engine.impact import compute_impact
from ..engine.graph import subject_nodes
from ..engine.planner import evaluate_under, run_plan
from ..engine.recovery import OptionSet, build_options
from ..engine.scenarios import materialize
from ..engine.verify import verify
from ..events import (
    ApplyResult, CapacityChange, CommitmentAdd, CommitmentChange, DemandChange, EventBase, EventError, LotStatusChange,
    RouteQualification, SupplyEvent, apply_event,
)
from ..ingest import extract as extract_mod
from ..ingest.validate import data_quality, validate_integrity
from ..store import audit as audit_log
from ..store.db import (
    Action, Approval, AuditLog, Conflict, Document, EventRecord, Incident, Outcome, ProposedEvent, Snapshot, User,
    make_engine, session_factory,
)
from .actions import Adapter, OutboxAdapter, build_drafts, idem_key, run_with_retry
from .policy import ROLES, WRITE_ROLES, ExecutionPolicy, required_approvals

_EVENT_ADAPTER = TypeAdapter(SupplyEvent)


class ServiceError(Exception):
    status = 400


class NotFound(ServiceError):
    status = 404


class Forbidden(ServiceError):
    status = 403


class Conflicting(ServiceError):
    status = 409


class Invalid(ServiceError):
    status = 422


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    roles: frozenset[str]

    def can_write(self) -> bool:
        return bool(self.roles & WRITE_ROLES)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _hash_key(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()


class Platform:
    def __init__(self, db_url: str = "sqlite://", policy: ExecutionPolicy | None = None, adapter: Adapter | None = None,
                 extractors: list | None = None, sleep=time.sleep, workers: int = 2,
                 analysis_profiles: list[str] | None = None, analysis_binding: bool = True):
        self._in_memory = db_url in ("sqlite://", "sqlite:///:memory:")
        self.engine = make_engine(db_url)
        self.Session = session_factory(self.engine)
        self.policy = policy or ExecutionPolicy()
        self.adapter = adapter or OutboxAdapter("outbox")
        self.extractors = extractors or [extract_mod.RuleBasedExtractor()]
        self.sleep = sleep
        self.analysis_profiles, self.analysis_binding = analysis_profiles, analysis_binding
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="analysis")
        self._baseline_cache: dict[str, object] = {}
        self._plan_cache: dict[tuple, object] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ infra
    @contextmanager
    def tx(self):
        s = self.Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def close(self):
        self.pool.shutdown(wait=True)
        self.engine.dispose()

    # ------------------------------------------------------------------ users
    def create_user(self, tenant_id: str, name: str, roles: list[str], api_key: str | None = None) -> tuple[str, str]:
        bad = set(roles) - ROLES
        if bad:
            raise Invalid(f"unknown roles {sorted(bad)}")
        key = api_key or "sf_" + secrets.token_urlsafe(32)
        uid = _id("usr")
        with self.tx() as s:
            s.add(User(id=uid, tenant_id=tenant_id, name=name, roles=sorted(roles), api_key_hash=_hash_key(key)))
            audit_log.append(s, tenant_id, "system", "user.create", "user", uid, {"name": name, "roles": sorted(roles)})
        return uid, key

    def authenticate(self, api_key: str) -> Principal:
        with self.tx() as s:
            u = s.execute(select(User).where(User.api_key_hash == _hash_key(api_key), User.active.is_(True))).scalar_one_or_none()
            if u is None:
                raise Forbidden("invalid API key")
            return Principal(u.id, u.tenant_id, frozenset(u.roles))

    def _need_write(self, p: Principal):
        if not p.can_write():
            raise Forbidden("requires planner or admin role")

    # ------------------------------------------------------------------ snapshots
    def _latest(self, s: Session, tenant: str) -> Snapshot | None:
        return s.execute(select(Snapshot).where(Snapshot.tenant_id == tenant).order_by(Snapshot.version.desc()).limit(1)).scalar_one_or_none()

    def current(self, tenant: str) -> tuple[int, PlanInput]:
        with self.tx() as s:
            snap = self._latest(s, tenant)
            if snap is None:
                raise NotFound("no dataset imported yet")
            return snap.version, PlanInput.model_validate(snap.plan)

    def get_snapshot(self, tenant: str, version: int) -> PlanInput:
        with self.tx() as s:
            snap = s.get(Snapshot, (tenant, version))
            if snap is None:
                raise NotFound(f"snapshot {version} not found")
            return PlanInput.model_validate(snap.plan)

    def list_snapshots(self, tenant: str) -> list[dict]:
        with self.tx() as s:
            rows = s.execute(select(Snapshot).where(Snapshot.tenant_id == tenant).order_by(Snapshot.version.desc())).scalars()
            return [{"version": r.version, "hash": r.content_hash, "reason": r.reason, "event_ids": r.event_ids,
                     "created_by": r.created_by, "created_at": r.created_at.isoformat()} for r in rows]

    def import_plan(self, p: Principal, plan: PlanInput, reason: str = "dataset import") -> dict:
        self._need_write(p)
        plan = plan.model_copy(update={"tenant_id": p.tenant_id})
        issues = validate_integrity(plan)
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            raise Invalid("; ".join(f"{e.code}: {e.message}" for e in errors[:15]))
        for _ in range(3):
            try:
                with self.tx() as s:
                    prev = self._latest(s, p.tenant_id)
                    v = (prev.version if prev else 0) + 1
                    s.add(Snapshot(tenant_id=p.tenant_id, version=v, plan=plan.model_dump(mode="json"), content_hash=plan.content_hash(),
                                   reason=reason, created_by=p.user_id))
                    audit_log.append(s, p.tenant_id, p.user_id, "snapshot.import", "snapshot", str(v), {"hash": plan.content_hash(), "reason": reason})
                return {"version": v, "data_quality": data_quality(plan).model_dump(mode="json")}
            except IntegrityError:
                continue
        raise Conflicting("concurrent snapshot write; retry")

    def current_plan(self, tenant: str, profile: str = "no_spend") -> dict:
        """The plan for the current snapshot (cached by content hash): what ships, when, and what binds."""
        version, plan = self.current(tenant)
        key = (plan.content_hash(), profile)
        with self._lock:
            hit = self._plan_cache.get(key)
        if hit is None:
            hit = run_plan(plan, profile)
            with self._lock:
                if len(self._plan_cache) > 32:
                    self._plan_cache.clear()
                self._plan_cache[key] = hit
        gates = compliance.evaluate_all(plan)
        return {"version": version, "plan_start": plan.plan_start.isoformat(), "profile": profile, "result": hit.model_dump(mode="json"),
                "gates": {k: v.model_dump(mode="json") for k, v in gates.items()}}

    def data_quality(self, tenant: str) -> dict:
        _, plan = self.current(tenant)
        return data_quality(plan).model_dump(mode="json")

    @staticmethod
    def diff(a: PlanInput, b: PlanInput) -> list[dict]:
        da, db_ = a.model_dump(mode="json"), b.model_dump(mode="json")
        out: list[dict] = []
        for key in sorted(set(da) | set(db_)):
            va, vb = da.get(key), db_.get(key)
            if va == vb or key == "as_of":
                continue
            if isinstance(va, list) and isinstance(vb, list):
                def k(x):
                    return x.get("id") or f"{x.get('resource_id')}@{x.get('week')}" or x.get("destination") or json.dumps(x, sort_keys=True)
                ma, mb = {k(x): x for x in va}, {k(x): x for x in vb}
                for kk in sorted(set(ma) | set(mb)):
                    if ma.get(kk) != mb.get(kk):
                        fields = ({f: (ma[kk].get(f), mb[kk].get(f)) for f in set(ma[kk]) | set(mb[kk]) if ma[kk].get(f) != mb[kk].get(f) and f != "provenance"}
                                  if kk in ma and kk in mb else None)
                        out.append({"collection": key, "id": kk, "change": "changed" if fields is not None else ("added" if kk in mb else "removed"),
                                    "fields": fields})
            else:
                out.append({"collection": key, "change": "changed"})
        return out

    # ------------------------------------------------------------------ events
    def ingest_events(self, p: Principal, events: list[EventBase], *, title: str | None = None, create_incident: bool = True,
                      mode: str = "sync", proposed_ids: list[str] | None = None) -> dict:
        self._need_write(p)
        if not events:
            raise Invalid("no events supplied")
        for e in events:
            if e.force and "admin" not in p.roles and "planner" not in p.roles:
                raise Forbidden("force override requires planner/admin")
        for attempt in range(3):
            try:
                return self._ingest(p, events, title, create_incident, mode, proposed_ids or [])
            except IntegrityError:
                if attempt == 2:
                    raise Conflicting("concurrent write; retry")
        raise AssertionError

    def _ingest(self, p, events, title, create_incident, mode, proposed_ids) -> dict:
        tenant = p.tenant_id
        with self.tx() as s:
            snap = self._latest(s, tenant)
            if snap is None:
                raise NotFound("no dataset imported yet")
            before_v = snap.version
            plan = PlanInput.model_validate(snap.plan)
            total = ApplyResult()
            applied_events: list[tuple[EventBase, ApplyResult]] = []
            for ev in events:
                if any(r == ev.id for r in s.execute(select(EventRecord.id).where(EventRecord.id == ev.id)).scalars()):
                    raise Conflicting(f"event {ev.id} already ingested")
                try:
                    plan, res = apply_event(plan, ev)
                except EventError as e:
                    raise Invalid(str(e)) from e
                total.changes += res.changes
                total.conflicts += res.conflicts
                total.ignored += res.ignored
                total.subjects += res.subjects
                applied_events.append((ev, res))
            issues = [i for i in validate_integrity(plan) if i.severity == "error"]
            if issues:
                raise Invalid("; ".join(i.message for i in issues[:5]))
            after_v = before_v
            if total.changed:
                after_v = before_v + 1
                s.add(Snapshot(tenant_id=tenant, version=after_v, plan=plan.model_dump(mode="json"), content_hash=plan.content_hash(),
                               reason=title or "event: " + ", ".join(e.type for e, _ in applied_events), event_ids=[e.id for e, _ in applied_events],
                               created_by=p.user_id))
            for ev, res in applied_events:
                s.add(EventRecord(id=ev.id, tenant_id=tenant, event=ev.model_dump(mode="json"), snapshot_before=before_v, snapshot_after=after_v,
                                  result=res.model_dump(mode="json"), applied_by=p.user_id))
                audit_log.append(s, tenant, p.user_id, "event.apply", "event", ev.id,
                                 {"type": ev.type, "source": ev.source, "changes": [c.model_dump(mode="json") for c in res.changes][:50],
                                  "conflicts": len(res.conflicts), "snapshot": after_v})
                for c in res.conflicts:
                    cid = _id("cfl")
                    s.add(Conflict(id=cid, tenant_id=tenant, body=c.model_dump(mode="json"), event_id=ev.id))
                    audit_log.append(s, tenant, p.user_id, "conflict.open", "conflict", cid, c.model_dump(mode="json"))
            incident_id = None
            if total.changed and create_incident:
                incident_id = _id("inc")
                detected = min(e.observed_at for e in events)
                s.add(Incident(id=incident_id, tenant_id=tenant, title=title or self._title(applied_events), state="analyzing",
                               event_ids=[e.id for e in events], snapshot_before=before_v, snapshot_after=after_v, detected_at=detected))
                audit_log.append(s, tenant, p.user_id, "incident.open", "incident", incident_id, {"events": [e.id for e in events], "snapshot_after": after_v})
                for pid in proposed_ids:
                    pe = s.get(ProposedEvent, pid)
                    if pe:
                        pe.incident_id = incident_id
        out = {"snapshot_version": after_v, "changed": total.changed, "changes": [c.model_dump(mode="json") for c in total.changes],
               "conflicts": [c.model_dump(mode="json") for c in total.conflicts], "ignored": total.ignored, "incident_id": incident_id}
        if incident_id:
            subjects = total.subjects
            if mode == "async" and not self._in_memory:  # in-memory SQLite shares one connection: not thread-safe
                self.pool.submit(self._analyze_safe, tenant, incident_id, subjects)
            else:
                self._analyze_safe(tenant, incident_id, subjects)
        return out

    @staticmethod
    def _title(applied) -> str:
        kinds = Counter(e.type for e, _ in applied)
        return "Supply change: " + ", ".join(f"{k.replace('_', ' ')}" + (f" x{n}" if n > 1 else "") for k, n in kinds.items())

    # ------------------------------------------------------------------ analysis
    def _baseline(self, plan: PlanInput):
        h = plan.content_hash()
        with self._lock:
            hit = self._baseline_cache.get(h)
        if hit is None:
            hit = run_plan(plan, "no_spend")
            with self._lock:
                if len(self._baseline_cache) > 64:
                    self._baseline_cache.clear()
                self._baseline_cache[h] = hit
        return hit

    def _analyze_safe(self, tenant: str, incident_id: str, subjects: list[tuple[str, str]]):
        try:
            self._analyze(tenant, incident_id, subjects)
        except Exception as e:  # recorded on the incident; never lost
            with self.tx() as s:
                inc = s.get(Incident, incident_id)
                inc.state, inc.error = "analysis_failed", f"{type(e).__name__}: {e}"
                audit_log.append(s, tenant, "system", "incident.analysis_failed", "incident", incident_id, {"error": inc.error})

    def _analyze(self, tenant: str, incident_id: str, subjects: list[tuple[str, str]]):
        with self.tx() as s:
            inc = s.get(Incident, incident_id)
            before_plan = PlanInput.model_validate(s.get(Snapshot, (tenant, inc.snapshot_before)).plan)
            after_plan = PlanInput.model_validate(s.get(Snapshot, (tenant, inc.snapshot_after)).plan)
        before = self._baseline(before_plan)
        gates_before = compliance.evaluate_all(before_plan)
        opts = build_options(after_plan, with_binding=self.analysis_binding, profiles=self.analysis_profiles)
        analysis = self._compose_analysis(after_plan, before_plan, before, gates_before, opts, subjects)
        with self.tx() as s:
            inc = s.get(Incident, incident_id)
            inc.analysis = analysis
            inc.state = "options_ready" if analysis["material"] and opts.options else "no_impact"
            inc.plan_ready_at = utcnow()
            audit_log.append(s, tenant, "system", "incident.analyzed", "incident", incident_id,
                             {"material": analysis["material"], "options": [o["id"] for o in analysis["options"]],
                              "recommended": analysis["recommended"], "snapshot_after": inc.snapshot_after})

    def _compose_analysis(self, after_plan, before_plan, before, gates_before, opts: OptionSet, subjects) -> dict:
        # What happens if nobody acts: the pre-event plan can only shrink under the new facts.
        gates_after = opts.gates
        status_quo = evaluate_under(after_plan, before, before.scenario, gates_after)
        sq_impact = compute_impact(after_plan, subjects, before, status_quo)
        impacts, explanations, approvals, options_out = {}, {}, {}, []
        binding = opts.binding
        for o in opts.options:
            imp = compute_impact(after_plan, subjects, before, o.result)
            impacts[o.id] = imp
            approvals[o.id] = required_approvals(after_plan, o, imp, gates_before, opts.gates)
            explanations[o.id] = [explain_order(after_plan, oc, opts.gates[oc.order_id], binding) if (binding and o.recommended)
                                  else explain_order(after_plan, oc, opts.gates[oc.order_id], _EMPTY_BINDING) for oc in o.result.orders if oc.status != "on_time"]
            options_out.append({
                **o.model_dump(mode="json"), "impact": impacts[o.id].model_dump(mode="json"),
                "required_approvals": approvals[o.id], "explanations": explanations[o.id]})
        material = sq_impact.material
        return {
            "material": material,
            "status_quo": {"totals": status_quo.totals, "impact": sq_impact.model_dump(mode="json")},
            "baseline": {"totals": before.totals, "snapshot_hash": before.snapshot_hash,
                         "orders": [oc.model_dump(mode="json") for oc in before.orders], "scenario": before.scenario.value, "profile": before.profile},
            "options": options_out, "rejected_options": opts.rejected, "recommended": next((o.id for o in opts.options if o.recommended), None),
            "binding": binding.model_dump(mode="json") if binding else None,
            "gates_after": {k: v.model_dump(mode="json") for k, v in opts.gates.items()},
            "gates_before": {k: v.model_dump(mode="json") for k, v in gates_before.items()},
            "plan_start": after_plan.plan_start.isoformat(),
            "subjects": [list(x) for x in subjects], "dependency_nodes": subject_nodes(subjects),
            "units_on_time_lost_if_no_action": sq_impact.units_on_time_lost,
            "exposure_delta_if_no_action": sq_impact.exposure_delta,
        }

    # ------------------------------------------------------------------ incidents
    def _inc(self, s: Session, tenant: str, incident_id: str) -> Incident:
        inc = s.get(Incident, incident_id)
        if inc is None or inc.tenant_id != tenant:
            raise NotFound(f"incident {incident_id} not found")
        return inc

    def list_incidents(self, tenant: str, state: str | None = None) -> list[dict]:
        with self.tx() as s:
            q = select(Incident).where(Incident.tenant_id == tenant).order_by(Incident.created_at.desc())
            if state:
                q = q.where(Incident.state == state)
            return [self._summary(i) for i in s.execute(q).scalars()]

    @staticmethod
    def _summary(i: Incident) -> dict:
        a = i.analysis or {}
        return {"id": i.id, "title": i.title, "state": i.state, "detected_at": i.detected_at.isoformat(), "snapshot_before": i.snapshot_before,
                "snapshot_after": i.snapshot_after, "selected_option": i.selected_option, "recommended": a.get("recommended"),
                "material": a.get("material"), "units_on_time_lost_if_no_action": a.get("units_on_time_lost_if_no_action"),
                "exposure_delta_if_no_action": a.get("exposure_delta_if_no_action"), "error": i.error}

    def get_incident(self, tenant: str, incident_id: str, full: bool = True) -> dict:
        with self.tx() as s:
            i = self._inc(s, tenant, incident_id)
            out = self._summary(i)
            out.update({"event_ids": i.event_ids, "parent_id": i.parent_id, "required_approvals": i.required_approvals, "selected_by": i.selected_by,
                        "plan_hash": i.plan_hash, "plan_ready_at": i.plan_ready_at.isoformat() if i.plan_ready_at else None,
                        "approved_at": i.approved_at.isoformat() if i.approved_at else None})
            if full:
                out["analysis"] = i.analysis
            ap = s.execute(select(Approval).where(Approval.incident_id == incident_id).order_by(Approval.created_at)).scalars()
            names = {u.id: u.name for u in s.execute(select(User).where(User.tenant_id == tenant)).scalars()}
            out["selected_by_name"] = names.get(i.selected_by or "")
            out["approvals"] = [{"id": a.id, "user": a.user_id, "user_name": names.get(a.user_id), "role": a.role, "decision": a.decision, "comment": a.comment, "option_id": a.option_id,
                                 "plan_hash": a.plan_hash, "at": a.created_at.isoformat()} for a in ap]
            acts = s.execute(select(Action).where(Action.incident_id == incident_id).order_by(Action.created_at)).scalars()
            out["actions"] = [self._action_view(a) for a in acts]
            return out

    @staticmethod
    def _action_view(a: Action) -> dict:
        return {"id": a.id, "type": a.type, "target": a.target, "external": a.external, "status": a.status, "lever_id": a.lever_id, "payload": a.payload,
                "attempts": a.attempts, "last_error": a.last_error, "response": a.response, "idempotency_key": a.idempotency_key,
                "reverts_action_id": a.reverts_action_id}

    def _option(self, inc: Incident, option_id: str) -> dict:
        for o in (inc.analysis or {}).get("options", []):
            if o["id"] == option_id:
                return o
        raise NotFound(f"option {option_id} not found on incident")

    def select_option(self, p: Principal, incident_id: str, option_id: str) -> dict:
        self._need_write(p)
        with self.tx() as s:
            inc = self._inc(s, p.tenant_id, incident_id)
            if inc.state not in ("options_ready", "pending_approval", "rejected"):
                raise Conflicting(f"cannot select an option while incident is '{inc.state}'")
            opt = self._option(inc, option_id)
            if not opt["result"]["verified"]:
                raise Conflicting("option failed independent verification")
            inc.selected_option, inc.selected_by, inc.plan_hash = option_id, p.user_id, opt["plan_hash"]
            inc.required_approvals = opt["required_approvals"]
            inc.state = "pending_approval"
            audit_log.append(s, p.tenant_id, p.user_id, "incident.select_option", "incident", incident_id,
                             {"option": option_id, "plan_hash": opt["plan_hash"], "required": [r["role"] for r in opt["required_approvals"]],
                              "recommended": option_id == inc.analysis.get("recommended")})
            return self._summary(inc)

    def decide(self, p: Principal, incident_id: str, decision: str, role: str, comment: str = "") -> dict:
        if decision not in ("approve", "reject"):
            raise Invalid("decision must be approve|reject")
        if role not in p.roles:
            raise Forbidden(f"you do not hold the '{role}' role")
        with self.tx() as s:
            inc = self._inc(s, p.tenant_id, incident_id)
            if inc.state != "pending_approval":
                raise Conflicting(f"incident is '{inc.state}', not pending approval")
            required = {r["role"] for r in inc.required_approvals}
            if role not in required:
                raise Invalid(f"role '{role}' is not required for this plan (required: {sorted(required)})")
            if decision == "reject" and not comment.strip():
                raise Invalid("a rejection requires a reason")
            if self.policy.require_distinct_proposer and inc.selected_by == p.user_id:
                raise Forbidden("the user who selected the option cannot approve it (four-eyes)")
            mine = s.execute(select(Approval).where(Approval.incident_id == incident_id, Approval.plan_hash == inc.plan_hash,
                                                    Approval.user_id == p.user_id, Approval.role != role)).scalars().first()
            if mine and decision == "approve":
                raise Forbidden("one person cannot satisfy more than one required approval role")
            s.add(Approval(id=_id("apr"), tenant_id=p.tenant_id, incident_id=incident_id, user_id=p.user_id, role=role, decision=decision,
                           comment=comment, option_id=inc.selected_option, plan_hash=inc.plan_hash))
            s.flush()
            audit_log.append(s, p.tenant_id, p.user_id, f"incident.{decision}", "incident", incident_id, {"role": role, "comment": comment, "option": inc.selected_option})
            if decision == "reject":
                inc.state = "rejected"
            else:
                latest = {}
                for a in s.execute(select(Approval).where(Approval.incident_id == incident_id, Approval.plan_hash == inc.plan_hash).order_by(Approval.created_at)).scalars():
                    latest[a.role] = a.decision
                if all(latest.get(r) == "approve" for r in required):
                    inc.state, inc.approved_at = "approved", utcnow()
                    audit_log.append(s, p.tenant_id, "system", "incident.approved", "incident", incident_id, {"option": inc.selected_option, "plan_hash": inc.plan_hash})
            return self._summary(inc)

    def _recheck_feasible(self, s: Session, tenant: str, inc: Incident) -> tuple[bool, list[str]]:
        """The world may have moved since analysis: re-verify the chosen plan against the *current* snapshot."""
        current = PlanInput.model_validate(self._latest(s, tenant).plan)
        opt = self._option(inc, inc.selected_option)
        from ..engine.types import PlanResult
        res = PlanResult.model_validate(opt["result"])
        gates = compliance.evaluate_all(current)
        errs = verify(current, materialize(current, res.scenario), gates, res)
        return (not errs), errs[:10]

    def execute(self, p: Principal, incident_id: str) -> dict:
        self._need_write(p)
        if self.policy.mode == "shadow":
            raise Conflicting("tenant is in shadow mode: recommendations only, no actions are produced")
        with self.tx() as s:
            inc = self._inc(s, p.tenant_id, incident_id)
            if inc.state not in ("approved", "executing"):
                raise Conflicting(f"incident is '{inc.state}'; only approved plans can be executed")
            ok, errs = self._recheck_feasible(s, p.tenant_id, inc)
            if not ok:
                inc.state = "stale"
                audit_log.append(s, p.tenant_id, p.user_id, "incident.stale", "incident", incident_id, {"violations": errs})
                s.commit()
                raise Conflicting("plan no longer feasible on current data; re-plan required: " + "; ".join(errs[:3]))
            # approvals must still match the plan being executed
            required = {r["role"] for r in inc.required_approvals}
            got = {a.role for a in s.execute(select(Approval).where(Approval.incident_id == incident_id, Approval.plan_hash == inc.plan_hash,
                                                                   Approval.decision == "approve")).scalars()}
            if not required <= got:
                raise Forbidden("missing approvals: " + ", ".join(sorted(required - got)))
            opt = self._option(inc, inc.selected_option)
            from ..engine.recovery import RecoveryOption as RO
            option = RO.model_validate({k: v for k, v in opt.items() if k not in ("impact", "required_approvals", "explanations")})
            plan = PlanInput.model_validate(s.get(Snapshot, (p.tenant_id, inc.snapshot_after)).plan)
            drafts = build_drafts(plan, option, incident_id)
            existing = {a.idempotency_key: a for a in s.execute(select(Action).where(Action.incident_id == incident_id)).scalars()}
            for d in drafts:
                key = idem_key(incident_id, inc.plan_hash, d)
                if key in existing:
                    continue
                a = Action(id=_id("act"), tenant_id=p.tenant_id, incident_id=incident_id, type=d.type, target=d.target, external=d.external,
                           payload=d.payload, lever_id=d.lever_id, status="drafted", idempotency_key=key)
                s.add(a)
                existing[key] = a
                audit_log.append(s, p.tenant_id, p.user_id, "action.draft", "action", a.id, {"type": d.type, "target": d.target, "external": d.external})
            s.flush()
            inc.state = "executing"
            inc.executed_at = utcnow()
            pending = [a for a in existing.values() if a.status in ("drafted", "failed")]
            if self.policy.mode == "execute":
                for a in pending:
                    if a.type not in self.policy.execute_allowlist:
                        continue
                    self._send(s, p, a)
            self._maybe_complete(s, inc, p)
            return self.get_incident_in(s, inc)

    def get_incident_in(self, s: Session, inc: Incident) -> dict:
        s.flush()
        acts = s.execute(select(Action).where(Action.incident_id == inc.id).order_by(Action.created_at)).scalars()
        return {**self._summary(inc), "actions": [self._action_view(a) for a in acts]}

    def _send(self, s: Session, p: Principal, a: Action):
        out, attempts, err = run_with_retry(
            lambda: self.adapter.send(p.tenant_id, a.id, a.idempotency_key, a.type, a.target, a.payload), self.policy.max_attempts, self.sleep)
        a.attempts += attempts
        if err:
            a.status, a.last_error = "failed", err
        else:
            a.status, a.response, a.last_error = "sent", out, None
        audit_log.append(s, p.tenant_id, p.user_id, f"action.{a.status}", "action", a.id, {"attempts": a.attempts, "error": a.last_error, "adapter": self.adapter.name})

    def _maybe_complete(self, s: Session, inc: Incident, p: Principal):
        s.flush()
        sts = [a.status for a in s.execute(select(Action).where(Action.incident_id == inc.id)).scalars()]
        if sts and all(x in ("sent", "confirmed", "declined", "reverted") for x in sts) and inc.state == "executing":
            inc.state = "completed"
            audit_log.append(s, p.tenant_id, "system", "incident.completed", "incident", inc.id, {})

    def mark_sent(self, p: Principal, action_id: str, note: str = "") -> dict:
        self._need_write(p)
        with self.tx() as s:
            a = self._act(s, p.tenant_id, action_id)
            if a.status not in ("drafted", "failed"):
                raise Conflicting(f"action is '{a.status}'")
            a.status, a.response = "sent", {"manual": True, "by": p.user_id, "note": note}
            audit_log.append(s, p.tenant_id, p.user_id, "action.sent_manually", "action", a.id, {"note": note})
            self._maybe_complete(s, self._inc(s, p.tenant_id, a.incident_id), p)
            return self._action_view(a)

    def _act(self, s: Session, tenant: str, action_id: str) -> Action:
        a = s.get(Action, action_id)
        if a is None or a.tenant_id != tenant:
            raise NotFound(f"action {action_id} not found")
        return a

    def confirm_action(self, p: Principal, action_id: str, outcome: str, *, qty: int | None = None, date_: str | None = None, note: str = "") -> dict:
        """Close the loop: a counterparty answered a lever request -> becomes a sourced fact in the next snapshot."""
        self._need_write(p)
        if outcome not in ("confirmed", "declined"):
            raise Invalid("outcome must be confirmed|declined")
        with self.tx() as s:
            a = self._act(s, p.tenant_id, action_id)
            if a.type != "SUPPLIER_REQUEST" and not (a.type == "CUSTOMER_COMMIT_UPDATE" and a.lever_id):
                raise Invalid("only lever requests can be confirmed")
            if a.status not in ("sent",):
                raise Conflicting(f"action is '{a.status}'; it must be sent first")
            inc = self._inc(s, p.tenant_id, a.incident_id)
            snap_plan = PlanInput.model_validate(s.get(Snapshot, (p.tenant_id, inc.snapshot_after)).plan)
            lv = next(x for x in snap_plan.levers if x.id == a.lever_id)
            a.status = "confirmed" if outcome == "confirmed" else "declined"
            a.response = {**(a.response or {}), "answer": {"outcome": outcome, "qty": qty, "date": date_, "note": note, "by": p.user_id}}
            audit_log.append(s, p.tenant_id, p.user_id, f"action.{a.status}", "action", a.id, a.response["answer"])
            incident_id = inc.id
        result = {"action": a.id, "status": a.status, "snapshot_version": None, "replan_incident_id": None}
        if outcome == "confirmed":
            ev = self._lever_event(lv, snap_plan, p, qty, date_, a)
            r = self.ingest_events(p, [ev], title=f"Confirmation of {lv.id}", create_incident=False)
            result["snapshot_version"] = r["snapshot_version"]
        else:
            result["replan_incident_id"] = self.replan(p, incident_id, reason=f"{lv.id} declined by {lv.counterparty or lv.target_id}")["id"]
        with self.tx() as s:
            self._maybe_complete(s, self._inc(s, p.tenant_id, incident_id), p)
        return result

    @staticmethod
    def _lever_event(lv, plan, p, qty, date_, action) -> EventBase:
        from datetime import date as D
        when = D.fromisoformat(date_) if date_ else (lv.available_week or plan.plan_start)
        common = dict(source="supplier_portal", method="manual", note=f"confirmation of {lv.id} (action {action.id}) by {p.user_id}")
        if lv.type == "expedite_supply":
            from ..domain import SupplyCommitment
            return CommitmentAdd(commitment=SupplyCommitment(id=f"EXP-{lv.id}-{action.id[-6:]}", part_id=lv.target_id, qty=qty or lv.qty_cap, promised_week=when,
                                                             status="confirmed", po_ref=lv.id), **common)
        if lv.type == "spot_capacity":
            from datetime import timedelta
            return CapacityChange(resource_id=lv.target_id, from_week=when, to_week=when + timedelta(weeks=lv.capacity_weeks - 1), mode="add", value=qty or lv.qty_cap, **common)
        if lv.type == "accelerate_qualification":
            return RouteQualification(route_id=lv.target_id, qualification="qualified", qualified_from=when, **common)
        o = plan.order(lv.target_id)
        from datetime import timedelta
        return DemandChange(order_id=o.id, latest_week=o.latest_week + timedelta(weeks=lv.extend_weeks), **common)

    def revert_action(self, p: Principal, action_id: str, reason: str) -> dict:
        self._need_write(p)
        with self.tx() as s:
            a = self._act(s, p.tenant_id, action_id)
            if a.status not in ("sent", "confirmed"):
                raise Conflicting(f"action is '{a.status}'; only sent/confirmed actions can be reverted")
            comp = Action(id=_id("act"), tenant_id=p.tenant_id, incident_id=a.incident_id, type="CANCEL_" + a.type, target=a.target, external=a.external,
                          payload={"cancels": a.id, "reason": reason, "original_subject": a.payload.get("subject")}, lever_id=a.lever_id, status="drafted",
                          idempotency_key=hashlib.sha256(f"revert|{a.id}".encode()).hexdigest()[:32], reverts_action_id=a.id)
            s.add(comp)
            a.status = "reverted"
            audit_log.append(s, p.tenant_id, p.user_id, "action.revert", "action", a.id, {"compensating": comp.id, "reason": reason})
            return self._action_view(comp)

    def replan(self, p: Principal, incident_id: str, reason: str = "", mode: str = "sync") -> dict:
        self._need_write(p)
        with self.tx() as s:
            old = self._inc(s, p.tenant_id, incident_id)
            cur = self._latest(s, p.tenant_id)
            new_id = _id("inc")
            s.add(Incident(id=new_id, tenant_id=p.tenant_id, title=f"Re-plan: {old.title}", state="analyzing", parent_id=old.id, event_ids=[],
                           snapshot_before=old.snapshot_after, snapshot_after=cur.version, detected_at=utcnow()))
            if old.state not in ("completed", "executing"):
                old.state = "superseded"
            audit_log.append(s, p.tenant_id, p.user_id, "incident.replan", "incident", old.id, {"new": new_id, "reason": reason})
        if mode == "async" and not self._in_memory:
            self.pool.submit(self._analyze_safe, p.tenant_id, new_id, [])
        else:
            self._analyze_safe(p.tenant_id, new_id, [])
        return self.get_incident(p.tenant_id, new_id, full=False)

    # ------------------------------------------------------------------ documents & proposals
    def ingest_document(self, p: Principal, source: str, text: str, kind: str = "notice") -> dict:
        self._need_write(p)
        sha = hashlib.sha256(text.encode()).hexdigest()
        _, plan = self.current(p.tenant_id)
        with self.tx() as s:
            dup = s.execute(select(Document).where(Document.tenant_id == p.tenant_id, Document.sha256 == sha)).scalar_one_or_none()
            if dup:
                doc_id, dup_flag = dup.id, True
            else:
                doc_id, dup_flag = _id("doc"), False
                s.add(Document(id=doc_id, tenant_id=p.tenant_id, source=source, kind=kind, sha256=sha, content=text, uploaded_by=p.user_id))
                audit_log.append(s, p.tenant_id, p.user_id, "document.ingest", "document", doc_id, {"source": source, "sha256": sha, "bytes": len(text)})
        if dup_flag:
            return {"document_id": doc_id, "duplicate": True, "proposals": self.list_proposals(p.tenant_id, document_id=doc_id)}
        props = extract_mod.propose_events(text, plan, source, extractors=self.extractors, document_id=doc_id)
        out = []
        with self.tx() as s:
            for pr in props:
                if not pr.event:
                    continue
                pid = _id("prp")
                s.add(ProposedEvent(id=pid, tenant_id=p.tenant_id, document_id=doc_id, event=pr.event, quotes=pr.quotes, ambiguities=pr.ambiguities,
                                    warnings=pr.warnings, confidence=pr.confidence, method=pr.method))
                audit_log.append(s, p.tenant_id, "system", "proposal.create", "proposal", pid, {"document": doc_id, "type": pr.event.get("type"), "method": pr.method})
                out.append(pid)
        return {"document_id": doc_id, "duplicate": False, "proposals": self.list_proposals(p.tenant_id, document_id=doc_id)}

    def list_proposals(self, tenant: str, status: str | None = None, document_id: str | None = None) -> list[dict]:
        with self.tx() as s:
            q = select(ProposedEvent).where(ProposedEvent.tenant_id == tenant).order_by(ProposedEvent.created_at)
            if status:
                q = q.where(ProposedEvent.status == status)
            if document_id:
                q = q.where(ProposedEvent.document_id == document_id)
            return [{"id": r.id, "document_id": r.document_id, "event": r.event, "quotes": r.quotes, "ambiguities": r.ambiguities, "warnings": r.warnings,
                     "confidence": r.confidence, "method": r.method, "status": r.status, "incident_id": r.incident_id} for r in s.execute(q).scalars()]

    def confirm_proposal(self, p: Principal, proposal_id: str, *, edits: dict | None = None, acknowledge_ambiguities: bool = False, note: str = "") -> dict:
        self._need_write(p)
        with self.tx() as s:
            pr = s.get(ProposedEvent, proposal_id)
            if pr is None or pr.tenant_id != p.tenant_id:
                raise NotFound("proposal not found")
            if pr.status != "pending":
                raise Conflicting(f"proposal already {pr.status}")
            if pr.ambiguities and not acknowledge_ambiguities:
                raise Invalid("proposal has ambiguities; review and set acknowledge_ambiguities=true (optionally with edits): " + " | ".join(pr.ambiguities))
            body = {**pr.event, **(edits or {})}
        body["method"] = "extraction"
        body.setdefault("id", _id("evt"))
        try:
            ev = _EVENT_ADAPTER.validate_python(body)
        except ValidationError as e:
            raise Invalid(f"edited event is invalid: {e.errors()[0]['msg']}") from e
        # a human has now vouched for it: confidence reflects that, source stays the original (precedence rules still apply)
        ev = ev.model_copy(update={"confidence": max(ev.confidence, 0.9)})
        result = self.ingest_events(p, [ev], proposed_ids=[proposal_id])
        with self.tx() as s:
            pr = s.get(ProposedEvent, proposal_id)
            pr.status, pr.decided_by, pr.decided_at, pr.decision_note = "confirmed", p.user_id, utcnow(), note
            audit_log.append(s, p.tenant_id, p.user_id, "proposal.confirm", "proposal", proposal_id, {"edited": bool(edits), "note": note})
        return result

    def confirm_proposals(self, p: Principal, proposal_ids: list[str], *, acknowledge_ambiguities: bool = False, note: str = "", mode: str = "sync") -> dict:
        """Confirm several proposals from one notice as a single batch -> one snapshot, one incident."""
        self._need_write(p)
        events = []
        with self.tx() as s:
            for pid in proposal_ids:
                pr = s.get(ProposedEvent, pid)
                if pr is None or pr.tenant_id != p.tenant_id:
                    raise NotFound(f"proposal {pid} not found")
                if pr.status != "pending":
                    raise Conflicting(f"proposal {pid} already {pr.status}")
                if pr.ambiguities and not acknowledge_ambiguities:
                    raise Invalid(f"proposal {pid} has ambiguities; acknowledge_ambiguities=true required: " + " | ".join(pr.ambiguities))
                try:
                    ev = _EVENT_ADAPTER.validate_python({**pr.event, "id": _id("evt")})
                except ValidationError as e:
                    raise Invalid(f"proposal {pid} invalid: {e.errors()[0]['msg']}") from e
                events.append(ev.model_copy(update={"confidence": max(ev.confidence, 0.9)}))
        result = self.ingest_events(p, events, proposed_ids=proposal_ids, mode=mode)
        with self.tx() as s:
            for pid in proposal_ids:
                pr = s.get(ProposedEvent, pid)
                pr.status, pr.decided_by, pr.decided_at, pr.decision_note = "confirmed", p.user_id, utcnow(), note
                audit_log.append(s, p.tenant_id, p.user_id, "proposal.confirm", "proposal", pid, {"batch": len(proposal_ids), "note": note})
        return result

    def reject_proposal(self, p: Principal, proposal_id: str, note: str) -> dict:
        self._need_write(p)
        with self.tx() as s:
            pr = s.get(ProposedEvent, proposal_id)
            if pr is None or pr.tenant_id != p.tenant_id:
                raise NotFound("proposal not found")
            if pr.status != "pending":
                raise Conflicting(f"proposal already {pr.status}")
            pr.status, pr.decided_by, pr.decided_at, pr.decision_note = "rejected", p.user_id, utcnow(), note
            audit_log.append(s, p.tenant_id, p.user_id, "proposal.reject", "proposal", proposal_id, {"note": note})
            return {"id": pr.id, "status": pr.status}

    # ------------------------------------------------------------------ conflicts
    def list_conflicts(self, tenant: str, status: str | None = "open") -> list[dict]:
        with self.tx() as s:
            q = select(Conflict).where(Conflict.tenant_id == tenant).order_by(Conflict.created_at)
            if status:
                q = q.where(Conflict.status == status)
            return [{"id": c.id, "status": c.status, "event_id": c.event_id, **c.body, "resolution": c.resolution} for c in s.execute(q).scalars()]

    def resolve_conflict(self, p: Principal, conflict_id: str, accept: str, note: str = "") -> dict:
        self._need_write(p)
        if accept not in ("proposed", "current"):
            raise Invalid("accept must be proposed|current")
        with self.tx() as s:
            c = s.get(Conflict, conflict_id)
            if c is None or c.tenant_id != p.tenant_id:
                raise NotFound("conflict not found")
            if c.status != "open":
                raise Conflicting("conflict already resolved")
            b = dict(c.body)
        want_proposed = accept == "proposed"
        needs_change = want_proposed != bool(b["applied"])
        result = {"conflict_id": conflict_id, "snapshot_version": None, "incident_id": None}
        if needs_change:
            value = b["proposed_value"] if want_proposed else b["current_value"]
            ev = self._forced_event(b, value, p, note)
            r = self.ingest_events(p, [ev], title=f"Conflict resolution ({accept}) for {b['entity_type']} {b['entity_id']}")
            result.update(snapshot_version=r["snapshot_version"], incident_id=r["incident_id"])
        with self.tx() as s:
            c = s.get(Conflict, conflict_id)
            c.status, c.resolution, c.resolved_by, c.resolved_at = "resolved", accept, p.user_id, utcnow()
            audit_log.append(s, p.tenant_id, p.user_id, "conflict.resolve", "conflict", conflict_id, {"accept": accept, "note": note})
        return result

    def resolve_conflicts(self, p: Principal, accept: str, *, ids: list[str] | None = None, event_id: str | None = None, note: str = "") -> list[dict]:
        """Bulk resolution (e.g. every weekly bucket touched by one notice)."""
        if not ids and not event_id:
            raise Invalid("supply ids or event_id")
        with self.tx() as s:
            q = select(Conflict.id).where(Conflict.tenant_id == p.tenant_id, Conflict.status == "open")
            q = q.where(Conflict.id.in_(ids)) if ids else q.where(Conflict.event_id == event_id)
            todo = list(s.execute(q).scalars())
        return [self.resolve_conflict(p, cid, accept, note) for cid in todo]

    @staticmethod
    def _forced_event(b: dict, value, p: Principal, note: str) -> EventBase:
        common = dict(source="manual", method="manual", force=True, note=f"conflict resolution by {p.user_id}: {note}")
        et, eid, field = b["entity_type"], b["entity_id"], b["field"]
        if et == "capacity":
            rid, wk = eid.split("@")
            return CapacityChange(resource_id=rid, from_week=wk, to_week=wk, mode="set", value=int(value), **common)
        if et == "commitment":
            return CommitmentChange(commitment_id=eid, **{field: value}, **common)
        if et == "lot":
            return LotStatusChange(lot_id=eid, status=value, **common)
        raise Invalid(f"cannot resolve conflicts on {et}")

    # ------------------------------------------------------------------ outcomes & learning
    def record_outcome(self, p: Principal, kind: str, subject: str, planned: dict, actual: dict, incident_id: str | None = None) -> dict:
        self._need_write(p)
        if kind not in ("supplier_commitment", "delivery", "yield"):
            raise Invalid("kind must be supplier_commitment|delivery|yield")
        with self.tx() as s:
            oid = _id("out")
            s.add(Outcome(id=oid, tenant_id=p.tenant_id, incident_id=incident_id, kind=kind, subject=subject, planned=planned, actual=actual, recorded_by=p.user_id))
            audit_log.append(s, p.tenant_id, p.user_id, "outcome.record", "outcome", oid, {"kind": kind, "subject": subject})
            return {"id": oid}

    def calibration_report(self, tenant: str) -> dict:
        """Observed vs planned. Reported and versioned for humans; never silently fed back into constraints."""
        with self.tx() as s:
            rows = list(s.execute(select(Outcome).where(Outcome.tenant_id == tenant)).scalars())
        sup: dict[str, list[dict]] = defaultdict(list)
        deliv_err, yields = [], defaultdict(list)
        for r in rows:
            if r.kind == "supplier_commitment":
                sup[r.subject].append(r)
            elif r.kind == "delivery" and r.planned.get("units") and "units" in r.actual:
                deliv_err.append((r.actual["units"] - r.planned["units"]) / r.planned["units"])
            elif r.kind == "yield" and "yield_pm" in r.actual:
                yields[r.subject].append((r.planned.get("yield_pm"), r.actual["yield_pm"]))
        suppliers = {}
        for sid, rs in sup.items():
            on_time = sum(1 for r in rs if r.actual.get("qty", 0) >= r.planned.get("qty", 0) and r.actual.get("week", 0) <= r.planned.get("week", 0))
            n = len(rs)
            suppliers[sid] = {"commitments": n, "fully_on_time": on_time, "reliability_laplace": round((on_time + 1) / (n + 2), 3)}
        return {"suppliers": suppliers,
                "delivery_error_pct_mean": round(100 * sum(deliv_err) / len(deliv_err), 2) if deliv_err else None,
                "delivery_samples": len(deliv_err),
                "yield": {k: {"samples": len(v), "mean_actual_pm": round(sum(a for _, a in v) / len(v), 1),
                              "mean_planned_pm": round(sum(pl for pl, _ in v if pl) / max(1, len([1 for pl, _ in v if pl])), 1)} for k, v in yields.items()},
                "note": "Advisory only. Apply by importing revised confidences/yields as a new, reviewed snapshot."}

    def metrics(self, tenant: str) -> dict:
        with self.tx() as s:
            incs = list(s.execute(select(Incident).where(Incident.tenant_id == tenant)).scalars())
            appr = list(s.execute(select(Approval).where(Approval.tenant_id == tenant)).scalars())
            acts = list(s.execute(select(Action).where(Action.tenant_id == tenant)).scalars())
            conflicts_open = s.execute(select(func.count()).select_from(Conflict).where(Conflict.tenant_id == tenant, Conflict.status == "open")).scalar_one()
            props = list(s.execute(select(ProposedEvent).where(ProposedEvent.tenant_id == tenant)).scalars())
            chain = audit_log.verify_chain(s, tenant)
        t_plan = [(i.plan_ready_at - i.detected_at).total_seconds() / 3600 for i in incs if i.plan_ready_at]
        t_appr = [(i.approved_at - i.detected_at).total_seconds() / 3600 for i in incs if i.approved_at]
        decided = [i for i in incs if i.selected_option and i.state in ("approved", "executing", "completed", "rejected")]
        accepted_rec = [i for i in decided if i.state != "rejected" and i.selected_option == (i.analysis or {}).get("recommended")]
        rej = Counter(a.comment.strip().lower()[:80] for a in appr if a.decision == "reject")
        return {
            "incidents": Counter(i.state for i in incs), "hours_detect_to_plan_mean": round(sum(t_plan) / len(t_plan), 3) if t_plan else None,
            "hours_detect_to_approval_mean": round(sum(t_appr) / len(t_appr), 3) if t_appr else None,
            "recommendation_acceptance_rate": round(len(accepted_rec) / len(decided), 3) if decided else None,
            "rejection_reasons": dict(rej), "actions": Counter(a.status for a in acts),
            "plan_infeasible_at_execution": sum(1 for i in incs if i.state == "stale"),
            "open_conflicts": conflicts_open, "proposals": Counter(x.status for x in props),
            "extraction_confirm_rate": (round(sum(1 for x in props if x.status == "confirmed") / max(1, sum(1 for x in props if x.status != "pending")), 3)
                                         if any(x.status != "pending" for x in props) else None),
            "audit_chain": chain,
        }

    # ------------------------------------------------------------------ audit
    def audit(self, tenant: str, entity_type: str | None = None, entity_id: str | None = None, limit: int = 200) -> list[dict]:
        with self.tx() as s:
            q = select(AuditLog).where(AuditLog.tenant_id == tenant).order_by(AuditLog.seq.desc()).limit(limit)
            if entity_type:
                q = q.where(AuditLog.entity_type == entity_type)
            if entity_id:
                q = q.where(AuditLog.entity_id == entity_id)
            return [{"seq": r.seq, "ts": r.ts.isoformat(), "actor": r.actor, "action": r.action, "entity_type": r.entity_type, "entity_id": r.entity_id,
                     "data": r.data, "hash": r.hash} for r in s.execute(q).scalars()]

    def verify_audit(self, tenant: str) -> dict:
        with self.tx() as s:
            return audit_log.verify_chain(s, tenant)


class _EmptyBinding:
    by_order: dict = {}
    joint_only_orders: list = []


_EMPTY_BINDING = _EmptyBinding()
