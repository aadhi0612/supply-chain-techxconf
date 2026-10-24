"""Generate, verify, compare and rank recovery options for a changed supply picture."""
from __future__ import annotations

import hashlib
import json

from pydantic import Field

from ..domain import Model, PlanInput
from . import compliance
from .explain import BindingReport, analyze_binding
from .planner import evaluate_under, run_plan
from .types import ComplianceGate, PlanResult, Scenario

OPTION_ORDER = ["no_spend", "balanced", "max_fulfilment", "robust"]
TITLES = {
    "no_spend": ("Re-allocate only (no spend)", "Re-optimise allocation across customers using existing qualified supply, capacity and routes. No new commitments to suppliers or customers beyond re-sequencing."),
    "balanced": ("Balanced recovery", "Re-optimise and use recovery levers (expedite, spot capacity, qualification, window extension) when they improve on-time delivery, then minimise cost."),
    "max_fulfilment": ("Maximum fulfilment", "Prioritise total units delivered (even if late within contract window) before weighted on-time performance and cost."),
    "robust": ("Conservative (survives downside)", "Plan against the worst-case scenario (low yield, unconfirmed supply and tentative capacity excluded) and rely on no unconfirmed external levers."),
}


class ScenarioRange(Model):
    scenario: str
    on_time_units: int
    delivered_units: int
    weighted_on_time_units: int


class RecoveryOption(Model):
    id: str
    profile: str
    title: str
    description: str
    result: PlanResult
    incremental_cost_vs_no_spend: float
    extra_on_time_vs_no_spend: int
    cost_per_extra_on_time_unit: float | None
    scenario_range: list[ScenarioRange] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    plan_hash: str
    rank: int = 0
    recommended: bool = False


class OptionSet(Model):
    options: list[RecoveryOption]
    rejected: list[dict] = Field(default_factory=list)
    binding: BindingReport | None = None
    gates: dict[str, ComplianceGate] = Field(default_factory=dict)
    baseline_ref: dict = Field(default_factory=dict)


def plan_fingerprint(res: PlanResult) -> str:
    """Outcome-level identity: two plans that deliver the same units to the same orders with the same levers are one option."""
    blob = json.dumps(
        {"o": sorted((o.order_id, o.on_time, o.late, o.last_arrival_week or -1) for o in res.orders),
         "l": sorted((l.lever_id, l.qty) for l in res.levers)}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _risks(plan: PlanInput, res: PlanResult, profile: str) -> list[str]:
    risks = []
    ext = [l for l in res.levers if next(lv for lv in plan.levers if lv.id == l.lever_id).needs_external_confirmation]
    if ext:
        risks.append(f"Depends on {len(ext)} external confirmation(s) not yet obtained: {', '.join(l.lever_id for l in ext)}.")
    pend = {l.target_id for l in res.levers if l.type == "accelerate_qualification"}
    if pend:
        risks.append(f"Uses route(s) still pending qualification: {', '.join(sorted(pend))}.")
    unconf = [c.id for c in plan.commitments if c.status == "unconfirmed" and c.qty]
    if unconf and profile != "robust":
        risks.append("Base case counts unconfirmed supply at its confidence-weighted quantity: " + ", ".join(unconf) + ".")
    if res.extended_orders:
        risks.append("Requires customer agreement to extend delivery window for " + ", ".join(res.extended_orders) + ".")
    return risks


def build_options(
    plan: PlanInput, *, with_binding: bool = True, profiles: list[str] | None = None
) -> OptionSet:
    gates = compliance.evaluate_all(plan)
    options: list[RecoveryOption] = []
    rejected: list[dict] = []
    seen: dict[str, str] = {}
    results: dict[str, PlanResult] = {}
    for name in profiles or OPTION_ORDER:
        res = run_plan(plan, name, gates=gates)
        if res.solver_status == "ERROR" or not res.verified:
            rejected.append({"profile": name, "reason": "solver error" if res.solver_status == "ERROR" else "failed independent verification",
                             "errors": res.verification_errors[:10]})
            continue
        fp = plan_fingerprint(res)
        if fp in seen:
            rejected.append({"profile": name, "reason": f"identical to option '{seen[fp]}'"})
            continue
        seen[fp] = name
        results[name] = res
    base = results.get("no_spend")
    for name, res in results.items():
        title, desc = TITLES[name]
        inc = res.totals["total_cost"] - base.totals["total_cost"] if base else 0.0
        extra = int(res.totals["on_time_units"] - base.totals["on_time_units"]) if base else 0
        rng = []
        for sc in (Scenario.best, Scenario.base, Scenario.worst):
            r = res if sc == res.scenario else evaluate_under(plan, res, sc, gates)
            rng.append(ScenarioRange(scenario=sc.value, on_time_units=int(r.totals["on_time_units"]),
                                     delivered_units=int(r.totals["delivered_units"]),
                                     weighted_on_time_units=int(r.totals["weighted_on_time_units"])))
        options.append(RecoveryOption(
            id=f"opt_{name}", profile=name, title=title, description=desc, result=res,
            incremental_cost_vs_no_spend=round(inc, 2), extra_on_time_vs_no_spend=extra,
            cost_per_extra_on_time_unit=round(inc / extra, 2) if extra > 0 and inc > 0 else None,
            scenario_range=rng, risks=_risks(plan, res, name), plan_hash=plan_fingerprint(res),
        ))

    # Differences inside the solver's optimality tolerance are ties; cost then decides.
    def base_rng(o: RecoveryOption):
        return next(r for r in o.scenario_range if r.scenario == "base")

    w_tol = max(1.0, 0.005 * max([base_rng(o).weighted_on_time_units for o in options] or [1]))
    d_tol = max(1.0, 0.005 * max([base_rng(o).delivered_units for o in options] or [1]))

    def key(o: RecoveryOption):
        t = o.result.totals
        return (-t.get("protected_on_time_units", 0), -round(base_rng(o).weighted_on_time_units / w_tol),
                -round(base_rng(o).delivered_units / d_tol), t["total_cost"], len(o.result.levers))

    # the robust option is ranked on what it guarantees in the worst case, others on base
    options.sort(key=key)
    for i, o in enumerate(options, 1):
        o.rank = i
    if options:
        options[0].recommended = True
    binding = None
    if with_binding and options:
        binding = analyze_binding(plan, options[0].result, options[0].profile, gates)
    return OptionSet(options=options, rejected=rejected, binding=binding, gates=gates)
