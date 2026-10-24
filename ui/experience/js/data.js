// Fixtures for the walkthrough. Order, lot, lever and compliance ids and every number in ORDERS/OPTIONS/IMPACT come from
// `siliconflow demo` on the bundled sample dataset (siliconflow/sample_data.py). Lot-level shipment timing and serial numbers are
// invented for illustration. All data is synthetic.
export const PLAN_START = "2026-10-26";
export const WEEKS = 16;

export const NODES = {
  FOUNDRY: { label: "FoundryCo", sub: "logic die · TW", x: 80, y: 60 },
  MEMCO: { label: "MemCo", sub: "HBM-A · KR", x: 80, y: 140 },
  MEMCO2: { label: "MemCo2", sub: "HBM-B", x: 80, y: 220 },
  SUBCO: { label: "SubstrateCo", sub: "SUB-1", x: 80, y: 300 },
  SUBCO2: { label: "SubstrateCo2", sub: "SUB-2 alt", x: 80, y: 380 },
  "OSAT-A": { label: "OSAT-A", sub: "packaging · TW", x: 390, y: 110, osat: true },
  "OSAT-B": { label: "OSAT-B", sub: "packaging · MY", x: 390, y: 240, osat: true },
  "OSAT-C": { label: "OSAT-C", sub: "packaging · US", x: 390, y: 370, osat: true },
  "TEST-1": { label: "TEST-1", sub: "SLT · TW", x: 630, y: 240 },
  HYPERION: { label: "Hyperion", sub: "ORD-1001 · US", x: 905, y: 50 },
  NIMBUS: { label: "Nimbus AI", sub: "ORD-1002 · JP", x: 905, y: 130 },
  ORION: { label: "Orion", sub: "ORD-1003 · DE", x: 905, y: 210 },
  VEGA: { label: "Vega Labs", sub: "ORD-1004 · SG", x: 905, y: 290 },
  ATLAS: { label: "Atlas", sub: "ORD-1005 · held", x: 905, y: 370, held: "blocked" },
  HELIX: { label: "Helix", sub: "ORD-1006 · held", x: 905, y: 430, held: "needs licence" },
};

// Shipments, in weeks from plan start. `st` overrides fields per stage; `only` limits a shipment to some stages.
export const SHIPMENTS = [
  { id: "DIE-1", kind: "logic die", from: "FOUNDRY", to: "OSAT-A", qty: 4000, dep: 0.2, arr: 1.0, lot: "WL-N3-2041" },
  { id: "DIE-2", kind: "logic die", from: "FOUNDRY", to: "OSAT-A", qty: 4500, dep: 2.0, arr: 3.0, lot: "WL-N3-2044" },
  { id: "DIE-3", kind: "logic die", from: "FOUNDRY", to: "OSAT-B", qty: 3500, dep: 3.0, arr: 4.2, lot: "WL-N3-2046" },
  { id: "HBM-1", kind: "HBM stacks", from: "MEMCO", to: "OSAT-A", qty: 26000, dep: 0.5, arr: 1.4, lot: "HA-5521" },
  { id: "HBM-2", kind: "HBM stacks", from: "MEMCO", to: "OSAT-A", qty: 26000, dep: 2.5, arr: 3.4, lot: "HA-5530" },
  { id: "HBM-3", kind: "HBM stacks", from: "MEMCO2", to: "OSAT-B", qty: 24000, dep: 3.5, arr: 4.8, lot: "HB-3310", note: "supplier ETA unconfirmed (email, 60%)" },
  { id: "SUB-1a", kind: "substrate", from: "SUBCO", to: "OSAT-A", qty: 7500, dep: 1.0, arr: 1.8, lot: "LOT-S-71" },
  { id: "SUB-1b", kind: "substrate", from: "SUBCO", to: "OSAT-A", qty: 2000, dep: 3.0, arr: 3.8, lot: "LOT-S-77", holdable: true },
  { id: "SUB-2x", kind: "substrate (expedite)", from: "SUBCO2", to: "OSAT-B", qty: 1500, dep: 4.2, arr: 5.2, lot: "LOT-S2-19", only: ["recovered"], lever: "LV-SUB2-EXP" },
  { id: "MOD-A1", kind: "modules", from: "OSAT-A", to: "TEST-1", qty: 4000, dep: 5.0, arr: 5.4, lot: "PK-A-0412" },
  { id: "MOD-A2", kind: "modules", from: "OSAT-A", to: "TEST-1", qty: 3000, dep: 8.0, arr: 8.4, lot: "PK-A-0419", st: { disrupted: { dep: 10.5, arr: 10.9, qty: 2300, note: "PKG-A at 75% in wk 4-9" }, recovered: { dep: 9.0, arr: 9.4, qty: 2500 } } },
  { id: "MOD-B1", kind: "modules", from: "OSAT-B", to: "TEST-1", qty: 2000, dep: 7.0, arr: 7.5, lot: "PK-B-0188" },
  { id: "MOD-B2", kind: "modules", from: "OSAT-B", to: "TEST-1", qty: 1400, dep: 7.0, arr: 7.5, lot: "PK-B-0191", only: ["recovered"], lever: "LV-PKG-SPOT" },
  { id: "FIN-1", kind: "finished", from: "TEST-1", to: "HYPERION", qty: 4000, dep: 8.0, arr: 9.5, order: "ORD-1001", lot: "SH-1001" },
  { id: "FIN-2", kind: "finished", from: "TEST-1", to: "NIMBUS", qty: 2998, dep: 10.0, arr: 11.0, order: "ORD-1002", lot: "SH-1002",
    st: { disrupted: { qty: 2284, risk: 716 }, recovered: { qty: 2999 } } },
  { id: "FIN-3", kind: "finished", from: "TEST-1", to: "ORION", qty: 1977, dep: 11.0, arr: 13.0, order: "ORD-1003", lot: "SH-1003",
    st: { disrupted: { qty: 1415, risk: 585 }, recovered: { qty: 1432 } } },
  { id: "FIN-3b", kind: "finished (late)", from: "TEST-1", to: "ORION", qty: 568, dep: 14.0, arr: 16.0, order: "ORD-1003", lot: "SH-1003b", only: ["recovered"], late: true },
  { id: "FIN-4", kind: "finished", from: "TEST-1", to: "VEGA", qty: 1000, dep: 12.0, arr: 13.0, order: "ORD-1004", lot: "SH-1004", st: { recovered: { qty: 996 } } },
  { id: "FIN-4b", kind: "finished (late)", from: "TEST-1", to: "VEGA", qty: 4, dep: 14.0, arr: 15.0, order: "ORD-1004", lot: "SH-1004b", only: ["recovered"], late: true },
];

export function shipmentsFor(stage) {
  return SHIPMENTS.filter((s) => !s.only || s.only.includes(stage)).map((s) => ({ ...s, ...(s.st?.[stage] ?? {}) }));
}

// Order outcomes per stage: [on time, late, unfulfilled]. normal/disrupted from the demo's step 3; recovered = option 1 (explain lines, step 4).
export const ORDERS = [
  { id: "ORD-1001", cust: "Hyperion Cloud", qty: 4000, due: 11, dest: "US", note: "protected", out: { normal: [4000, 0, 0], disrupted: [4000, 0, 0], recovered: [4000, 0, 0] } },
  { id: "ORD-1002", cust: "Nimbus AI", qty: 3000, due: 12, dest: "JP", out: { normal: [2998, 0, 2], disrupted: [2284, 0, 716], recovered: [2999, 0, 1] } },
  { id: "ORD-1003", cust: "Orion Systems", qty: 2000, due: 13, dest: "DE", out: { normal: [1977, 21, 2], disrupted: [1415, 0, 585], recovered: [1432, 568, 0] } },
  { id: "ORD-1004", cust: "Vega Labs", qty: 1000, due: 14, dest: "SG", note: "licence LIC-0042", out: { normal: [1000, 0, 0], disrupted: [1000, 0, 0], recovered: [996, 4, 0] } },
  { id: "ORD-1005", cust: "Atlas Compute", qty: 1500, due: 12, dest: "XX", gate: "blocked", why: "Rule set demo-2026.10: prohibited for CLASS-A -> G3", out: { normal: [0, 0, 1500], disrupted: [0, 0, 1500], recovered: [0, 0, 1500] } },
  { id: "ORD-1006", cust: "Helix HPC", qty: 1200, due: 12, dest: "IN", gate: "needs_license", why: "License required for CLASS-A -> IN; no valid license on file", out: { normal: [0, 0, 1200], disrupted: [0, 0, 1200], recovered: [0, 0, 1200] } },
];

export const NOTICE = `Subject: Reserved capacity adjustment - OSAT-A
Dear partner, following a tool-qualification outage OSAT-A will reduce your reserved 2.5D-L packaging capacity (PKG-A) by 25% in weeks 4-9.
Separately, substrate lot LOT-S-77 is on quality hold pending failure analysis.`;

export const PROPOSALS = [
  { id: "prp_28a7d1f6a479", kind: "capacity_change", conf: 0.6, summary: "PKG-A reserved capacity -25 %, weeks 4-9", ambiguity: "'week 4-9' interpreted as plan-relative weeks (week 0 = plan start); confirm." },
  { id: "prp_cf779be4d5f3", kind: "lot_status", conf: 0.7, summary: "Lot LOT-S-77 (SUB-1, 2,000 units at OSAT-A) -> hold", ambiguity: null },
];

export const IMPACT = {
  lost: 1276, exposure: 38897400, base: 9975, after: 8699,
  binding: [["Packaging capacity - PKG-B (OSAT B, 2.5D-L)", 569], ["Packaging capacity - PKG-A (OSAT A, 2.5D-L)", 535]],
};

export const OPTIONS = [
  { id: "max", name: "Maximum fulfilment", onTime: 9427, spend: 920680, bbw: [9427, 9427, 8048], levers: ["LV-PKG-SPOT", "LV-SUB2-EXP"], rec: true, live: true },
  { id: "bal", name: "Balanced recovery", onTime: 9432, spend: 923080, bbw: [9432, 9432, 8051], levers: ["LV-PKG-SPOT", "LV-SUB2-EXP"], live: true },
  { id: "realloc", name: "Re-allocate only (no spend)", onTime: 8900, spend: 0, bbw: [8900, 8900, 7551], levers: [] },
  { id: "robust", name: "Conservative (survives downside)", onTime: 8208, spend: -389200, bbw: [8208, 8208, 8208], levers: [] },
];
export const LEVERS = {
  "LV-PKG-SPOT": { text: "Spot packaging block at OSAT-B (+300/wk x 5)", cp: "OSAT-B", roles: ["procurement", "finance"] },
  "LV-SUB2-EXP": { text: "Expedite SUB-2 for the alternate route (cap 1,500)", cp: "SubstrateCo2", roles: ["procurement"] },
};

export const PEOPLE = [
  { name: "Pat Planner", role: "planner", proposer: true },
  { name: "Sam Second-Planner", role: "planner" },
  { name: "Priya Procurement", role: "procurement" },
  { name: "Fiona Finance", role: "finance" },
  { name: "Sasha Sales", role: "sales" },
  { name: "Carl Compliance", role: "compliance" },
];

// Actions the demo's step 5 produced for option 1 (ids per `siliconflow demo`).
export function actionsFor(opt) {
  const a = [];
  if (opt.levers.includes("LV-PKG-SPOT")) a.push(["SUPPLIER_REQUEST", "OSAT-B", "sent", "LV-PKG-SPOT"]);
  if (opt.levers.includes("LV-SUB2-EXP")) a.push(["SUPPLIER_REQUEST", "SubstrateCo2", "sent", "LV-SUB2-EXP"], ["ERP_PO_CHANGE", "SubstrateCo2", "sent", "LV-SUB2-EXP"]);
  a.push(["CUSTOMER_COMMIT_UPDATE", "NIMBUS", "sent"], ["CUSTOMER_COMMIT_UPDATE", "ORION", "sent"], ["CUSTOMER_COMMIT_UPDATE", "VEGA", "sent"],
    ["COMPLIANCE_REVIEW", "ORD-1005", "drafted"], ["COMPLIANCE_REVIEW", "ORD-1006", "drafted"], ["ALLOCATION_UPDATE", "ERP/APS", "sent"]);
  return a;
}
export const requiredRoles = (opt) => [...new Set(["planner", "sales", "compliance", ...opt.levers.flatMap((l) => LEVERS[l].roles)])];

// ---- serial-level genealogy (illustrative): deterministic PRNG so every load shows the same units ----
function rng(seed) { return () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
const pick = (r, a) => a[Math.floor(r() * a.length)];
export function buildUnits() {
  const r = rng(2026), units = [];
  const plan = [["ORD-1001", 18, "MOD-A1", "FIN-1"], ["ORD-1002", 14, "MOD-A2", "FIN-2"], ["ORD-1003", 10, "MOD-A2", "FIN-3"], ["ORD-1004", 6, "MOD-B1", "FIN-4"]];
  let n = 400;
  for (const [order, count, mod, fin] of plan) for (let i = 0; i < count; i++) {
    const site = mod.startsWith("MOD-B") || (mod === "MOD-A2" && r() < 0.3) ? "OSAT-B" : "OSAT-A";
    const sub = site === "OSAT-B" ? "LOT-S-90" : (r() < 0.38 ? "LOT-S-77" : "LOT-S-71");
    units.push({
      serial: `AX9-26W${40 + Math.floor(r() * 4)}-${String(++n).padStart(5, "0")}`, order, site, sub,
      logic: site === "OSAT-B" ? "WL-N3-2046" : pick(r, ["WL-N3-2041", "WL-N3-2044"]),
      hbm: site === "OSAT-B" ? "HB-3310" : pick(r, ["HA-5521", "HA-5530"]),
      pkgLot: mod, tool: pick(r, ["TCB-03", "TCB-07", "TCB-11"]), bin: pick(r, ["A", "A", "A", "B"]), slt: pick(r, ["PASS", "PASS", "PASS", "PASS"]), ship: fin,
    });
  }
  return units;
}
export const LOT_INFO = {
  "LOT-S-77": { part: "SUB-1 substrate", supplier: "SubstrateCo", qty: 2000 }, "LOT-S-71": { part: "SUB-1 substrate", supplier: "SubstrateCo", qty: 7500 },
  "LOT-S-90": { part: "SUB-2 substrate", supplier: "SubstrateCo2", qty: 1000 },
  "WL-N3-2041": { part: "logic die wafer lot", supplier: "FoundryCo" }, "WL-N3-2044": { part: "logic die wafer lot", supplier: "FoundryCo" }, "WL-N3-2046": { part: "logic die wafer lot", supplier: "FoundryCo" },
  "HA-5521": { part: "HBM3E 12-Hi lot", supplier: "MemCo" }, "HA-5530": { part: "HBM3E 12-Hi lot", supplier: "MemCo" }, "HB-3310": { part: "HBM3E 12-Hi lot", supplier: "MemCo2" },
};
