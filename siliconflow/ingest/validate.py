"""Referential-integrity validation and data-quality reporting for a PlanInput."""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

from pydantic import Field

from ..domain import Model, PlanInput
from ..engine import compliance
from ..engine.scenarios import materialize
from ..engine.types import Scenario


class Issue(Model):
    severity: str  # error | warning | info
    code: str
    message: str
    subject: str = ""


class DataQualityReport(Model):
    metrics: dict[str, float] = Field(default_factory=dict)
    issues: list[Issue] = Field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


def validate_integrity(plan: PlanInput) -> list[Issue]:
    out: list[Issue] = []

    def err(code, msg, subj=""):
        out.append(Issue(severity="error", code=code, message=msg, subject=subj))

    def warn(code, msg, subj=""):
        out.append(Issue(severity="warning", code=code, message=msg, subject=subj))

    for name, items in [("customer", plan.customers), ("part", plan.parts), ("sku", plan.skus), ("site", plan.sites),
                        ("resource", plan.resources), ("route", plan.routes), ("commitment", plan.commitments),
                        ("lot", plan.inventory), ("order", plan.orders), ("lever", plan.levers), ("license", plan.licenses)]:
        for ident, n in Counter(i.id for i in items).items():
            if n > 1:
                err("duplicate_id", f"duplicate {name} id '{ident}'", ident)
    parts = {p.id: p for p in plan.parts}
    skus = {s.id: s for s in plan.skus}
    res = {r.id: r for r in plan.resources}
    sites = {s.id for s in plan.sites}
    custs = {c.id for c in plan.customers}
    lanes = {l.destination for l in plan.lanes}
    routes = {r.id: r for r in plan.routes}
    for s in plan.skus:
        for line in s.bom:
            for p in line.allowed_parts:
                if p not in parts:
                    err("bom_unknown_part", f"SKU {s.id} BOM references unknown part {p}", s.id)
                elif parts[p].kind != line.kind:
                    err("bom_kind_mismatch", f"SKU {s.id}: part {p} is kind '{parts[p].kind}', BOM line is '{line.kind}'", s.id)
    for r in plan.resources:
        if r.site_id not in sites:
            err("resource_unknown_site", f"resource {r.id} references unknown site {r.site_id}", r.id)
    for r in plan.routes:
        if r.sku_id not in skus:
            err("route_unknown_sku", f"route {r.id} references unknown SKU {r.sku_id}", r.id)
            continue
        for st in r.steps:
            if st.resource_id not in res:
                err("route_unknown_resource", f"route {r.id} step references unknown resource {st.resource_id}", r.id)
        if r.lead_weeks < max(st.offset_weeks for st in r.steps):
            err("route_lead_lt_offset", f"route {r.id} lead_weeks is shorter than its last step offset", r.id)
        bom = {ln.kind: set(ln.allowed_parts) for ln in skus[r.sku_id].bom}
        for kind, allowed in r.part_constraints.items():
            if kind not in bom:
                err("route_constraint_kind", f"route {r.id} constrains kind '{kind}' absent from the BOM", r.id)
            elif not set(allowed) <= bom[kind]:
                err("route_constraint_unqualified", f"route {r.id} allows parts not qualified in the BOM: {sorted(set(allowed) - bom[kind])}", r.id)
        if r.qualification == "qualified" and r.qualified_from is None:
            pass
    for b in plan.capacity:
        if b.resource_id not in res:
            err("capacity_unknown_resource", f"capacity bucket references unknown resource {b.resource_id}", b.resource_id)
    for c in plan.commitments:
        if c.part_id not in parts:
            err("commitment_unknown_part", f"commitment {c.id} references unknown part {c.part_id}", c.id)
    for lot in plan.inventory:
        if lot.part_id not in parts:
            err("lot_unknown_part", f"lot {lot.id} references unknown part {lot.part_id}", lot.id)
    for o in plan.orders:
        if o.customer_id not in custs:
            err("order_unknown_customer", f"order {o.id} references unknown customer {o.customer_id}", o.id)
        if o.sku_id not in skus:
            err("order_unknown_sku", f"order {o.id} references unknown SKU {o.sku_id}", o.id)
        elif not any(r.sku_id == o.sku_id for r in plan.routes):
            warn("order_no_route", f"SKU {o.sku_id} (order {o.id}) has no production route", o.id)
        if o.destination not in lanes:
            err("order_no_lane", f"order {o.id}: no logistics lane for destination {o.destination}", o.id)
    for lv in plan.levers:
        targets = {"expedite_supply": parts, "spot_capacity": res, "accelerate_qualification": routes,
                   "extend_window": {o.id: o for o in plan.orders}}[lv.type]
        if lv.target_id not in targets:
            err("lever_unknown_target", f"lever {lv.id} targets unknown {lv.type} object {lv.target_id}", lv.id)
        if lv.type in ("expedite_supply", "spot_capacity", "accelerate_qualification") and lv.available_week is None:
            err("lever_no_date", f"lever {lv.id} needs available_week", lv.id)
        if lv.type in ("expedite_supply", "spot_capacity") and lv.qty_cap <= 0:
            err("lever_no_cap", f"lever {lv.id} needs qty_cap > 0", lv.id)
        if lv.type == "extend_window" and lv.extend_weeks <= 0:
            err("lever_no_extension", f"lever {lv.id} needs extend_weeks > 0", lv.id)
        if lv.type == "accelerate_qualification" and lv.target_id in routes and routes[lv.target_id].qualification == "qualified":
            warn("lever_redundant", f"lever {lv.id}: route {lv.target_id} is already qualified", lv.id)
    if len({(l.destination) for l in plan.lanes}) != len(plan.lanes):
        err("duplicate_lane", "duplicate logistics lane destination")
    if plan.compliance is None:
        warn("no_compliance_rules", "no compliance rule set loaded: every order will require manual review")
    else:
        classes = set(plan.compliance.decisions)
        for s in plan.skus:
            if s.export_class not in classes:
                warn("sku_unclassified", f"SKU {s.id} export class '{s.export_class}' has no rule in {plan.compliance.version}", s.id)
    return out


def data_quality(plan: PlanInput) -> DataQualityReport:
    issues = validate_integrity(plan)
    stale_cut = plan.as_of - timedelta(days=plan.config.stale_after_days)
    m: dict[str, float] = {}
    live_commit = [c for c in plan.commitments if c.status != "cancelled"]
    backed = [c for c in live_commit if c.status in ("confirmed", "acknowledged")]
    m["supplier_commitments_total"] = len(live_commit)
    m["pct_commitments_source_confirmed"] = round(100 * len(backed) / len(live_commit), 1) if live_commit else 100.0
    facts = ([(f"commitment {c.id}", c.provenance) for c in live_commit] + [(f"lot {l.id}", l.provenance) for l in plan.inventory]
             + [(f"capacity {b.resource_id}@{b.week}", b.provenance) for b in plan.capacity])
    stale = [(n, p) for n, p in facts if p.observed_at < stale_cut]
    lowc = [(n, p) for n, p in facts if p.confidence < 0.7]
    m["facts_total"] = len(facts)
    m["pct_facts_stale"] = round(100 * len(stale) / len(facts), 1) if facts else 0.0
    m["pct_facts_low_confidence"] = round(100 * len(lowc) / len(facts), 1) if facts else 0.0
    for n, p in stale[:25]:
        issues.append(Issue(severity="warning", code="stale_fact", message=f"{n} last observed {p.observed_at:%Y-%m-%d} via {p.source}", subject=n))
    for n, p in lowc[:25]:
        issues.append(Issue(severity="warning", code="low_confidence_fact", message=f"{n} has confidence {p.confidence:.2f} (source {p.source})", subject=n))
    pending = [r.id for r in plan.routes if r.qualification == "pending"]
    if pending:
        issues.append(Issue(severity="info", code="routes_pending_qualification", message=f"routes pending qualification: {', '.join(pending)}"))
    gates = compliance.evaluate_all(plan) if not [i for i in issues if i.severity == "error"] else {}
    held = [g for g in gates.values() if not g.plannable]
    m["orders_total"] = len(plan.orders)
    m["orders_compliance_held"] = len(held)
    for g in held:
        issues.append(Issue(severity="warning", code="order_compliance_hold", subject=g.order_id,
                            message=f"{g.order_id}: {g.status.value} - {'; '.join(g.reasons)}"))
    if not [i for i in issues if i.severity == "error"]:
        mat = materialize(plan, Scenario.base)
        demand_by_sku = Counter()
        for o in plan.orders:
            demand_by_sku[o.sku_id] += o.qty
        m["demand_units_total"] = sum(demand_by_sku.values())
        m["nominal_supply_gap_parts"] = 0
        for s in plan.skus:
            for ln in s.bom:
                supply = sum(mat.cum_supply(p)[-1] for p in ln.allowed_parts if p in mat.part_arrivals)
                if supply < demand_by_sku[s.id] * ln.qty_per_unit:
                    m["nominal_supply_gap_parts"] += 1
                    issues.append(Issue(severity="warning", code="nominal_supply_short",
                                        message=f"{s.id}: {ln.kind} supply {supply} is below nominal need {demand_by_sku[s.id] * ln.qty_per_unit} (before yield)", subject=s.id))
    return DataQualityReport(metrics=m, issues=issues)
