# Problem statement, end to end

Two briefs motivate this repository. The figures below are as stated in those briefs; they have not been independently audited.

## 1. Semiconductor supply chain ("supply chain 2.0 for chips")

| # | Problem | Evidence in the brief | Missing capability | Where it lands in this repo |
|---|---|---|---|---|
| 1 | Fragile, manual chain | One advanced AI chip: ~1,400 process steps, a dozen countries, ~5 months; run on spreadsheets, SAP and phone calls | Early warning, impact, recovery options | `events.py` → `engine/impact.py` → `engine/recovery.py` |
| 2 | No multi-tier visibility | 2021: a $300 chip held up a $50,000 car, $210B of vehicles unbuilt; tiers 2-3 invisible | Multi-tier risk monitoring | Sourced, provenance-aware events and conflicts (`events.py`); supplier confirmations re-enter as facts |
| 3 | Allocation bottlenecks | Advanced packaging is the top AI-compute bottleneck, one buyer holds 60%+; HBM booked through 2026 | Real-time allocation tracking | CP-SAT optimiser per order, route and week (`engine/optimizer.py`) |
| 4 | Export controls | Change quarterly | Export compliance | Compliance gate: non-`allow` orders get no supply (`engine/compliance.py`) |
| 5 | New fabs from scratch | CHIPS Act fabs in AZ, TX, OH, NY each need a supply chain built nearly from nothing | Tooling that exists on day one | Multi-tenant platform, CSV/API onboarding (`ingest/`) |

## 2. Hardware iteration speed

A Shenzhen team can go from design to a physical part in a day; in the US the same loop often takes weeks, and the gap compounds. The missing stack tightly integrates design, manufacturing and logistics.

**Status: not implemented.** SiliconFlow addresses the supply-allocation half for semiconductors. The iteration loop (quotes, lead times, part availability feeding back into design) is a roadmap item that would reuse the same typed-event, approval and audit model.

## 3. Trust and traceability (added requirement)

Multi-party chains need a shared record of who promised and approved what. See [`BLOCKCHAIN_LAYER.md`](BLOCKCHAIN_LAYER.md).

## What "solved" would look like

1. A shortage signal becomes a quantified impact in minutes, not days.
2. Every recovery option is feasible, compliance-gated and independently verified.
3. Every approval, action and supplier confirmation is attributable and cannot be quietly rewritten.
