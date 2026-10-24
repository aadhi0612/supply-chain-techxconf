# Proposed: ledger anchoring and traceability layer

**Status: design only. Nothing in this document is implemented.**

## Why

`store/audit.py` keeps a per-tenant hash chain with a head row to detect truncation. That is tamper-*evident* to someone who trusts the database operator. The operator can still rebuild the chain, and counterparties (OEM, fab, OSAT, suppliers) have no independent copy. A shared ledger removes the need to trust the operator for history, and gives non-repudiable supplier commitments.

## What a ledger does not fix

* Garbage in, garbage out: a ledger records a false claim as faithfully as a true one (the oracle problem). Provenance and conflict handling in `events.py` stay the first line of defence.
* Privacy and export control: prices, volumes and controlled technical data must not go on a shared ledger. Only digests and signatures do.
* Throughput: nothing here needs high TPS; batching keeps cost trivial.

## Design

```
Platform (audit rows, approvals, confirmations)
   │  same transaction as today
   ▼
Anchor service ── batch N rows or T minutes ──► Merkle tree ──► signed root
   │                                                            │
   ▼                                                            ▼
anchors table (root, seq range, tx id)                   Permissioned ledger
                                                          (e.g. Hyperledger Fabric;
                                                           or anchor to a public chain)
   ▲                                                            │
   └──────────── inclusion proof for any audit row ◄────────────┘
```

### On the ledger
| Record | Form | Purpose |
|---|---|---|
| Audit chain head / Merkle root | digest + seq range | history was not rewritten |
| Approval | `plan_hash`, role, approver key, time | exactly which plan was approved by whom |
| Supplier confirmation | payload digest signed by the supplier's key | non-repudiable commitment |
| Export-licence evidence | document digest + issuer | licence existed at ship time |
| Lot / wafer genealogy (later) | digest chain per lot | traceability toward a digital product passport |

### Off the ledger (database only)
Orders, prices, capacities, BOMs, document bodies.

## Integration points in this repo

* `store/audit.py`: `append` already commits inside the caller's transaction; add an `anchors` table and a background `anchor.py` that batches by `seq`.
* `GET /v1/audit/verify`: extend to also verify the latest anchor and return an inclusion proof for a given `seq`.
* `services/actions.py`: confirmations accept an optional detached signature; verification of the supplier's registered public key happens before the confirmation becomes an event.
* Failure mode: if the ledger is unreachable, the platform continues and anchors catch up; verification reports "unanchored since seq N".

## Decisions for a human
1. Permissioned consortium ledger vs anchoring to a public chain (governance and cost vs neutrality).
2. Who runs ledger nodes (customer, suppliers, a neutral party).
3. Supplier key issuance and rotation.

## Build order
1. Anchor table, Merkle batching, local "mock ledger" adapter, tests for proofs and tamper detection.
2. Real ledger adapter behind the same interface.
3. Signed supplier confirmations.
4. Lot genealogy and licence evidence.
