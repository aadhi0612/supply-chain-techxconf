"""Turn an approved option into concrete, idempotent actions and run them through adapters."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..domain import PlanInput
from ..engine.recovery import RecoveryOption


@dataclass
class Draft:
    type: str
    target: str
    external: bool
    payload: dict
    lever_id: str | None = None


def idem_key(incident_id: str, plan_hash: str, d: Draft) -> str:
    return hashlib.sha256(f"{incident_id}|{plan_hash}|{d.type}|{d.target}|{d.lever_id}".encode()).hexdigest()[:32]


def _fmt_week(plan: PlanInput, w: int | None) -> str:
    return plan.date_of(w).isoformat() if w is not None else "n/a"


def build_drafts(plan: PlanInput, option: RecoveryOption, incident_id: str) -> list[Draft]:
    res = option.result
    drafts: list[Draft] = []
    for lu in res.levers:
        lv = next(x for x in plan.levers if x.id == lu.lever_id)
        when = _fmt_week(plan, lu.available_week)
        if lv.type == "expedite_supply":
            body = (f"Please confirm in writing whether you can supply {lu.qty} units of part {lv.target_id} by {when} "
                    f"at the expedite rate of ${lv.unit_cost:,.2f}/unit (total ${lu.cost:,.2f}). Reply with the confirmed quantity and date; "
                    "this request is not a purchase commitment until confirmed by both parties.")
            drafts.append(Draft("SUPPLIER_REQUEST", lv.counterparty or lv.target_id, True,
                                {"subject": f"Expedite request: {lv.target_id} x{lu.qty}", "body": body, "lever": lv.id, "qty": lu.qty, "date": when}, lv.id))
            drafts.append(Draft("ERP_PO_CHANGE", lv.counterparty or lv.target_id, False,
                                {"change": "expedite", "part_id": lv.target_id, "qty": lu.qty, "need_date": when, "status": "draft_pending_supplier_confirmation", "est_cost": lu.cost}, lv.id))
        elif lv.type == "spot_capacity":
            body = (f"Please confirm availability of {lv.qty_cap} additional {lv.target_id} slots per week for {lv.capacity_weeks} weeks "
                    f"starting {when}, fixed fee ${lu.cost:,.2f}. Confirm the exact weeks and slot count.")
            drafts.append(Draft("SUPPLIER_REQUEST", lv.counterparty or lv.target_id, True,
                                {"subject": f"Spot capacity request: {lv.target_id}", "body": body, "lever": lv.id, "qty": lv.qty_cap, "date": when, "weeks": lv.capacity_weeks}, lv.id))
        elif lv.type == "accelerate_qualification":
            body = (f"Request to accelerate qualification of route {lv.target_id} so that it is released for production from {when}. "
                    "Please confirm feasibility, the data package required and the earliest release date.")
            drafts.append(Draft("SUPPLIER_REQUEST", lv.counterparty or lv.target_id, True,
                                {"subject": f"Qualification acceleration: {lv.target_id}", "body": body, "lever": lv.id, "date": when}, lv.id))
        else:
            o = plan.order(lv.target_id)
            body = (f"We would like to agree a delivery window extension of {lv.extend_weeks} week(s) for order {o.id} "
                    f"(original latest date {o.latest_week.isoformat()}).")
            drafts.append(Draft("CUSTOMER_COMMIT_UPDATE", lv.counterparty or o.customer_id, True,
                                {"subject": f"Window extension request: {o.id}", "body": body, "order_id": o.id, "lever": lv.id}, lv.id))
    for oc in res.orders:
        if oc.gate.value != "allow":
            drafts.append(Draft("COMPLIANCE_REVIEW", oc.order_id, False, {"order_id": oc.order_id, "gate": oc.gate.value, "action": "qualified review required before any shipment is planned"}))
            continue
        if oc.late or oc.unfulfilled:
            o = plan.order(oc.order_id)
            lines = [f"Revised delivery outlook for order {o.id} ({oc.qty} units, committed {o.committed_week.isoformat()}):",
                     f"- on time: {oc.on_time}; late: {oc.late} (up to {oc.weeks_late_max} week(s)); not deliverable in current plan: {oc.unfulfilled}."]
            for w, q in oc.schedule:
                lines.append(f"- {q} units expected week of {_fmt_week(plan, w)}")
            drafts.append(Draft("CUSTOMER_COMMIT_UPDATE", o.customer_id, True,
                                {"subject": f"Delivery update: {o.id}", "body": "\n".join(lines), "order_id": o.id, "outlook": oc.model_dump(mode="json")}))
    drafts.append(Draft("ALLOCATION_UPDATE", "ERP/APS", False, {
        "option": option.id, "plan_hash": option.plan_hash,
        "starts": [{"order": s.order_id, "route": s.route_id, "week": _fmt_week(plan, s.start_week), "started": s.started, "good": s.good,
                    "arrival": _fmt_week(plan, s.arrival_week)} for s in res.starts],
        "part_allocations": [a.model_dump(mode="json") for a in res.allocations]}))
    return drafts


class Adapter(Protocol):
    name: str

    def send(self, tenant: str, action_id: str, idem: str, atype: str, target: str, payload: dict) -> dict: ...


class OutboxAdapter:
    """Writes each action as a file. Idempotent: the same key always maps to the same file."""

    name = "outbox"

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def send(self, tenant, action_id, idem, atype, target, payload):
        d = self.root / tenant
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{atype.lower()}_{idem}.json"
        if not f.exists():
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps({"action_id": action_id, "idempotency_key": idem, "type": atype, "target": target, "payload": payload}, indent=2, default=str))
            tmp.replace(f)
        return {"adapter": self.name, "path": str(f), "idempotent_replay": False}


class WebhookAdapter:
    """POSTs to a customer-configured endpoint with an Idempotency-Key header."""

    name = "webhook"

    def __init__(self, url: str, token: str | None = None, timeout: float = 10.0, client=None):
        import httpx
        self.url, self.token, self.timeout = url, token, timeout
        self.client = client or httpx.Client(timeout=timeout)

    def send(self, tenant, action_id, idem, atype, target, payload):
        headers = {"Idempotency-Key": idem, "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        r = self.client.post(self.url, headers=headers, json={"tenant": tenant, "action_id": action_id, "type": atype, "target": target, "payload": payload})
        r.raise_for_status()
        return {"adapter": self.name, "status_code": r.status_code}


def run_with_retry(fn, attempts: int, sleep=time.sleep):
    last = None
    for i in range(attempts):
        try:
            return fn(), i + 1, None
        except Exception as e:  # adapters are external; any failure is retried then recorded
            last = f"{type(e).__name__}: {e}"
            if i < attempts - 1:
                sleep(min(2 ** i, 8))
    return None, attempts, last
