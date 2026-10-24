**Demo UI:** https://main.d2skuf17sh79ex.amplifyapp.com

# SiliconFlow — semiconductor supply allocation & recovery control plane

When a supplier changes a packaging slot, a substrate lot goes on hold, or an export rule moves, this system answers, with
evidence and a complete audit trail:

1. Which customer orders are now at risk, by how much, and **which constraint binds**?
2. What are the **feasible, compliance-gated recovery options**, what does each cost, and how do they hold up in a bad case?
3. Once humans approve one: **supplier/ERP/customer actions** are drafted or executed, and supplier answers flow back in as new facts.

Deterministic constraint solving and human approval govern anything physical, legal or irreversible. An LLM (optional) is only
allowed to *propose* events from unstructured notices.

Design details and honest limits: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Run it

```bash
pip install -e ".[dev,llm]"
siliconflow demo            # ~15 s: full incident, end to end, printed step by step
siliconflow serve --demo    # DEV ONLY: sample data + demo users; landing page at /, console at /ui/console/, API docs at /docs
pytest                      # 86 tests (~2 min; includes solver-backed property tests)

# service
siliconflow init-user --tenant acme --name "Pat" --roles planner      # prints an API key (only its hash is stored)
SILICONFLOW_DB=sqlite:///sf.db SILICONFLOW_MODE=draft siliconflow serve   # http://127.0.0.1:8000/docs
```

Environment: `SILICONFLOW_DB` (any SQLAlchemy URL, e.g. `postgresql+psycopg://…`), `SILICONFLOW_MODE` (`shadow|draft|execute`),
`SILICONFLOW_OUTBOX`, `SILICONFLOW_WEBHOOK_URL/TOKEN`, `ANTHROPIC_API_KEY` (+ `SILICONFLOW_LLM_MODEL`, default `claude-opus-5-5`)
to add the Claude extractor beside the rule-based one.

## The incident the demo runs (illustrative data)
6 orders / 12,700 modules for one AX-900 product; 2 are compliance-held (one prohibited, one missing a licence), so 10,000 are plannable.
OSAT-A cuts reserved packaging 25 % for weeks 4-9 and a substrate lot goes on quality hold.

| | |
|---|---|
| Pre-incident plan | 9,975 on time / 25 late, independently verified |
| If nobody acts | 1,276 on-time units lost across ORD-1002 and ORD-1003 (≈ $38.9 M exposure); protected order 1001 is unaffected |
| Binding | packaging at OSAT-A and OSAT-B (probed by relaxing each tight pool and re-solving) |
| Options | re-allocate only (8,900 on time) · **balanced** (≈ 9,432, spot packaging + expedited alt substrate, ≈ $0.9 M) · max-fulfilment · conservative (worst-case-safe) |
| Control | compliance, finance, planner, procurement, sales must each approve (four-eyes); execution re-verifies against current data |
| Close the loop | supplier confirms → commitment/capacity fact → new snapshot; decline → automatic re-plan |

## API (all under `/v1`, `Authorization: Bearer <key>`; interactive docs at `/docs`)

| Step | Endpoint |
|---|---|
| Load data | `POST /dataset` (JSON) · `POST /dataset/tables` (CSV: orders, commitments, inventory, capacity) · `GET /dataset/quality` |
| Signals | `POST /events` (typed events, one batch → one incident) · `POST /documents` → `GET /proposals` → `POST /proposals/confirm` |
| Conflicts | `GET /conflicts` · `POST /conflicts/{id}/resolve` · `POST /conflicts/resolve` |
| Decide | `GET /incidents[/{id}]` · `POST /incidents/{id}/select` · `…/decisions` · `…/execute` · `…/replan` |
| Act | `POST /actions/{id}/mark-sent` · `…/confirmation` · `…/revert` |
| Learn / govern | `POST /outcomes` · `GET /calibration` · `GET /metrics` · `GET /audit` · `GET /audit/verify` · `GET /snapshots[/diff]` |

Roles: `viewer` (read) · `planner`/`admin` (write, select, execute) · `procurement|finance|quality|sales|compliance` (approve their own role only).

## Layout
```
siliconflow/domain.py      typed, validated domain model (PlanInput snapshot)
siliconflow/events.py      typed events + provenance-aware reconciliation
siliconflow/engine/        scenarios, compliance gate, CP-SAT optimiser, verifier, impact, binding analysis, options
siliconflow/ingest/        CSV importers, integrity + data-quality, document extraction (rules / Claude)
siliconflow/store/         SQLAlchemy tables, hash-chained audit log
siliconflow/services/      Platform (workflow), approval policy, action drafting + adapters
siliconflow/api/           FastAPI app          siliconflow/cli.py   serve · init-user · demo
ui/                        landing page (index.html) + operational console (console/) + browser e2e test (e2e/)
tests/                     unit, property, service-flow and API tests
```

## UI
`siliconflow serve --demo` (dev only: seeds sample data and public demo users) and open `http://127.0.0.1:8000/`.
* **Landing page** (`ui/index.html`): scroll-driven story page with a scrubbed aerial freight clip.
* **Console** (`ui/console/`): dashboard, plan & orders, signals (notice → proposals → confirm; structured events; conflicts),
  incidents (impact, binding constraints, verified options, four-eyes approvals, execute, supplier confirmations, re-plan),
  dataset (quality, snapshots/diff, import), audit & learning. No framework, no build step; see [`ui/README.md`](ui/README.md).
* `node ui/e2e/run.js` drives the whole loop in a real browser (sign in → notice → incident → approvals by five roles → execute → confirm → audit).

## Status
Backend and console are complete for the shortage-to-recovery workflow. Before real production use see
the "Known limits" list (migrations, SSO, job queue, customer ERP adapters, customer-owned compliance table).
