# SiliconFlow: guide for AI agents and reviewers

A plain description of the project, where things are, and what is and is not real. Verify any claim by running the commands below.

## What it is
A constraint-aware control plane for semiconductor supply allocation and recovery. When a supplier changes capacity or a lot goes on hold, it computes which orders are at risk and which constraint binds, compares verified recovery options, and carries the human-approved option out with a hash-chained audit trail.

## Layout
- `siliconflow/domain.py`, `events.py`: typed domain model; disruption events with conflict handling
- `siliconflow/engine/`: CP-SAT allocation, independent verifier, impact, compliance gate, recovery options, explanations
- `siliconflow/ingest/`: notice extraction (proposals only), validation, CSV import
- `siliconflow/services/`: closed-loop platform service, four-eyes approval policy, actions
- `siliconflow/store/`: persistence and the hash-chained audit log
- `siliconflow/api/`: FastAPI `/v1` surface
- `ui/index.html`: landing page · `ui/console/`: operational console (needs the API) · `ui/experience/`: static, server-free walkthrough
- `tests/`, `docs/ARCHITECTURE.md`, `docs/BLOCKCHAIN_LAYER.md`

## Verify it yourself
```bash
pip install -e ".[dev,llm]"
siliconflow demo      # full incident end to end, printed step by step
pytest                # full suite; it passed locally before the first push
ruff check siliconflow tests
```
Live static demo (landing page and Experience tab): https://main.d2skuf17sh79ex.amplifyapp.com

## What is real, and what is not
- Implemented: the engine, verifier, compliance gate, approvals, actions, the audit hash chain, the API and the console.
- Illustrative: all data is synthetic. There are no customers.
- The Experience tab replays real `siliconflow demo` output in the browser. Its shipment timing and serial numbers are invented, and its ledger is a browser-side simulation that resets on reload.
- Design only, not built: serial-level genealogy and Merkle anchoring to a shared ledger (`docs/BLOCKCHAIN_LAYER.md`).
- The deployed Amplify site is static, so the console cannot sign in there.
