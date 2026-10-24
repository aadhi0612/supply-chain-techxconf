"""Materialise a PlanInput into the flat, time-bucketed numbers the optimiser consumes.

Uncertainty is explicit: best / base / worst differ in how unconfirmed supply, tentative
capacity, holds, delays and yield are treated.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import PlanInput
from .types import Scenario


@dataclass
class Materialized:
    scenario: Scenario
    H: int  # number of week buckets (0..H-1)
    part_arrivals: dict[str, list[int]]
    capacity: dict[str, list[int]]
    yield_pm: dict[str, int]
    route_from: dict[str, int | None]  # first usable start week without levers (None = never)
    notes: list[str] = field(default_factory=list)

    def cum_supply(self, part_id: str) -> list[int]:
        out, t = [], 0
        for a in self.part_arrivals.get(part_id, [0] * self.H):
            t += a
            out.append(t)
        return out



def materialize(plan: PlanInput, scenario: Scenario) -> Materialized:
    H = plan.config.horizon_weeks
    notes: list[str] = []
    arrivals = {p.id: [0] * H for p in plan.parts}
    cfg = plan.config

    for c in plan.commitments:
        if c.part_id not in arrivals or c.status == "cancelled":
            continue
        week = plan.wk(c.promised_week)
        qty = c.qty
        if c.status in ("confirmed",):
            pass
        elif c.status == "acknowledged":
            if scenario is Scenario.worst:
                qty = int(c.qty * c.confidence)
        elif c.status == "delayed":
            if scenario is Scenario.worst:
                week += 1
                notes.append(f"worst: delayed commitment {c.id} slips one more week")
        elif c.status == "unconfirmed":
            if scenario is Scenario.best:
                pass
            elif scenario is Scenario.base:
                conf = cfg.unconfirmed_haircut_base if cfg.unconfirmed_haircut_base >= 0 else c.confidence
                qty = int(c.qty * conf)
            else:
                qty = 0
        if qty and 0 <= week < H:
            arrivals[c.part_id][week] += qty
        elif qty and week < 0:
            arrivals[c.part_id][0] += qty

    for lot in plan.inventory:
        if lot.part_id not in arrivals:
            continue
        week = max(0, plan.wk(lot.available_week))
        qty = lot.qty
        if lot.status == "available":
            pass
        elif lot.status == "in_transit":
            if scenario is Scenario.worst:
                week += 1
        elif lot.status in ("hold", "quarantine"):
            if lot.release_week is None or scenario is Scenario.worst:
                qty = 0  # a hold only releases in the plan if there is a dated release and we are not in worst
            else:
                week = max(week, plan.wk(lot.release_week))
        else:  # reserved elsewhere, wip
            qty = 0
        if qty and week < H:
            arrivals[lot.part_id][week] += qty

    capacity = {r.id: [0] * H for r in plan.resources}
    for b in plan.capacity:
        if b.resource_id not in capacity:
            continue
        w = plan.wk(b.week)
        if not 0 <= w < H:
            continue
        q = b.reserved
        if b.status == "tentative":
            q = {Scenario.best: b.reserved, Scenario.base: int(b.reserved * b.provenance.confidence), Scenario.worst: 0}[scenario]
        capacity[b.resource_id][w] += q

    yield_pm, route_from = {}, {}
    for r in plan.routes:
        yield_pm[r.id] = {Scenario.best: r.yield_pm.high, Scenario.base: r.yield_pm.base, Scenario.worst: r.yield_pm.low}[scenario]
        if r.qualification == "qualified":
            route_from[r.id] = max(0, plan.wk(r.qualified_from)) if r.qualified_from else 0
        else:
            route_from[r.id] = None
    return Materialized(scenario, H, arrivals, capacity, yield_pm, route_from, notes)
