import { get, post } from "../api.js";
import { badge, fmt, h, jsonPre, kpi, table, tabs, toast } from "../util.js";

export default async function (root, ctx) {
  const [metrics, calib, verify] = await Promise.all([get("/v1/metrics"), get("/v1/calibration"), get("/v1/audit/verify")]);
  const canWrite = ctx.me.roles.some((r) => r === "planner" || r === "admin");
  root.append(h("div", { class: "page-head" }, h("div", { class: "grow" }, h("h1", null, "Audit & learning"),
    h("div", { class: "sub" }, "Every change is hash-chained. Outcomes are compared with the plan and reported for humans to act on; nothing is silently fed back into constraints."))),
    h("div", { class: "grid g4" },
      kpi("Audit chain", verify.ok ? "intact" : "BROKEN", verify.ok ? `${verify.entries} entries` : `broken at #${verify.broken_at}`),
      kpi("Detect → plan", fmt.hours(metrics.hours_detect_to_plan_mean), "mean"), kpi("Detect → approval", fmt.hours(metrics.hours_detect_to_approval_mean), "mean"),
      kpi("Extraction confirm rate", metrics.extraction_confirm_rate == null ? "-" : fmt.pct(100 * metrics.extraction_confirm_rate, 0), "proposals accepted by humans")));
  const body = h("div", { class: "card", style: "margin-top:16px" });
  root.append(tabs([["audit", "Audit log"], ["metrics", "Metrics"], ["calib", "Calibration"], ["outcome", "Record outcome"]], "audit", async (t) => {
    body.replaceChildren(h("div", { class: "empty" }, "…"));
    body.replaceChildren(...await panel(t, metrics, calib, canWrite, ctx));
  }), body);
}

async function panel(t, metrics, calib, canWrite, ctx) {
  if (t === "audit") {
    const rows = await get("/v1/audit?limit=300");
    return [h("div", { class: "row", style: "margin-bottom:10px" }, h("button", { class: "btn", onclick: async () => { const v = await get("/v1/audit/verify"); toast(v.ok ? `Chain intact: ${v.entries} entries` : `BROKEN at #${v.broken_at}`, v.ok ? "ok" : "err"); } }, "Re-verify chain"), h("span", { class: "muted small" }, "Newest first, latest 300")),
      table([{ label: "#", key: "seq" }, { label: "When", render: (r) => fmt.dt(r.ts) }, { label: "Who", render: (r) => h("span", { class: "mono" }, r.actor) }, { label: "Action", render: (r) => h("b", null, r.action) },
        { label: "Entity", render: (r) => h("span", { class: "mono small" }, `${r.entity_type}:${r.entity_id}`) }, { label: "Data", render: (r) => h("span", { class: "mono small" }, JSON.stringify(r.data).slice(0, 120)) },
        { label: "Hash", render: (r) => h("span", { class: "mono muted" }, r.hash.slice(0, 10)) }], rows)];
  }
  if (t === "metrics") return [h("div", { class: "grid g3" }, ...["incidents", "actions", "proposals"].map((k) => h("div", null, h("h3", null, k), table([{ label: "State", render: (r) => r[0] }, { label: "Count", num: true, render: (r) => r[1] }], Object.entries(metrics[k] || {}), { empty: "none" })))),
    h("div", { class: "row", style: "margin-top:16px" }, kpi("Open conflicts", metrics.open_conflicts), kpi("Plans stale at execution", metrics.plan_infeasible_at_execution), kpi("Acceptance", metrics.recommendation_acceptance_rate == null ? "-" : fmt.pct(100 * metrics.recommendation_acceptance_rate, 0))),
    h("h3", { style: "margin-top:16px" }, "Rejection reasons"), table([{ label: "Reason", render: (r) => r[0] }, { label: "Count", num: true, render: (r) => r[1] }], Object.entries(metrics.rejection_reasons), { empty: "No rejections." })];
  if (t === "calib") return [h("div", { class: "note" }, calib.note), h("h3", { style: "margin:14px 0 6px" }, "Supplier reliability"),
    table([{ label: "Supplier", render: (r) => h("b", null, r[0]) }, { label: "Commitments", num: true, render: (r) => r[1].commitments }, { label: "Fully on time", num: true, render: (r) => r[1].fully_on_time }, { label: "Reliability (Laplace)", num: true, render: (r) => r[1].reliability_laplace }], Object.entries(calib.suppliers), { empty: "No supplier outcomes recorded yet." }),
    h("div", { class: "grid g2", style: "margin-top:16px" }, kpi("Delivery error (mean)", calib.delivery_error_pct_mean == null ? "-" : fmt.pct(calib.delivery_error_pct_mean), `${calib.delivery_samples} samples`), kpi("Yield routes observed", Object.keys(calib.yield).length))];
  // record outcome
  const kind = h("select", null, ["supplier_commitment", "delivery", "yield"].map((k) => h("option", { value: k }, k.replaceAll("_", " "))));
  const subject = h("input", { type: "text", placeholder: "Supplier, order or route id" });
  const pq = h("input", { type: "number", placeholder: "planned qty / units / yield‰" }), aq = h("input", { type: "number", placeholder: "actual qty / units / yield‰" });
  const pw = h("input", { type: "number", placeholder: "planned week (commitments)" }), aw = h("input", { type: "number", placeholder: "actual week (commitments)" });
  const submit = async () => {
    const k = kind.value, pn = Number(pq.value), an = Number(aq.value);
    const planned = k === "supplier_commitment" ? { qty: pn, week: Number(pw.value) } : k === "delivery" ? { units: pn } : { yield_pm: pn };
    const actual = k === "supplier_commitment" ? { qty: an, week: Number(aw.value) } : k === "delivery" ? { units: an } : { yield_pm: an };
    try { await post("/v1/outcomes", { kind: k, subject: subject.value, planned, actual }); toast("Outcome recorded", "ok"); ctx.refresh(); } catch (e) { toast(e.message, "err"); }
  };
  return [h("div", { class: "form-row" }, h("div", null, h("label", null, "Kind"), kind), h("div", null, h("label", null, "Subject"), subject), h("div", null, h("label", null, "Planned"), pq), h("div", null, h("label", null, "Actual"), aq),
    h("div", null, h("label", null, "Planned week"), pw), h("div", null, h("label", null, "Actual week"), aw)),
    h("div", { style: "margin-top:12px" }, h("button", { class: "btn primary", disabled: !canWrite, onclick: submit }, "Record outcome"))];
}
