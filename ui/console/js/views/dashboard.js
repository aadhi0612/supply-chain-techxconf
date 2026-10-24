import { get } from "../api.js";
import { bar, fmt, h, kpi, legend, spinner, stateBadge, table } from "../util.js";

export default async function (root, ctx) {
  const safe = (p) => get(p).catch((e) => ({ __err: e }));
  const [quality, incidents, metrics, plan, conflicts, proposals] = await Promise.all([
    safe("/v1/dataset/quality"), get("/v1/incidents"), get("/v1/metrics"), safe("/v1/plan"), get("/v1/conflicts"), get("/v1/proposals?status=pending")]);
  const noData = quality.__err;
  const open = incidents.filter((i) => ["analyzing", "options_ready", "pending_approval", "approved", "executing", "stale"].includes(i.state));

  // incidents that wait on *this* user: pending approval where one of my roles is required and not yet approved
  const waiting = [];
  for (const i of incidents.filter((x) => x.state === "pending_approval").slice(0, 6)) {
    const d = await get(`/v1/incidents/${i.id}?full=false`);
    const done = new Set(d.approvals.filter((a) => a.plan_hash === d.plan_hash && a.decision === "approve").map((a) => a.role));
    const mine = d.required_approvals.map((r) => r.role).filter((r) => ctx.me.roles.includes(r) && !done.has(r));
    if (mine.length && d.selected_by !== ctx.me.user_id) waiting.push({ ...i, roles: mine });
  }
  const toReview = incidents.filter((i) => i.state === "options_ready");

  root.append(
    h("div", { class: "page-head" }, h("div", { class: "grow" }, h("h1", null, "Dashboard"),
      h("div", { class: "sub" }, "Where supply stands, what changed, and what is waiting on you.")),
      h("a", { class: "btn primary", href: "#/signals" }, "Report a supply change")),
    h("div", { class: "grid g4" },
      kpi("Dataset", noData ? "none" : `v${(await get("/v1/snapshots"))[0].version}`, noData ? "Import data to begin" : `${fmt.int(quality.metrics.orders_total)} orders · ${fmt.int(quality.metrics.demand_units_total ?? 0)} units demanded`),
      kpi("Open incidents", open.length, `${toReview.length} to review · ${waiting.length} awaiting your approval`),
      kpi("Open conflicts", conflicts.length, conflicts.length ? "Lower-authority sources disagree with current facts" : "All facts reconciled"),
      kpi("Pending proposals", proposals.length, proposals.length ? "Extracted from notices; needs human confirmation" : "Nothing waiting"),
    ),
    h("div", { class: "grid g2", style: "margin-top:16px" },
      h("div", { class: "card" }, h("h2", null, "Needs your attention"),
        waiting.length || toReview.length || proposals.length ? h("div", { class: "stack" },
          waiting.map((i) => h("div", { class: "note warn" }, h("div", { class: "row sb" }, h("span", null, h("b", null, i.title), " · approve as ", i.roles.join(", ")), h("a", { class: "btn sm primary", href: `#/incidents/${i.id}` }, "Open")))),
          toReview.map((i) => h("div", { class: "note" }, h("div", { class: "row sb" }, h("span", null, h("b", null, i.title), " · options ready"), h("a", { class: "btn sm", href: `#/incidents/${i.id}` }, "Review")))),
          proposals.length ? h("div", { class: "note" }, h("div", { class: "row sb" }, h("span", null, `${proposals.length} extracted proposal(s) need confirmation`), h("a", { class: "btn sm", href: "#/signals" }, "Review"))) : null,
        ) : h("div", { class: "empty" }, "Nothing is waiting on you.")),
      h("div", { class: "card" }, h("h2", null, "Governance"),
        h("div", { class: "grid g2" },
          kpi("Detect → plan", fmt.hours(metrics.hours_detect_to_plan_mean), "mean time to options"),
          kpi("Acceptance", metrics.recommendation_acceptance_rate == null ? "-" : fmt.pct(100 * metrics.recommendation_acceptance_rate, 0), "recommended option chosen"),
          kpi("Audit chain", metrics.audit_chain.ok ? "intact" : "BROKEN", `${metrics.audit_chain.entries} entries`),
          kpi("Plan stale at exec", metrics.plan_infeasible_at_execution, "blocked by re-verification")))),
    h("div", { class: "card", style: "margin-top:16px" },
      h("h2", null, "Current plan", h("span", { class: "grow" }), legend()),
      plan.__err ? h("div", { class: "empty" }, "No plan yet.") : planTable(plan)),
    h("div", { class: "card", style: "margin-top:16px" }, h("h2", null, "Recent incidents"),
      table([{ label: "Incident", render: (i) => h("b", null, i.title) }, { label: "State", render: (i) => stateBadge(i.state) },
        { label: "Detected", render: (i) => fmt.dt(i.detected_at) }, { label: "On-time units at risk", num: true, render: (i) => fmt.int(i.units_on_time_lost_if_no_action) },
        { label: "Exposure", num: true, render: (i) => fmt.usd(i.exposure_delta_if_no_action) }], incidents.slice(0, 6),
        { empty: "No incidents yet. Report a supply change to create one.", onRow: (i) => ctx.nav(`#/incidents/${i.id}`) })),
  );
}

export function planTable(plan) {
  const res = plan.result;
  return table([
    { label: "Order", render: (o) => h("b", null, o.order_id) }, { label: "Customer", key: "customer_id" },
    { label: "Qty", num: true, render: (o) => fmt.int(o.qty) },
    { label: "Outcome", render: (o) => h("div", { style: "min-width:200px" }, bar(o.on_time, o.late, o.unfulfilled)) },
    { label: "On time", num: true, render: (o) => fmt.int(o.on_time) }, { label: "Late", num: true, render: (o) => fmt.int(o.late) },
    { label: "Short", num: true, render: (o) => fmt.int(o.unfulfilled) },
    { label: "Status", render: (o) => stateBadge(o.status) }], res.orders);
}
