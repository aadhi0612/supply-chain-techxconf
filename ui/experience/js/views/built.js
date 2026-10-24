// What is real in this page, and where each piece lives in the repository.
import { h, table, badge } from "../util.js";

const REPO = "https://github.com/aadhi0612/supply-chain-techxconf/blob/main/";
const link = (p) => h("a", { href: REPO + p, target: "_blank", rel: "noopener" }, h("span", { class: "mono" }, p));
const ROWS = [
  ["Track", "Lots and orders by site and week", "siliconflow/domain.py, engine/planner.py", "GET /v1/plan", "Implemented; shipment dots here are scripted"],
  ["Trace", "Serial → lot genealogy, lot → serial recall scope", "docs/BLOCKCHAIN_LAYER.md (traceability section)", "-", "Design only; serial-level genealogy is not in the engine yet"],
  ["Disrupt · notice → facts", "Unstructured email becomes proposed events", "siliconflow/ingest/extract.py, events.py", "POST /v1/signals", "Implemented"],
  ["Disrupt · impact", "Plan before vs after, binding pools", "engine/impact.py, engine/explain.py", "GET /v1/incidents/{id}", "Implemented; numbers here are real demo output"],
  ["Disrupt · options", "Verified best/base/worst recovery options", "engine/recovery.py, optimizer.py, verify.py", "GET /v1/incidents/{id}", "Implemented"],
  ["Disrupt · approvals, actions", "Four-eyes policy, idempotent actions", "services/policy.py, actions.py, platform.py", "POST /v1/incidents/{id}/…", "Implemented"],
  ["Ledger", "Hash-chained audit log", "siliconflow/store/audit.py", "GET /v1/audit/verify", "Chain, Merkle anchoring to a separate local ledger and proofs are implemented; a real shared ledger is an adapter still to be written"],
];
export default function built(root) {
  root.append(h("div", { class: "page-head" }, h("div", null, h("h1", null, "How it's built"), h("div", { class: "sub" },
      "This page is a static mockup: no server, no database, no network calls. It replays the platform's real demo output so you can feel the workflow. The real thing is a Python service; this maps each tab to it."))),
    h("div", { class: "card" }, h("h2", null, "Tab → real implementation"),
      table([{ label: "Tab / step", key: 0 }, { label: "What it shows", key: 1 }, { label: "Code", render: (r) => h("span", { class: "small" }, r[2]) }, { label: "API", render: (r) => h("span", { class: "mono small" }, r[3]) },
        { label: "Status", render: (r) => h("span", { class: "small" }, r[4]) }], ROWS)),
    h("div", { class: "grid g2", style: "margin-top:16px" },
      h("div", { class: "card" }, h("h2", null, "Architecture"), h("pre", { class: "pay" }, `signals (email, portal, ERP, CSV)
        │  proposals only; a human confirms
        ▼
 events → snapshot (versioned PlanInput)
        ▼
 engine: CP-SAT allocate → independent verify
        ▼
 impact · binding pools · recovery options
        ▼
 four-eyes approvals → idempotent actions
        ▼
 supplier answers → new facts   (loop)
        └──────── every step → hash-chained audit`)),
      h("div", { class: "card" }, h("h2", null, "Honest limits of this page"),
        h("ul", { class: "small", style: "margin:0;padding-left:18px" },
          h("li", null, "Data is synthetic. Order, lot, lever ids and the headline numbers are real engine output on the sample dataset; shipment timing and serial numbers are invented."),
          h("li", null, "The ledger is a real SHA-256 chain plus a separate list of Merkle roots, both in your tab; they reset on reload. It is a simulation, not a blockchain. The console runs the same logic against the API and a separate ledger file."),
          h("li", null, "Serial-level traceability is a design (see the docs), not yet a feature of the engine."),
          h("li", null, "Only the two recommended options can be executed here; the console runs any of them against a live API."))),
      h("div", { class: "card" }, h("h2", null, "Read the code"), h("div", { class: "stack" }, ["README.md", "docs/ARCHITECTURE.md", "docs/BLOCKCHAIN_LAYER.md", "siliconflow/engine/recovery.py"].map((p) => h("div", null, link(p)))))));
}
