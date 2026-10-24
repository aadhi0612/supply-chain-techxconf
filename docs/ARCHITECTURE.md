# Architecture and design decisions

```
 CSV / JSON / API events / supplier notices
            │
            ▼
 ingest/  importers · validate (integrity + data quality) · extract (rules | Claude, proposals only)
            │  events.py: typed events + reconciliation (source precedence, staleness, conflicts)
            ▼
 store/   tenant-scoped, versioned PlanInput snapshots · hash-chained audit log
            │
            ▼
 engine/  scenarios (best/base/worst) → compliance gate → CP-SAT lexicographic optimiser
          → independent verifier → recovery options → impact · binding-constraint probes
            │
            ▼
 services/Platform  incident state machine · approvals (policy, four-eyes) · actions (idempotent, retried)
          · confirmations → new facts · outcomes → calibration
            │
            ▼
 api/     FastAPI (API-key auth, roles)         cli.py   serve · init-user · demo
```

## The rule that shapes everything
Quantities, capacity, qualification and compliance are decided by deterministic code (`engine/`). A language model may
*propose* a structured event from a document; it can never change state, and nothing it says reaches the solver without
(1) verbatim evidence in the source text, (2) schema validation, (3) existence checks on every referenced entity,
(4) a dry-run application, and (5) human confirmation. Explanations are generated from solver output, not by a model,
so an "unsupported claim" cannot occur.

## Planning model (`engine/optimizer.py`)
Variables per *(order, qualified route, start week)*: units started `s`, good units `g` (`1000·g ≤ yield‰·s`).

Hard constraints: BOM and route-compatible parts · cumulative time-phased part supply (incl. lever supply) · time-phased
resource capacity (incl. spot capacity) · route qualification window (incl. accelerated qualification) · contractual latest
delivery (incl. approved window extension) · compliance gate (orders that are not `allow` get no variables, so their supply is
re-allocated) · all-or-nothing / minimum-ship orders · optional hard spend cap.

Objectives are solved **lexicographically** (each level is fixed before the next): protected-order on-time units →
priority-weighted on-time units → delivered units → cost (route premiums + lateness penalties + lever spend) → disruption.
Profiles reorder or switch levers: `no_spend`, `balanced`, `max_fulfilment`, `robust` (worst-case scenario, no unconfirmed levers).

Uncertainty is explicit: *best* counts unconfirmed supply and high yield; *base* counts unconfirmed supply at its confidence
and base yield; *worst* excludes unconfirmed supply, tentative capacity and unreleased holds, and uses low yield. Every option
is also evaluated under all three scenarios by fixing its decisions as upper bounds (a committed plan can only degrade).

### Independent verification (`engine/verify.py`)
Every plan is re-checked from the raw input by code that shares nothing with the CP-SAT model (BOM, part compatibility,
cumulative supply, capacity, yield, arrival and contract windows, compliance, all-or-nothing, outcome recomputation).
A plan that fails is rejected, not shown. The same check re-runs against the **current** snapshot at execution time; if
reality has moved and the plan is no longer feasible the incident becomes `stale` and execution is refused.

### Optimality gap
CP-SAT proves optimality per level where it can; otherwise it stops at `solver_gap` (default 0.05%) or the time limit and
the achieved value and proven bound are stored per level in the plan (`levels[].value/bound/solver_status`). Objective
values are deterministic; the specific allocation among equal-value solutions is not bit-identical with `solver_workers > 1`.
Differences smaller than the tolerance are treated as ties when ranking options (cost then decides).

## Facts, provenance and conflicts (`events.py`)
Every mutable fact carries `Provenance(source, observed_at, confidence, method, document_id)`. Events are applied to a *copy*;
the result is a new immutable snapshot. Reconciliation is asymmetric:
* older observations never overwrite newer ones (`stale`);
* a less authoritative source (per `source_precedence`) may *worsen* the plan immediately (applied conservatively, conflict
  opened) but may not *improve* it until confirmed (conflict opened, not applied);
* a hold is released only by an equal-or-higher source. Humans resolve conflicts; resolution is itself an audited event.

## Workflow and control (`services/`)
`analyzing → options_ready → pending_approval → approved → executing → completed` (plus `rejected`, `stale`, `superseded`,
`no_impact`, `analysis_failed`). Approval roles come from policy (lever approvers, spend threshold, compliance status changes,
customer-promise changes). Four-eyes: the proposer cannot approve; one person cannot satisfy two roles; approvals bind to a
`plan_hash` so changing the selected plan voids earlier approvals.
Execution modes: `shadow` (recommend only) → `draft` (drafts for humans to send) → `execute` (allow-listed types through
adapters). Actions have stable idempotency keys, bounded retries, failure records and compensating "revert" actions.
Counterparty confirmations re-enter as sourced events, which closes the loop.

## Audit
`audit_log` rows are hash-chained per tenant (`prev_hash → hash`) in the same transaction as the change, with a head row to
detect truncation. `GET /v1/audit/verify` re-computes the chain.

## Known limits (deliberate, listed so nobody assumes otherwise)
* Single-process service; analysis can run in a thread pool (`mode=async`, file/Postgres DB). No external job queue.
* Schema is created with `create_all`; add Alembic before the first production schema change.
* Auth is hashed API keys + roles; plug SSO/OIDC in front for production. No rate limiting.
* Yield is modelled per route and continuous (rounded down per start record); lot genealogy, shared die across SKUs and
  inventory-in-process are not modelled. Parts are consumed at start week.
* Compliance is a *customer-maintained decision table* with evidence capture; the bundled table is illustrative and not legal advice.
* Adapters shipped: file outbox and generic webhook. SAP/Oracle/EDI adapters are integration work per customer.
* Calibration is reported, never auto-applied to constraints.
