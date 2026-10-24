import { get } from "../api.js";
import { fmt, h, stateBadge, table } from "../util.js";

const STATES = ["", "analyzing", "options_ready", "pending_approval", "approved", "executing", "completed", "rejected", "stale", "no_impact", "superseded", "analysis_failed"];

export default async function (root, ctx) {
  if (ctx.params[0]) return (await import("./incident.js")).default(root, ctx);
  const q = new URLSearchParams(location.hash.split("?")[1] || "").get("state") || "";
  const list = await get("/v1/incidents" + (q ? `?state=${q}` : ""));
  root.append(h("div", { class: "page-head" }, h("div", { class: "grow" }, h("h1", null, "Incidents"),
    h("div", { class: "sub" }, "Each material supply change becomes an incident: impact, verified recovery options, approvals, actions and outcome in one place.")),
    h("div", { style: "min-width:220px" }, h("select", { onchange: (e) => { location.hash = `#/incidents${e.target.value ? "?state=" + e.target.value : ""}`; } },
      STATES.map((s) => h("option", { value: s, selected: s === q }, s ? s.replaceAll("_", " ") : "all states")))),
    h("a", { class: "btn primary", href: "#/signals" }, "Report a supply change")),
    h("div", { class: "card" }, table([
      { label: "Incident", render: (i) => h("div", null, h("b", null, i.title), h("div", { class: "muted small mono" }, i.id)) },
      { label: "State", render: (i) => stateBadge(i.state) }, { label: "Detected", render: (i) => fmt.dt(i.detected_at) },
      { label: "Snapshots", render: (i) => `v${i.snapshot_before} → v${i.snapshot_after}` },
      { label: "On-time units at risk", num: true, render: (i) => fmt.int(i.units_on_time_lost_if_no_action) },
      { label: "Exposure", num: true, render: (i) => fmt.usd(i.exposure_delta_if_no_action) },
      { label: "Selected", render: (i) => i.selected_option ? i.selected_option.replace("opt_", "").replaceAll("_", " ") : "-" },
    ], list, { empty: "No incidents.", onRow: (i) => ctx.nav(`#/incidents/${i.id}`) })));
}
