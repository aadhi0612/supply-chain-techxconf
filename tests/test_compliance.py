from datetime import date

from siliconflow.domain import License
from siliconflow.engine import compliance
from siliconflow.engine.types import GateStatus


def gate(plan, oid):
    return compliance.evaluate_order(plan, plan.order(oid))


def test_sample_gates(sample):
    assert gate(sample, "ORD-1001").status == GateStatus.allow
    assert gate(sample, "ORD-1004").status == GateStatus.allow and gate(sample, "ORD-1004").license_id == "LIC-0042"
    assert gate(sample, "ORD-1005").status == GateStatus.blocked
    g = gate(sample, "ORD-1006")
    assert g.status == GateStatus.needs_license and "no valid license" in g.reasons[0]


def test_unmapped_destination_requires_review_not_allow(sample):
    p = sample.copy_deep()
    p.compliance.country_groups.pop("US")
    assert gate(p, "ORD-1001").status == GateStatus.review


def test_no_ruleset_never_allows(sample):
    p = sample.copy_deep()
    p.compliance = None
    assert all(g.status == GateStatus.review for g in compliance.evaluate_all(p).values())


def test_license_quantity_and_validity_enforced(sample):
    p = sample.copy_deep()
    p.licenses[0] = p.licenses[0].model_copy(update={"qty_limit": 999})
    g = gate(p, "ORD-1004")
    assert g.status == GateStatus.needs_license and any("covers 999" in r for r in g.reasons)
    p.licenses[0] = License(id="L", export_class="CLASS-A", destination="SG", end_user="Vega Labs", qty_limit=1000, valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31))
    assert gate(p, "ORD-1004").status == GateStatus.needs_license


def test_license_bound_to_end_user(sample):
    p = sample.copy_deep()
    p.orders[3] = p.orders[3].model_copy(update={"end_user": "Someone Else"})
    assert gate(p, "ORD-1004").status == GateStatus.needs_license


def test_denied_party_blocks_even_when_allowed(sample):
    p = sample.copy_deep()
    p.orders[0] = p.orders[0].model_copy(update={"end_user": "ACME shell trading"})
    assert gate(p, "ORD-1001").status == GateStatus.blocked


def test_missing_end_use_for_sensitive_group_is_review(sample):
    p = sample.copy_deep()
    p.orders[3] = p.orders[3].model_copy(update={"end_use": ""})
    assert gate(p, "ORD-1004").status == GateStatus.review


def test_unknown_export_class_is_review(sample):
    p = sample.copy_deep()
    p.skus[0] = p.skus[0].model_copy(update={"export_class": "MYSTERY"})
    assert gate(p, "ORD-1001").status == GateStatus.review
