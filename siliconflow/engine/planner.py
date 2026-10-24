"""Orchestrates gate -> materialise -> solve -> independent verify."""
from __future__ import annotations

from ..domain import PlanInput
from . import compliance
from .optimizer import PROFILES, PlanCaps, Profile, solve
from .scenarios import materialize
from .types import ComplianceGate, PlanResult, Scenario
from .verify import verify


def run_plan(
    plan: PlanInput,
    profile: str | Profile = "balanced",
    scenario: Scenario | None = None,
    *,
    gates: dict[str, ComplianceGate] | None = None,
    caps: PlanCaps | None = None,
    relax: frozenset[str] = frozenset(),
    levels: tuple[str, ...] | None = None,
    time_limit: float | None = None,
    check: bool = True,
) -> PlanResult:
    prof = PROFILES[profile] if isinstance(profile, str) else profile
    scen = scenario or prof.scenario
    gates = gates or compliance.evaluate_all(plan)
    mat = materialize(plan, scen)
    res = solve(plan, mat, gates, prof, caps=caps, relax=relax, levels=levels, time_limit=time_limit,
                snapshot_hash=plan.content_hash())
    res.scenario = scen
    if check and res.solver_status != "ERROR":
        errs = verify(plan, mat, gates, res)
        res.verified, res.verification_errors = not errs, errs
    return res


def caps_from(res: PlanResult) -> PlanCaps:
    starts = {(s.order_id, s.route_id, s.start_week): s.started for s in res.starts}
    levers = {lu.lever_id: lu.qty for lu in res.levers}
    return PlanCaps(starts=starts, levers=levers)


def evaluate_under(plan: PlanInput, res: PlanResult, scenario: Scenario, gates=None) -> PlanResult:
    """What survives of an already-chosen plan if reality matches `scenario`?

    Decisions can only shrink (fewer starts, same levers): this is how a committed plan degrades,
    not how a re-optimised plan could recover.
    """
    gates = gates or compliance.evaluate_all(plan)
    if not verify(plan, materialize(plan, scenario), gates, res):
        # the plan is still valid as-is under these facts: nothing to degrade (deterministic, no solver noise)
        kept = res.model_copy(deep=True)
        kept.scenario, kept.snapshot_hash, kept.verified, kept.verification_errors = scenario, plan.content_hash(), True, []
        return kept
    return run_plan(plan, "no_spend" if not res.levers else "balanced", scenario,
                    gates=gates, caps=caps_from(res), levels=("protected_on_time_units", "weighted_on_time_units", "delivered_units"))
