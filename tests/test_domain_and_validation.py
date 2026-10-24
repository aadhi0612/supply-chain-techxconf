from datetime import date

import pytest
from pydantic import ValidationError

from siliconflow.domain import CapacityBucket, Order, PlanInput, YieldRange
from siliconflow.ingest.importers import ImportError_, layer_tables, parse_orders
from siliconflow.ingest.validate import data_quality, validate_integrity


def test_dates_normalise_to_monday(make_mini):
    p = make_mini()
    assert all(b.week.weekday() == 0 for b in p.capacity)
    o = Order(id="x", customer_id="c", sku_id="s", qty=1, requested_week="2026-11-04", committed_week="2026-11-04", latest_week="2026-11-05", destination="US")
    assert o.committed_week == date(2026, 11, 2)


def test_invalid_values_rejected():
    with pytest.raises(ValidationError):
        YieldRange(low=900, base=800, high=950)
    with pytest.raises(ValidationError):
        CapacityBucket(resource_id="r", week="2026-11-02", nominal=10, reserved=11)
    with pytest.raises(ValidationError):
        Order(id="x", customer_id="c", sku_id="s", qty=1, requested_week="2026-11-02", committed_week="2026-11-16", latest_week="2026-11-02", destination="US")
    with pytest.raises(ValidationError):  # unknown fields are errors, not silently dropped
        PlanInput.model_validate({"plan_start": "2026-10-26", "bogus": 1})


def test_content_hash_ignores_as_of_but_not_data(make_mini):
    a = make_mini()
    b = a.copy_deep()
    b.as_of = b.as_of.replace(year=2030)
    assert a.content_hash() == b.content_hash()
    b.capacity[0].reserved -= 1
    assert a.content_hash() != b.content_hash()


def test_sample_is_valid_and_dq_reports(sample):
    assert not [i for i in validate_integrity(sample) if i.severity == "error"]
    dq = data_quality(sample)
    assert dq.metrics["orders_compliance_held"] == 2
    assert 0 < dq.metrics["pct_commitments_source_confirmed"] < 100
    codes = {i.code for i in dq.issues}
    assert {"low_confidence_fact", "order_compliance_hold", "nominal_supply_short"} <= codes


def test_integrity_errors_are_specific(sample):
    p = sample.copy_deep()
    p.skus[0].bom[0].allowed_parts = ["NOPE"]
    p.routes[0].part_constraints = {"substrate": ["HBM-A"]}
    p.orders[0].destination = "ATLANTIS"
    p.lanes = [l for l in p.lanes if l.destination != "ATLANTIS"]
    p.levers[0].target_id = "NOPE"
    codes = {i.code for i in validate_integrity(p) if i.severity == "error"}
    assert {"bom_unknown_part", "route_constraint_unqualified", "order_no_lane", "lever_unknown_target"} <= codes


def test_duplicate_ids_detected(sample):
    p = sample.copy_deep()
    p.orders.append(p.orders[0].model_copy())
    assert "duplicate_id" in {i.code for i in validate_integrity(p)}


def test_csv_orders_roundtrip_and_line_errors():
    good = "id,customer_id,sku_id,qty,requested_week,committed_week,latest_week,priority,protected,unit_price,penalty_per_unit_week,allow_partial,min_ship_qty,destination\n" \
           "O9,C1,S,10,2026-11-02,2026-11-09,2026-11-16,1,true,5,1,false,0,US\n"
    o = parse_orders(good)[0]
    assert o.protected and not o.allow_partial and o.priority == 1
    bad = good + "O10,C1,S,notanumber,2026-11-02,2026-11-09,2026-11-16,1,true,5,1,false,0,US\n"
    with pytest.raises(ImportError_) as e:
        parse_orders(bad)
    assert "line 3" in str(e.value)


def test_layer_tables_replaces_only_named_tables(sample):
    csv = "id,customer_id,sku_id,qty,requested_week,committed_week,latest_week,destination\nOX,HYPERION,AX-900,5,2026-12-07,2026-12-07,2026-12-14,US\n"
    new = layer_tables(sample, {"orders": csv})
    assert [o.id for o in new.orders] == ["OX"] and len(new.commitments) == len(sample.commitments)
    with pytest.raises(ImportError_):
        layer_tables(sample, {"nonsense": "a\n1\n"})
