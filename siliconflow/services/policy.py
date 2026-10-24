"""Approval policy: who must sign off on a recovery plan before anything leaves the building."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import PlanInput
from ..engine.impact import ImpactReport
from ..engine.recovery import RecoveryOption
from ..engine.types import ComplianceGate

ROLES = {"viewer", "planner", "procurement", "finance", "quality", "sales", "compliance", "admin"}
WRITE_ROLES = {"planner", "admin"}


@dataclass
class ExecutionPolicy:
    """shadow: recommend only. draft: create drafts for humans to send. execute: call adapters for allow-listed types."""

    mode: str = "draft"
    execute_allowlist: set[str] = field(default_factory=lambda: {"ALLOCATION_UPDATE", "SUPPLIER_REQUEST", "ERP_PO_CHANGE", "CUSTOMER_COMMIT_UPDATE"})
    require_distinct_proposer: bool = True
    max_attempts: int = 3

    def __post_init__(self):
        if self.mode not in ("shadow", "draft", "execute"):
            raise ValueError("mode must be shadow|draft|execute")


def required_approvals(
    plan: PlanInput, option: RecoveryOption, impact: ImpactReport | None,
    gates_before: dict[str, ComplianceGate] | None, gates_after: dict[str, ComplianceGate],
) -> list[dict]:
    req: dict[str, list[str]] = {"planner": ["Owns the allocation plan."]}
    res = option.result
    spend = sum(lu.cost for lu in res.levers)
    for lu in res.levers:
        lv = next(x for x in plan.levers if x.id == lu.lever_id)
        for role in lv.approver_roles:
            req.setdefault(role, []).append(f"Lever {lv.id}: {lv.description or lv.type}.")
    if spend > plan.config.spend_requires_finance_above:
        req.setdefault("finance", []).append(f"Recovery spend ${spend:,.0f} exceeds ${plan.config.spend_requires_finance_above:,.0f}.")
    changed = [oid for oid, g in gates_after.items() if gates_before is None or gates_before.get(oid) is None or gates_before[oid].status != g.status]
    if changed:
        req.setdefault("compliance", []).append("Compliance status changed for " + ", ".join(sorted(changed)) + ".")
    for oc in res.orders:
        if oc.gate.value != "allow" and oc.status == "blocked":
            req.setdefault("compliance", []).append(f"{oc.order_id} is held by the compliance gate; plan excludes it.")
            break
    if any(oc.late or oc.unfulfilled for oc in res.orders if oc.gate.value == "allow"):
        req.setdefault("sales", []).append("Customer commitments move or cannot be met; customer communication required.")
    return [{"role": r, "reasons": sorted(set(rs))} for r, rs in sorted(req.items())]
