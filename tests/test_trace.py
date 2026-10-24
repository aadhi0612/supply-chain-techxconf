"""The confidentiality claim, tested: what a viewer's model context contains, and that attestations really verify."""

import json

import pytest

from siliconflow.trace import narrate
from siliconflow.trace.graph import FIRST_PACKAGE, N_PACKAGES, SUPPLIERS, TODAY, build_graph
from siliconflow.trace.resolve import VIEWERS, Resolver


@pytest.fixture(scope="module")
def rs():
    return Resolver(secret="test-secret")


def test_graph_is_deterministic_and_dated_sanely():
    a, b = build_graph(), build_graph()
    assert [(p.id, p.lots, p.status) for p in a.packages.values()] == [(p.id, p.lots, p.status) for p in b.packages.values()]
    assert len(a.packages) == N_PACKAGES and 1124544 in a.packages
    for l in a.lots.values():
        assert all(a.lots[p].made <= l.made for p in l.parents)  # a lot is never made before its inputs
    assert all(e["d"] <= TODAY for p in a.packages.values() for e in p.events)


def test_each_supplier_has_one_tier_so_names_cannot_leak_by_correlation(rs):
    for l in rs.g.lots.values():
        assert SUPPLIERS[l.supplier][1] >= 2
    assert {SUPPLIERS[s][1] for s in SUPPLIERS} == {1, 2, 3, 4, 5}


@pytest.mark.parametrize("viewer", sorted(VIEWERS))
def test_no_hidden_name_or_lot_id_in_any_context_sent_to_the_model(rs, viewer):
    hidden = rs.hidden_terms(viewer)
    assert hidden
    for pid in range(FIRST_PACKAGE, FIRST_PACKAGE + N_PACKAGES, 7):
        ctx = narrate.context(rs.package(pid, viewer)).lower()
        assert not [t for t in hidden if t.lower() in ctx], (viewer, pid)
    for lot in list(rs.g.lots)[:40]:
        r = rs.lot(rs.ref(lot, viewer), viewer)
        assert r and not [t for t in hidden if t.lower() in narrate.context(r).lower()]


def test_viewer_visibility_grows_by_one_tier(rs):
    seen = {v: {n["tier"] for n in rs.package(1124544, v)["chain"] if n["named"]} for v in VIEWERS}
    assert seen["oem"] == {1} and seen["tier1"] == {1, 2} and seen["tier2"] == {1, 2, 3}


def test_hidden_real_lot_id_is_indistinguishable_from_unknown(rs):
    deep = next(l for l in rs.g.lots if rs.tier(l) == 5)
    assert rs.lot(deep, "oem") is None and rs.lot("LOT-NOPE-1", "oem") is None
    assert rs.lot(rs.token(deep), "oem")["kind"] == "lot"  # the pseudonym the viewer was shown still works


def test_attestation_verifies_and_detects_tampering():
    r = Resolver(build_graph(), "k")
    t = r.package(1124544, "oem")
    assert t["attestations"] and all(a["verified"] for a in t["attestations"]) and t["verdict"]["genuine"]
    lot = next(iter(r.g.packages[1124544].lots.values()))
    parent = next(iter(r.g.ancestors[lot]))
    r.g.lots[parent].supplier = "WaferCo" if r.g.lots[parent].supplier != "WaferCo" else "PolySiCo"  # swap who supposedly made it
    t2 = r.package(1124544, "oem")
    assert not t2["verdict"]["attestations_verified"] and not t2["verdict"]["genuine"]


def test_hold_reaches_oem_without_naming_the_lot(rs):
    t = rs.package(1124101, "oem")
    assert not t["verdict"]["genuine"] and t["alerts"] and "LOT-S-77" not in json.dumps(t) and "SubstrateCo" not in json.dumps(t)
    assert "LOT-S-77" in json.dumps(rs.package(1124101, "tier1"))  # Tier-1 is entitled to its direct supplier's lot


def test_free_text_lookup(rs):
    assert rs.lookup("where is package 1124544 from?", "oem")["kind"] == "package"
    assert rs.lookup("which packages use LOT-S-77", "tier1")["kind"] == "lot"
    assert rs.lookup("status of ORD-1002", "oem")["kind"] == "order" and rs.lookup("status of ORD-1002", "tier1")["kind"] == "catalog"
    assert rs.lookup("tell me a joke", "oem")["kind"] == "catalog"


def test_question_masking_and_output_guard(rs):
    q, hit = narrate.sanitize_question("is WaferCo or QZ-1234 involved in 1124544?", rs, "oem")
    assert hit and "WaferCo" not in q and "1124544" in q
    assert narrate.guard("made by WaferCo", rs, "oem") == "WaferCo" and narrate.guard("made by BumpCo? no", rs, "tier1") is None
    assert narrate.guard("Undisclosed Tier-3 supplier #AB12", rs, "oem") is None


def test_fallback_text_is_complete_and_clean(rs):
    for v in VIEWERS:
        txt = narrate.fallback(rs.package(1124544, v))
        assert "Chain:" in txt and "Attestations:" in txt and not [t for t in rs.hidden_terms(v) if t.lower() in txt.lower()]
