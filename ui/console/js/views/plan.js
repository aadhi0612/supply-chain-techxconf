import { get } from "../api.js";
import { bar, fmt, h, kpi, legend, stateBadge, table, weekDate } from "../util.js";

const PROFILES = [["no_spend", "Re-allocate only (no spend)"], ["balanced", "Balanced (with levers)"], ["max_fulfilment", "Maximum fulfilment"], ["robust", "Conservative (worst case)"]];

export default async function (root, ctx) {
  const profile = new URLSearchParams(location.hash.split("?")[1] || "").get("profile") || "no_spend";
  const plan = await get(`/v1/plan?profile=${profile}`);
  const r = plan.result, t = r.totals, ps = plan.plan_start;
  const sel = h("select", { onchange: (e) => { location.hash = `#/plan?profile=${e.target.value}`; } }, PROFILES.map(([v, l]) => h("option", { value: v, selected: v === profile }, l)));
  root.append(
    h("div", { class: "page-head" }, h("div", { class: "grow" }, h("h1", null, "Plan & orders"),
      h("div", { class: "sub" }, "What can ship, when, and which constraint binds, for the current snapshot (v" + plan.version + "). Every plan is independently re-verified.")),
      h("div", { style: "min-width:260px" }, sel)),
    h("div", { class: "grid g4" },
      kpi("On time", fmt.int(t.on_time_units), `of ${fmt.int(t.demand_units)} demanded`), kpi("Late", fmt.int(t.late_units), "inside contract window"),
      kpi("Unfulfilled", fmt.int(t.unfulfilled_units), "incl. compliance-held orders"), kpi("Exposure", fmt.usd(t.exposure), `${fmt.usd(t.total_cost)} route + penalty cost`)),
    h("div", { class: "row", style: "margin:14px 0" },
      r.verified ? h("span", { class: "badge ok" }, "independently verified") : h("span", { class: "badge bad" }, "verification failed"),
      h("span", { class: "badge" }, `solver ${r.solver_status.toLowerCase()}`), h("span", { class: "badge" }, `scenario ${r.scenario}`),
      h("span", { class: "muted small" }, r.levels.map((l) => `${l.name.replaceAll("_", " ")}: ${fmt.int(l.value)}${l.gap_proven ? "" : " (within gap)"}`).join(" · "))),
    h("div", { class: "card" }, h("h2", null, "Orders", h("span", { class: "grow" }), legend()),
      table([
        { label: "Order", render: (o) => h("div", null, h("b", null, o.order_id), h("div", { class: "muted small" }, `${o.customer_id} · ${o.sku_id}`)) },
        { label: "Committed", render: (o) => weekDate(ps, o.committed_week) },
        { label: "Outcome", render: (o) => h("div", { style: "min-width:220px" }, bar(o.on_time, o.late, o.unfulfilled), h("div", { class: "muted small" }, `${fmt.int(o.on_time)} / ${fmt.int(o.late)} / ${fmt.int(o.unfulfilled)} of ${fmt.int(o.qty)}`)) },
        { label: "Arrivals", render: (o) => o.schedule.length ? h("div", { class: "small" }, o.schedule.slice(0, 4).map(([w, q]) => h("div", null, `${weekDate(ps, w)}: ${fmt.int(q)}`)), o.schedule.length > 4 ? h("div", { class: "muted" }, `+${o.schedule.length - 4} more`) : null) : "-" },
        { label: "Routes", render: (o) => Object.entries(o.routes).map(([k, v]) => h("div", { class: "small" }, `${k}: ${fmt.int(v)}`)) },
        { label: "Compliance", render: (o) => h("div", null, stateBadge(plan.gates[o.order_id].status), h("div", { class: "muted small", style: "max-width:260px" }, plan.gates[o.order_id].reasons[0] || "")) },
        { label: "Status", render: (o) => stateBadge(o.status) },
      ], r.orders)),
    h("div", { class: "card", style: "margin-top:16px" }, h("h2", null, "Resource and supply pools"),
      h("p", { class: "muted small" }, "Peak utilisation of each pool the plan uses. Pools at 100% are binding: relaxing them is what recovers units."),
      table([{ label: "Pool", render: (p) => h("b", null, p[0]) }, { label: "Peak utilisation", render: (p) => h("div", { style: "min-width:200px" }, h("div", { class: "bar" }, h("i", { class: p[1] >= 0.999 ? "un" : "ot", style: `width:${Math.min(100, p[1] * 100)}%` }))) },
        { label: "", num: true, render: (p) => fmt.pct(100 * p[1], 0) }, { label: "Binding", render: (p) => r.tight_pools.includes(p[0]) ? h("span", { class: "badge bad" }, "tight") : "" }],
        Object.entries(r.utilization).sort((a, b) => b[1] - a[1]), { empty: "No pools used." })),
  );
}
