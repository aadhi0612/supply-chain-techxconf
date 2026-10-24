"""Deterministic, versioned trade-compliance gate.

This evaluates a customer-maintained decision table. It never *grants* permission by
inference: anything not positively resolved becomes ``review`` or ``blocked`` and is
routed to a qualified human. It is evidence assistance, not legal advice.
"""
from __future__ import annotations

from ..domain import Order, PlanInput
from .types import ComplianceGate, GateStatus


def evaluate_order(plan: PlanInput, order: Order, ship_week: int | None = None) -> ComplianceGate:
    rs = plan.compliance
    if rs is None:
        return ComplianceGate(
            order_id=order.id, status=GateStatus.review,
            reasons=["No compliance rule set loaded; a human must clear this shipment."],
        )
    sku = plan.sku(order.sku_id)
    reasons: list[str] = []
    evidence = {
        "ruleset_version": rs.version, "ruleset_effective": rs.effective.isoformat(),
        "export_class": sku.export_class, "destination": order.destination,
        "end_user": order.end_user or None, "end_use": order.end_use or None,
    }
    ev = {"order_id": order.id, "ruleset_version": rs.version, "evidence": evidence}

    if order.end_user and order.end_user.strip().lower() in {p.strip().lower() for p in rs.denied_parties}:
        return ComplianceGate(status=GateStatus.blocked, reasons=[f"End user '{order.end_user}' is on the denied-party list."], **ev)

    group = rs.country_groups.get(order.destination)
    if group is None:
        return ComplianceGate(
            status=GateStatus.review,
            reasons=[f"Destination '{order.destination}' is not mapped to a country group in rule set {rs.version}."],
            **ev,
        )
    evidence["country_group"] = group
    if group in rs.end_use_required_groups and not order.end_use:
        reasons.append(f"End-use statement required for country group {group} but missing.")
    table = rs.decisions.get(sku.export_class)
    if table is None or group not in table:
        return ComplianceGate(
            status=GateStatus.review,
            reasons=[f"No rule for export class '{sku.export_class}' / group '{group}'."] + reasons, **ev,
        )
    decision = table[group]
    evidence["decision"] = decision
    if decision == "prohibited":
        return ComplianceGate(status=GateStatus.blocked, reasons=[f"Rule set {rs.version}: prohibited for {sku.export_class} -> {group}."], **ev)
    if reasons:
        return ComplianceGate(status=GateStatus.review, reasons=reasons, **ev)
    if decision == "allowed":
        return ComplianceGate(status=GateStatus.allow, reasons=[f"Rule set {rs.version}: allowed."], **ev)

    # license_required: need a license that covers destination/class/end user/quantity/validity
    ref = plan.date_of(ship_week if ship_week is not None else plan.wk(order.committed_week)).isoformat()
    for lic in plan.licenses:
        if lic.export_class != sku.export_class or lic.destination != order.destination:
            continue
        if lic.end_user and lic.end_user.strip().lower() != order.end_user.strip().lower():
            continue
        if not (lic.valid_from.isoformat() <= ref <= lic.valid_to.isoformat()):
            continue
        if lic.qty_limit < order.qty:
            reasons.append(f"License {lic.id} covers {lic.qty_limit} units, order needs {order.qty}.")
            continue
        evidence["license"] = lic.model_dump(mode="json")
        return ComplianceGate(
            status=GateStatus.allow, license_id=lic.id,
            reasons=[f"License {lic.id} covers destination, class, end user, quantity and date."], **ev,
        )
    return ComplianceGate(
        status=GateStatus.needs_license,
        reasons=[f"License required for {sku.export_class} -> {order.destination}; no valid license on file."] + reasons,
        **ev,
    )


def evaluate_all(plan: PlanInput) -> dict[str, ComplianceGate]:
    return {o.id: evaluate_order(plan, o) for o in plan.orders}
