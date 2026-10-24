"""Synthetic multi-tier supply graph for the provenance chatbot. Everything here is invented.

Tier = hops from the OEM along the chain (T1 supplies the OEM, T2 supplies a T1, ...). A supplier has one fixed tier, so a name
hidden at one depth can never be visible at another and leak by correlation. Pure stdlib so it ships in a tiny Lambda zip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

TODAY = date(2026, 10, 24)
FIRST_PACKAGE = 1124000
N_PACKAGES = 600

# id -> (display name, tier, what it does, country, region)
SUPPLIERS: dict[str, tuple[str, int, str, str, str]] = {
    "OSAT-A": ("OSAT A", 1, "2.5D packaging and test", "TW", "East Asia"),
    "OSAT-B": ("OSAT B", 1, "2.5D packaging and test", "MY", "Southeast Asia"),
    "OSAT-C": ("OSAT C", 1, "2.5D packaging and test", "US", "North America"),
    "FoundryCo": ("FoundryCo", 2, "logic die (N3-class)", "TW", "East Asia"),
    "MemCo": ("MemCo", 2, "HBM3E 12-Hi stacks", "KR", "East Asia"),
    "MemCo2": ("MemCo2", 2, "HBM3E 12-Hi stacks", "SG", "Southeast Asia"),
    "SubstrateCo": ("SubstrateCo", 2, "package substrate type 1", "JP", "East Asia"),
    "SubstrateCo2": ("SubstrateCo2", 2, "package substrate type 2", "JP", "East Asia"),
    "BumpCo": ("BumpCo", 2, "solder bumps and underfill", "SG", "Southeast Asia"),
    "WaferCo": ("WaferCo", 3, "300 mm silicon wafers", "JP", "East Asia"),
    "DramWaferCo": ("DramWaferCo", 3, "DRAM wafers", "KR", "East Asia"),
    "NeonGasCo": ("NeonGasCo", 3, "lithography gas mix", "PL", "Europe"),
    "ResinCo": ("ResinCo", 3, "build-up film resin", "JP", "East Asia"),
    "CopperFoilCo": ("CopperFoilCo", 3, "electrodeposited copper foil", "TW", "East Asia"),
    "AlloyCo": ("AlloyCo", 3, "solder alloy", "MX", "North America"),
    "PolySiCo": ("PolySiCo", 4, "polysilicon", "DE", "Europe"),
    "EpoxyChemCo": ("EpoxyChemCo", 4, "epoxy chemistry", "DE", "Europe"),
    "CopperOreCo": ("CopperOreCo", 4, "refined copper cathode", "CL", "South America"),
    "QuartzMine": ("QuartzMine", 5, "high-purity quartz", "US", "North America"),
}
OSAT_SITE = {"OSAT-A": "OSAT-A Hsinchu (TW)", "OSAT-B": "OSAT-B Penang (MY)", "OSAT-C": "OSAT-C Phoenix (US)"}
ORDERS = [
    ("ORD-1001", "Hyperion Cloud", "US", 4000),
    ("ORD-1002", "Nimbus AI", "JP", 3000),
    ("ORD-1003", "Orion Systems", "DE", 2000),
    ("ORD-1004", "Vega Labs", "SG", 1000),
]


class Rng:
    """xorshift32: identical sequence on every Python version and platform."""

    def __init__(self, seed: int):
        self.s = seed or 1

    def next(self) -> int:
        s = self.s
        s ^= (s << 13) & 0xFFFFFFFF
        s ^= s >> 17
        s ^= (s << 5) & 0xFFFFFFFF
        self.s = s
        return s

    def int(self, lo: int, hi: int) -> int:
        return lo + self.next() % (hi - lo + 1)

    def pick(self, seq):
        return seq[self.next() % len(seq)]


@dataclass
class Lot:
    id: str
    supplier: str
    part: str
    made: date
    parents: list[str] = field(default_factory=list)
    hold: str | None = None


@dataclass
class Package:
    id: int
    order: str
    site: str
    lots: dict[str, str]  # component role -> lot id
    events: list[dict]
    status: str
    test: dict


@dataclass
class Graph:
    lots: dict[str, Lot]
    packages: dict[int, Package]
    ancestors: dict[str, set[str]]  # lot id -> every lot above it (its upstream closure)
    lot_packages: dict[str, list[int]]  # lot id (any depth) -> packages that contain it

    def tier(self, lot_id: str) -> int:
        return SUPPLIERS[self.lots[lot_id].supplier][1]


def _gen_lots(r: Rng) -> dict[str, Lot]:
    lots: dict[str, Lot] = {}

    def add(prefix, sup, part, parents, n, fixed=(), lead=(10, 24), base=date(2026, 2, 1)):
        ids = list(fixed) + [f"{prefix}-{r.int(1000, 9999)}" for _ in range(n)]
        for lid in ids:
            while lid in lots and lid not in fixed:
                lid = f"{prefix}-{r.int(1000, 9999)}"
            ps = [r.pick(p) for p in parents] if parents else []
            start = max([lots[p].made for p in ps], default=base + timedelta(days=r.int(0, 20)))
            lots[lid] = Lot(lid, sup, part, start + timedelta(days=r.int(*lead)) if ps else start, ps)
        return [i for i in lots if lots[i].supplier == sup]

    q = add("QZ", "QuartzMine", "quartz", [], 4)
    poly = add("PS", "PolySiCo", "polysilicon", [q], 6)
    epx = add("EP", "EpoxyChemCo", "epoxy resin precursor", [], 4)
    cu = add("CU", "CopperOreCo", "copper cathode", [], 4)
    wafer = add("WF", "WaferCo", "silicon wafer", [poly], 8)
    dram = add("DW", "DramWaferCo", "DRAM wafer", [poly], 6)
    neon = add("NG", "NeonGasCo", "lithography gas", [], 4)
    resin = add("RS", "ResinCo", "build-up film", [epx], 5)
    foil = add("CF", "CopperFoilCo", "copper foil", [cu], 5)
    alloy = add("AL", "AlloyCo", "solder alloy", [], 4)
    die = add("WL-N3", "FoundryCo", "logic die", [wafer, neon], 5, fixed=("WL-N3-2041", "WL-N3-2044", "WL-N3-2046"), lead=(25, 40))
    hbma = add("HA", "MemCo", "HBM3E stack", [dram], 4, fixed=("HA-5521", "HA-5530"), lead=(25, 40))
    hbmb = add("HB", "MemCo2", "HBM3E stack", [dram], 3, fixed=("HB-3310",), lead=(25, 40))
    sub1 = add("LOT-S", "SubstrateCo", "substrate type 1", [resin, foil], 4, fixed=("LOT-S-71", "LOT-S-77"), lead=(25, 40))
    sub2 = add("LOT-S2", "SubstrateCo2", "substrate type 2", [resin, foil], 3, fixed=("LOT-S-90",), lead=(25, 40))
    add("BP", "BumpCo", "solder bumps", [alloy], 5, lead=(20, 30))
    lots["LOT-S-77"].hold = "quality hold pending failure analysis"
    assert die and hbma and hbmb and sub1 and sub2
    return lots


def build_graph(seed: int = 2026) -> Graph:
    r = Rng(seed)
    lots = _gen_lots(r)
    by_sup: dict[str, list[str]] = {}
    for lid, l in lots.items():
        by_sup.setdefault(l.supplier, []).append(lid)

    def closure(lid: str, memo: dict[str, set[str]]) -> set[str]:
        if lid not in memo:
            s: set[str] = set()
            for p in lots[lid].parents:
                s |= {p} | closure(p, memo)
            memo[lid] = s
        return memo[lid]

    anc: dict[str, set[str]] = {}
    for lid in lots:
        closure(lid, anc)

    packages: dict[int, Package] = {}
    lot_packages: dict[str, list[int]] = {}
    for i in range(N_PACKAGES):
        pid = FIRST_PACKAGE + i
        order = ORDERS[i % 4][0]
        site = "OSAT-B" if (order in ("ORD-1004",) or r.int(0, 9) < 3) else "OSAT-A"
        sub_sup = "SubstrateCo2" if site == "OSAT-B" else "SubstrateCo"
        hbm_sup = "MemCo2" if site == "OSAT-B" and r.int(0, 1) else "MemCo"
        comp = {"die": r.pick(by_sup["FoundryCo"]), "hbm": r.pick(by_sup[hbm_sup]), "substrate": r.pick(by_sup[sub_sup]), "bump": r.pick(by_sup["BumpCo"])}
        if pid == 1124544:
            comp = {"die": "WL-N3-2044", "hbm": "HB-3310", "substrate": "LOT-S-90", "bump": by_sup["BumpCo"][0]}
            site = "OSAT-B"
        if pid == 1124101:
            comp = {"die": "WL-N3-2041", "hbm": "HA-5521", "substrate": "LOT-S-77", "bump": by_sup["BumpCo"][1]}
            site = "OSAT-A"
        last = max(lots[c].made for c in comp.values())
        asm = last + timedelta(days=r.int(8, 20))
        held = any(lots[c].hold or any(lots[a].hold for a in anc[c]) for c in comp.values())
        tool = r.pick(["TCB-03", "TCB-07", "TCB-11"])
        bin_ = r.pick(["A", "A", "A", "B"])
        ev = [{"d": lots[c].made + timedelta(days=r.int(6, 12)), "stage": "inbound", "lot": c} for c in comp.values()]
        ev += [{"d": asm, "stage": "assembly", "tool": tool}, {"d": asm + timedelta(days=2), "stage": "underfill"}]
        tested = asm + timedelta(days=r.int(4, 7))
        retest = pid == 1124544
        if retest:
            ev.append({"d": tested, "stage": "test", "result": "FAIL", "detail": "SLT thermal margin below limit"})
        ev.append(
            {
                "d": tested + timedelta(days=2 if retest else 0),
                "stage": "test",
                "result": "PASS",
                "detail": f"SLT pass, bin {bin_}" + (" after retest" if retest else ""),
            }
        )
        status = "on_hold" if held else "delivered"
        ship = tested + timedelta(days=r.int(3, 6))
        if not held:
            roll = r.int(0, 99)
            if roll < 8 and pid != 1124544:
                status = "in_transit"
            elif roll < 12 and pid != 1124544:
                status = "in_test"
        if status != "in_test":
            ev.append({"d": ship, "stage": "ship", "carrier": r.pick(["Air carrier A", "Air carrier B", "Ocean/air multimodal"])})
            ev.append({"d": ship + timedelta(days=1), "stage": "customs", "order": order})
            if status == "delivered":
                ev.append({"d": ship + timedelta(days=r.int(3, 7)), "stage": "delivered", "order": order})
        ev = [e for e in ev if e["d"] <= TODAY]
        if status == "delivered" and not any(e["stage"] == "delivered" for e in ev):
            status = "in_transit"
        pk = Package(pid, order, site, comp, sorted(ev, key=lambda e: e["d"]), status, {"bin": bin_, "retest": retest})
        packages[pid] = pk
        for c in comp.values():
            for lid in {c} | anc[c]:
                lot_packages.setdefault(lid, []).append(pid)
    return Graph(lots, packages, anc, lot_packages)
