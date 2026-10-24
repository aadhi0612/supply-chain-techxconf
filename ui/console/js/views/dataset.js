import { get, post, api } from "../api.js";
import { badge, fmt, h, jsonPre, kpi, stateBadge, table, tabs, toast } from "../util.js";

export default async function (root, ctx) {
  const canWrite = ctx.me.roles.some((r) => r === "planner" || r === "admin");
  const [ds, quality, snaps] = await Promise.all([get("/v1/dataset").catch(() => null), get("/v1/dataset/quality").catch(() => null), get("/v1/snapshots").catch(() => [])]);
  root.append(h("div", { class: "page-head" }, h("div", { class: "grow" }, h("h1", null, "Dataset"),
    h("div", { class: "sub" }, "The time-versioned snapshot every plan is computed from, its data quality, and how to load or replace it.")),
    ds ? h("span", { class: "badge info" }, `snapshot v${ds.version}`) : null));
  if (ds && quality) {
    const p = ds.plan, m = quality.metrics;
    root.append(h("div", { class: "grid g4" },
      kpi("Source-confirmed supply", fmt.pct(m.pct_commitments_source_confirmed), `${m.supplier_commitments_total} commitments`),
      kpi("Stale facts", fmt.pct(m.pct_facts_stale), `of ${m.facts_total} facts (> ${p.config.stale_after_days} days)`),
      kpi("Low-confidence facts", fmt.pct(m.pct_facts_low_confidence), "confidence < 0.70"),
      kpi("Compliance-held orders", m.orders_compliance_held, `of ${m.orders_total}`)));
    const body = h("div", { class: "card", style: "margin-top:16px" });
    root.append(tabs([["issues", `Data quality (${quality.issues.length})`], ["orders", "Orders"], ["supply", "Supply"], ["capacity", "Capacity"], ["routes", "Routes & levers"], ["history", `Snapshots (${snaps.length})`]], "issues", (t) => body.replaceChildren(...view(t, p, quality, snaps, ctx))), body);
  } else root.append(h("div", { class: "note warn" }, "No dataset yet. Load one below."));
  if (canWrite) root.append(importCard(ctx));
}

function view(t, p, quality, snaps, ctx) {
  const wk = (d) => fmt.date(d);
  if (t === "issues") return [table([{ label: "Severity", render: (i) => badge(i.severity, i.severity === "error" ? "bad" : i.severity === "warning" ? "warn" : "info") }, { label: "Code", render: (i) => h("span", { class: "mono" }, i.code) }, { label: "Detail", key: "message" }], quality.issues, { empty: "No issues. Data is clean." })];
  if (t === "orders") return [table([{ label: "Order", render: (o) => h("b", null, o.id) }, { label: "Customer", key: "customer_id" }, { label: "SKU", key: "sku_id" }, { label: "Qty", num: true, render: (o) => fmt.int(o.qty) },
    { label: "Committed", render: (o) => wk(o.committed_week) }, { label: "Latest", render: (o) => wk(o.latest_week) }, { label: "Prio", render: (o) => `${o.priority}${o.protected ? " · protected" : ""}` },
    { label: "Dest", key: "destination" }, { label: "Price", num: true, render: (o) => fmt.usd(o.unit_price) }, { label: "Penalty/unit·wk", num: true, render: (o) => fmt.usd(o.penalty_per_unit_week) }], p.orders)];
  if (t === "supply") return [h("h3", null, "Supplier commitments"), table([{ label: "ID", render: (c) => h("b", null, c.id) }, { label: "Part", key: "part_id" }, { label: "Qty", num: true, render: (c) => fmt.int(c.qty) }, { label: "Promised", render: (c) => wk(c.promised_week) },
    { label: "Status", render: (c) => stateBadge(c.status) }, { label: "Confidence", num: true, render: (c) => c.confidence }, { label: "Source", render: (c) => `${c.provenance.source} · ${fmt.dt(c.provenance.observed_at)}` }], p.commitments),
    h("h3", { style: "margin-top:16px" }, "Inventory lots"), table([{ label: "Lot", render: (l) => h("b", null, l.id) }, { label: "Part", key: "part_id" }, { label: "Qty", num: true, render: (l) => fmt.int(l.qty) }, { label: "Status", render: (l) => stateBadge(l.status) },
    { label: "Available", render: (l) => wk(l.available_week) }, { label: "Source", render: (l) => l.provenance.source }], p.inventory)];
  if (t === "capacity") {
    const byRes = {};
    p.capacity.forEach((b) => { (byRes[b.resource_id] ||= []).push(b); });
    return [table([{ label: "Resource", render: (r) => h("b", null, r[0]) }, { label: "Weeks", num: true, render: (r) => r[1].length }, { label: "Reserved total", num: true, render: (r) => fmt.int(r[1].reduce((s, b) => s + b.reserved, 0)) },
      { label: "Nominal total", num: true, render: (r) => fmt.int(r[1].reduce((s, b) => s + b.nominal, 0)) }, { label: "Window", render: (r) => `${wk(r[1][0].week)} → ${wk(r[1][r[1].length - 1].week)}` },
      { label: "Tentative", render: (r) => r[1].some((b) => b.status === "tentative") ? badge("tentative", "warn") : "" }], Object.entries(byRes))];
  }
  if (t === "routes") return [h("h3", null, "Routes"), table([{ label: "Route", render: (r) => h("b", null, r.id) }, { label: "Name", key: "name" }, { label: "Qualification", render: (r) => stateBadge(r.qualification === "qualified" ? "confirmed" : "pending") },
    { label: "Yield (low/base/high ‰)", render: (r) => `${r.yield_pm.low}/${r.yield_pm.base}/${r.yield_pm.high}` }, { label: "Lead (wk)", num: true, render: (r) => r.lead_weeks }, { label: "Extra cost/unit", num: true, render: (r) => fmt.usd(r.extra_cost_per_unit) },
    { label: "Primary", render: (r) => r.is_primary ? "yes" : "" }], p.routes),
    h("h3", { style: "margin-top:16px" }, "Recovery levers"), table([{ label: "Lever", render: (l) => h("b", null, l.id) }, { label: "Type", render: (l) => l.type.replaceAll("_", " ") }, { label: "Target", key: "target_id" },
      { label: "Cap", num: true, render: (l) => fmt.int(l.qty_cap) }, { label: "Available", render: (l) => wk(l.available_week) }, { label: "Cost", num: true, render: (l) => fmt.usd(l.fixed_cost + l.unit_cost * l.qty_cap + l.relationship_cost) },
      { label: "Approvers", render: (l) => l.approver_roles.join(", ") }], p.levers)];
  if (t === "history") {
    const a = h("select", null, snaps.map((s) => h("option", { value: s.version }, `v${s.version}`))), b = h("select", null, snaps.map((s, i) => h("option", { value: s.version, selected: i === 0 }, `v${s.version}`)));
    const out = h("div", { style: "margin-top:10px" });
    if (snaps.length > 1) a.value = snaps[1].version;
    const diff = async () => { try { const r = await get(`/v1/snapshots/diff?a=${a.value}&b=${b.value}`); out.replaceChildren(r.length ? table([{ label: "Collection", key: "collection" }, { label: "Id", render: (x) => h("span", { class: "mono" }, x.id || "") }, { label: "Change", render: (x) => badge(x.change, x.change === "added" ? "ok" : x.change === "removed" ? "bad" : "info") }, { label: "Fields", render: (x) => x.fields ? h("span", { class: "mono small" }, Object.entries(x.fields).map(([k, v]) => `${k}: ${v[0]} → ${v[1]}`).join("; ")) : "" }], r) : h("div", { class: "empty" }, "Identical.")); } catch (e) { toast(e.message, "err"); } };
    return [table([{ label: "Version", render: (s) => h("b", null, `v${s.version}`) }, { label: "When", render: (s) => fmt.dt(s.created_at) }, { label: "By", render: (s) => s.created_by }, { label: "Reason", key: "reason" }, { label: "Events", render: (s) => s.event_ids.length || "" }], snaps),
      h("div", { class: "row", style: "margin-top:14px" }, h("span", { class: "muted" }, "Compare"), a, h("span", null, "→"), b, h("button", { class: "btn", onclick: diff }, "Show diff")), out];
  }
  return [];
}

function importCard(ctx) {
  const json = h("input", { type: "file", accept: "application/json,.json" }), reason = h("input", { type: "text", placeholder: "Reason (audit log)", value: "dataset import" });
  const files = { orders: h("input", { type: "file", accept: ".csv" }), commitments: h("input", { type: "file", accept: ".csv" }), inventory: h("input", { type: "file", accept: ".csv" }), capacity: h("input", { type: "file", accept: ".csv" }) };
  const upJson = async () => {
    const f = json.files[0]; if (!f) return toast("Choose a JSON file", "err");
    try { const parsed = JSON.parse(await f.text()); const r = await post("/v1/dataset", { plan: parsed.plan || parsed, reason: reason.value }); toast(`Imported as snapshot v${r.version}`, "ok"); ctx.refresh(); } catch (e) { toast(e.message, "err"); }
  };
  const upCsv = async () => {
    const fd = new FormData(); let n = 0;
    for (const [k, el] of Object.entries(files)) if (el.files[0]) { fd.append(k, el.files[0]); n++; }
    if (!n) return toast("Choose at least one CSV", "err");
    try { const r = await api("POST", "/v1/dataset/tables", fd); toast(`Imported as snapshot v${r.version}`, "ok"); ctx.refresh(); } catch (e) { toast(e.message, "err"); }
  };
  return h("div", { class: "grid g2", style: "margin-top:16px" },
    h("div", { class: "card" }, h("h2", null, "Import full dataset (JSON)"), h("p", { class: "muted small" }, "A complete PlanInput document (master data + operational tables). Validated for referential integrity; errors are listed, nothing is half-imported."),
      json, h("label", null, "Reason"), reason, h("div", { style: "margin-top:12px" }, h("button", { class: "btn primary", onclick: upJson }, "Validate and import"))),
    h("div", { class: "card" }, h("h2", null, "Replace operational tables (CSV)"), h("p", { class: "muted small" }, "Layered on the current master data. Row errors are reported with line numbers."),
      Object.entries(files).map(([k, el]) => h("div", null, h("label", null, `${k}.csv`), el)), h("div", { style: "margin-top:12px" }, h("button", { class: "btn primary", onclick: upCsv }, "Validate and import"))));
}
