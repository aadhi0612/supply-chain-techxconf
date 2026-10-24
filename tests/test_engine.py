"""Optimiser + independent verifier. Small instances are hand-computable."""
import random
from datetime import date

import pytest

from siliconflow.domain import Lever, Order
from siliconflow.engine import compliance
from siliconflow.engine.explain import stage_ceilings
from siliconflow.engine.planner import evaluate_under, run_plan
from siliconflow.engine.recovery import build_options
from siliconflow.engine.scenarios import materialize
from siliconflow.engine.types import Scenario
from siliconflow.engine.verify import verify
from siliconflow.sample_data import wk


def order(oid, qty, cust="C1", due=date(2026, 12, 7), late=date(2026, 12, 28), **kw):
    return Order(id=oid, customer_id=cust, sku_id="S", qty=qty, requested_week=date(2026, 10, 26), committed_week=due, latest_week=late,
                 destination="US", unit_price=100, penalty_per_unit_week=1, **kw)


def test_hand_computed_capacity_and_yield_bound(make_mini):
    # 2 weeks x 100 slots, 90% yield => 180 good units, regardless of the 1000 demanded
    r = run_plan(make_mini(cap=100, weeks=2, yield_base=900), "no_spend")
    oc = r.outcome("O1")
    assert r.verified and oc.on_time + oc.late == 180 and oc.unfulfilled == 820


def test_part_supply_is_binding_when_scarcer(make_mini):
    r = run_plan(make_mini(cap=1000, weeks=2, part_qty=150), "no_spend")
    assert r.outcome("O1").on_time == 135  # 150 started * 0.9


def test_deadline_respected_hard_limit(make_mini):
    # capacity only far too late to arrive before latest_week => nothing delivered
    p = make_mini(cap=100, weeks=1)
    p.capacity[0] = p.capacity[0].model_copy(update={"week": wk(12)})
    r = run_plan(p, "no_spend")
    assert r.outcome("O1").on_time + r.outcome("O1").late == 0


def test_protected_order_beats_higher_priority_weight(make_mini):
    orders = [order("BIG", 200, "C1", priority=1, protected=False), order("PROT", 200, "C2", priority=5, protected=True)]
    p = make_mini(cap=100, weeks=2, yield_base=1000, orders=orders)  # only 200 units possible in total
    r = run_plan(p, "no_spend")
    assert r.outcome("PROT").on_time == 200 and r.outcome("BIG").on_time == 0


def test_priority_weights_decide_when_not_protected(make_mini):
    orders = [order("LO", 200, "C1", priority=5), order("HI", 200, "C2", priority=1)]
    p = make_mini(cap=100, weeks=2, yield_base=1000, orders=orders)
    r = run_plan(p, "no_spend")
    assert r.outcome("HI").on_time == 200 and r.outcome("LO").on_time == 0


def test_all_or_nothing_order(make_mini):
    orders = [order("AON", 150, allow_partial=False)]
    r = run_plan(make_mini(cap=100, weeks=1, yield_base=1000, orders=orders), "no_spend")  # only 100 possible < 150
    assert r.outcome("AON").on_time + r.outcome("AON").late == 0 and r.verified


def test_min_ship_quantity(make_mini):
    orders = [order("M", 500, min_ship_qty=300)]
    r = run_plan(make_mini(cap=100, weeks=2, yield_base=1000, orders=orders), "no_spend")  # 200 < 300
    assert r.outcome("M").on_time + r.outcome("M").late == 0


def test_blocked_order_releases_supply_to_others(sample):
    base = run_plan(sample, "no_spend")
    assert all(base.outcome(o).on_time + base.outcome(o).late == 0 for o in ("ORD-1005", "ORD-1006"))
    p = sample.copy_deep()
    p.compliance.decisions["CLASS-A"]["G3"] = "allowed"  # unblock 1005
    p.orders[4] = p.orders[4].model_copy(update={"end_use": "datacenter"})
    r2 = run_plan(p, "no_spend")
    assert r2.outcome("ORD-1005").on_time + r2.outcome("ORD-1005").late > 0
    assert r2.totals["delivered_units"] >= base.totals["delivered_units"] - 1  # un-blocking can only add demand, supply is shared
    assert r2.verified


def test_expedite_lever_used_only_when_it_helps_and_costs_counted(make_mini):
    lv = Lever(id="L", type="expedite_supply", target_id="P", qty_cap=1000, available_week=wk(1), unit_cost=2)
    p = make_mini(cap=1000, weeks=2, part_qty=150, levers=[lv])
    nospend, bal = run_plan(p, "no_spend"), run_plan(p, "balanced")
    assert nospend.outcome("O1").on_time == 135 and not nospend.levers
    assert bal.outcome("O1").on_time > 135 and bal.levers and bal.levers[0].cost == bal.levers[0].qty * 2
    assert bal.verified
    # no benefit => no spend
    p2 = make_mini(cap=1000, weeks=2, part_qty=10_000, levers=[lv])
    assert not run_plan(p2, "balanced").levers


def test_spend_cap_is_hard(make_mini):
    lv = Lever(id="L", type="spot_capacity", target_id="R", qty_cap=500, capacity_weeks=2, available_week=wk(1), fixed_cost=10_000)
    p = make_mini(cap=100, weeks=2, yield_base=1000, levers=[lv])
    assert run_plan(p, "balanced").levers
    p.config.recovery_spend_cap = 5_000
    assert not run_plan(p, "balanced").levers


def test_accelerate_qualification_and_window_levers(make_mini):
    p = make_mini(cap=100, weeks=3, yield_base=1000, orders=[order("O1", 300, due=date(2026, 11, 30), late=date(2026, 12, 7))])
    # qualify route only from week 2 via lever; otherwise unusable
    p.routes[0].qualification = "pending"
    lv = Lever(id="Q", type="accelerate_qualification", target_id="RT", available_week=wk(2), fixed_cost=1)
    p.levers = [lv]
    assert run_plan(p, "no_spend").outcome("O1").on_time == 0
    r = run_plan(p, "balanced")
    assert r.verified and [l.lever_id for l in r.levers] == ["Q"] and all(s.start_week >= 2 for s in r.starts)


def test_verifier_catches_tampering(sample):
    gates = compliance.evaluate_all(sample)
    r = run_plan(sample, "no_spend", gates=gates)
    assert r.verified
    mat = materialize(sample, Scenario.base)
    bad = r.model_copy(deep=True)
    bad.starts[0].started += 5000
    bad.starts[0].good += 5000
    errs = verify(sample, mat, gates, bad)
    assert errs and any("capacity" in e or "supply" in e or "BOM" in e for e in errs)
    bad2 = r.model_copy(deep=True)
    bad2.starts.append(bad2.starts[0].model_copy(update={"order_id": "ORD-1005"}))
    assert any("compliance gate" in e for e in verify(sample, mat, gates, bad2))


def test_scenarios_are_ordered_and_degrade_monotonically(sample):
    base = run_plan(sample, "no_spend")
    best, worst = evaluate_under(sample, base, Scenario.best), evaluate_under(sample, base, Scenario.worst)
    assert worst.totals["on_time_units"] <= base.totals["on_time_units"] <= best.totals["on_time_units"] + 1
    assert worst.verified or worst.verification_errors == []


def test_unconfirmed_supply_is_haircut_in_base_and_absent_in_worst(sample):
    base, worst = materialize(sample, Scenario.base), materialize(sample, Scenario.worst)
    best = materialize(sample, Scenario.best)
    assert base.part_arrivals["HBM-B"][3] == 14400 and worst.part_arrivals["HBM-B"][3] == 0 and best.part_arrivals["HBM-B"][3] == 24000


def test_stage_ceilings_overstate_when_orders_compete(sample):
    ceil = {c.stage: c.units for c in stage_ceilings(sample, "ORD-1001")}
    assert min(ceil.values()) >= 4000  # alone, order 1001 would be fine
    r = run_plan(sample, "no_spend")
    assert sum(r.outcome(o).on_time for o in ("ORD-1001", "ORD-1002", "ORD-1003")) <= 9000


@pytest.mark.parametrize("seed", range(6))
def test_random_instances_always_verify_and_respect_bounds(make_mini, seed):
    rnd = random.Random(seed)
    orders = [order(f"O{i}", rnd.randint(20, 300), cust=rnd.choice(["C1", "C2"]), priority=rnd.randint(1, 5), protected=rnd.random() < 0.3,
                    allow_partial=rnd.random() < 0.7, due=date(2026, 12, 7 + 7 * rnd.randint(0, 2)), late=date(2027, 1, 11)) for i in range(rnd.randint(2, 5))]
    p = make_mini(cap=rnd.randint(30, 200), weeks=rnd.randint(1, 4), yield_base=rnd.choice([800, 900, 950]), part_qty=rnd.randint(100, 800), orders=orders)
    for prof in ("no_spend", "robust"):
        r = run_plan(p, prof)
        assert r.solver_status in ("OPTIMAL", "FEASIBLE") and r.verified, r.verification_errors
        delivered = sum(o.on_time + o.late for o in r.orders)
        yld = (p.routes[0].yield_pm.low if prof == "robust" else p.routes[0].yield_pm.base) / 1000
        bound = min(sum(b.reserved for b in p.capacity), sum(c.qty for c in p.commitments)) * yld
        assert delivered <= bound + 1e-6
        for o in r.orders:
            assert o.on_time + o.late <= o.qty


@pytest.fixture(scope="module")
def incident_options():
    from siliconflow.events import apply_event
    from siliconflow.sample_data import build_sample, sample_incident_events
    p = build_sample()
    for e in sample_incident_events():
        p, _ = apply_event(p, e)
    return p, build_options(p)


def test_options_are_verified_ranked_and_explained(incident_options):
    p, s = incident_options
    assert s.options and all(o.result.verified for o in s.options)
    assert [o.rank for o in s.options] == list(range(1, len(s.options) + 1)) and s.options[0].recommended and sum(o.recommended for o in s.options) == 1
    by = {o.profile: o for o in s.options}
    assert by["balanced"].result.totals["on_time_units"] >= by["no_spend"].result.totals["on_time_units"]
    assert by["no_spend"].result.levers == []
    rng = {r.scenario: r.on_time_units for r in by["balanced"].scenario_range}
    assert rng["worst"] <= rng["base"] <= rng["best"] + 1
    assert s.binding and s.binding.pools, "packaging must show up as binding"
    assert any(pv.pool.startswith("resource:PKG") for pv in s.binding.pools)


def test_incident_hurts_then_levers_recover(incident_options):
    from siliconflow.sample_data import build_sample
    p, s = incident_options
    before = run_plan(build_sample(), "no_spend")
    by = {o.profile: o for o in s.options}
    assert by["no_spend"].result.totals["on_time_units"] < before.totals["on_time_units"] - 500
    assert by["balanced"].result.totals["on_time_units"] > by["no_spend"].result.totals["on_time_units"] + 300
    # protected order survives every option
    assert all(o.result.outcome("ORD-1001").on_time == 4000 for o in s.options)
