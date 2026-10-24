import { get, post } from "../api.js";
import { askText, badge, bar, fmt, h, jsonPre, legend, modal, spinner, stateBadge, table, toast, weekDate } from "../util.js";

const STEPS = [["analyzing", "Analysing"], ["options_ready", "Options"], ["pending_approval", "Approval"], ["approved", "Approved"], ["executing", "Executing"], ["completed", "Done"]];

export default async function (root, ctx) {
  const id = ctx.params[0];
  let d = await get(`/v1/incidents/${id}`);
  if (d.state === "analyzing") {
    root.append(header(d, ctx), h("div", { class: "card" }, spinner("Analysing: building recovery options, verifying each plan and probing binding constraints. This usually takes 15 to 60 seconds…")));
    const t = setInterval(async () => {
      try { const x = await get(`/v1/incidents/${id}?full=false`); if (x.state !== "analyzing" && ctx.isCurrent()) { clearInterval(t); ctx.refresh(); } } catch { /* retry next tick */ }
    }, 2000);
    ctx.onCleanup(() => clearInterval(t));
    return;
  }
  const A = d.analysis || {}, ps = A.plan_start;
  const canWrite = ctx.me.roles.some((r) => r === "planner" || r === "admin");
  const reload = () => ctx.refresh();
  const act = async (fn, okMsg) => { try { const r = await fn(); if (okMsg) toast(okMsg, "ok"); return r ?? true; } catch (e) { toast(e.message, "err"); return null; } };

  root.append(header(d, ctx), stepper(d.state));
  if (d.state === "analysis_failed") root.append(h("div", { class: "note bad" }, h("b", null, "Analysis failed: "), d.error || "unknown error", " ", canWrite ? h("button", { class: "btn sm", onclick: () => replan(d, ctx) }, "Re-run on latest data") : null));
  if (d.state === "stale") root.append(h("div", { class: "note bad" }, h("b", null, "This plan is no longer feasible on current data. "), "Execution was refused. Re-plan on the latest snapshot.", " ", canWrite ? h("button", { class: "btn sm primary", onclick: () => replan(d, ctx) }, "Re-plan") : null));
  if (d.state === "no_impact") root.append(h("div", { class: "note ok" }, h("b", null, "No order is worse off. "), "The pre-incident plan still holds under the new facts, so no action is needed. Options are shown for reference."));
  if (!A.options) return;

  // ---- impact
  const sq = A.status_quo.impact;
  root.append(h("div", { class: "grid g4", style: "margin-bottom:16px" },
    kpiCard("On-time units at risk", fmt.int(sq.units_on_time_lost), "if nobody acts"), kpiCard("Exposure", fmt.usd(sq.exposure_delta), "revenue at risk + penalties"),
    kpiCard("Orders worse off", sq.orders.filter((o) => o.on_time_after < o.on_time_before || o.delivered_after < o.delivered_before).length, `of ${sq.orders.length}`),
    kpiCard("Events", d.event_ids.length, `snapshot v${d.snapshot_before} → v${d.snapshot_after}`)));
  root.append(h("div", { class: "card" }, h("h2", null, "Impact if nobody acts", h("span", { class: "grow" }), legend()),
    h("p", { class: "muted small" }, "The pre-event plan evaluated against the new facts. 'Direct' orders depend on a changed entity; 'indirect' orders lose supply through shared pools."),
    table([{ label: "Order", render: (o) => h("div", null, h("b", null, o.order_id), h("div", { class: "muted small" }, o.customer_id)) },
      { label: "Dependency", render: (o) => h("div", null, badge(o.dependency, o.dependency === "direct" ? "warn" : o.dependency === "indirect" ? "info" : ""), o.dependency_path.length ? h("div", { class: "muted small mono", style: "max-width:260px" }, o.dependency_path.join(" → ")) : null) },
      { label: "Before", render: (o) => h("div", { style: "min-width:150px" }, bar(o.on_time_before, o.delivered_before - o.on_time_before, o.unfulfilled_before), h("div", { class: "small muted" }, `${fmt.int(o.on_time_before)} on time`)) },
      { label: "After", render: (o) => h("div", { style: "min-width:150px" }, bar(o.on_time_after, o.delivered_after - o.on_time_after, o.unfulfilled_after), h("div", { class: "small muted" }, `${fmt.int(o.on_time_after)} on time`)) },
      { label: "Δ exposure", num: true, render: (o) => o.exposure_delta ? fmt.usd(o.exposure_delta) : "-" },
      { label: "Status", render: (o) => h("span", null, stateBadge(o.status_before), " → ", stateBadge(o.status_after)) }], sq.orders)));

  // ---- binding
  if (A.binding && A.binding.pools.length) {
    root.append(h("div", { class: "card", style: "margin-top:16px" }, h("h2", null, "Binding constraints"),
      h("p", { class: "muted small" }, `Found by relaxing each tight pool in the recommended plan and re-solving. Relaxing all of them together recovers ${fmt.int(A.binding.joint_recovered_on_time)} on-time units.`),
      table([{ label: "Pool", render: (p) => h("b", null, p.label) }, { label: "Recovered on-time units", num: true, render: (p) => fmt.int(p.recovered_on_time_units) },
        { label: "Orders helped", render: (p) => Object.entries(p.per_order_delivered).map(([o, v]) => h("span", { class: "chip", style: "margin-right:4px" }, `${o} +${fmt.int(v)}`)) }], A.binding.pools)));
  }

  // ---- options
  const selectable = canWrite && ["options_ready", "pending_approval", "rejected"].includes(d.state);
  const grid = h("div", { class: "grid g2" }, A.options.map((o) => optionCard(o, d, ps, selectable, async () => {
    if (await act(() => post(`/v1/incidents/${id}/select`, { option_id: o.id }), "Option selected. Approvals required.")) reload();
  })));
  root.append(h("div", { style: "margin-top:16px" }, h("h2", { style: "margin:0 0 10px" }, "Recovery options"),
    h("p", { class: "muted small" }, "Every option is independently re-verified against the raw data. Scenario ranges show what the plan still delivers if reality is better or worse than expected (a committed plan can only degrade)."), grid));
  if (A.rejected_options && A.rejected_options.length) root.append(h("p", { class: "muted small" }, "Not shown: ", A.rejected_options.map((r) => `${r.profile} (${r.reason})`).join("; ")));

  // ---- approvals
  if (d.selected_option) root.append(approvals(d, ctx, act, reload));

  // ---- actions
  if (d.actions.length || d.state === "approved") root.append(actionsCard(d, ctx, canWrite, act, reload, ps));

  // ---- audit
  root.append(await auditCard(id));
}

function header(d, ctx) {
  return h("div", { class: "page-head" }, h("div", { class: "grow" }, h("a", { href: "#/incidents", class: "small" }, "← Incidents"), h("h1", { style: "margin-top:6px" }, d.title),
    h("div", { class: "sub row" }, stateBadge(d.state), h("span", { class: "mono" }, d.id), h("span", null, `detected ${fmt.dt(d.detected_at)}`), d.parent_id ? h("a", { href: `#/incidents/${d.parent_id}` }, "re-plan of earlier incident") : null)));
}
function stepper(state) {
  const idx = STEPS.findIndex((s) => s[0] === state);
  const bad = ["rejected", "stale", "analysis_failed"].includes(state);
  const pos = bad ? (state === "rejected" ? 2 : 1) : idx < 0 ? 1 : idx;
  return h("div", { class: "stepper" }, STEPS.map(([, label], i) => h("div", { class: `st ${bad && i === pos ? "bad" : i < pos || state === "completed" ? "done" : i === pos ? "now" : ""}` }, label)));
}
const kpiCard = (l, v, s) => h("div", { class: "card kpi" }, h("div", { class: "l" }, l), h("div", { class: "v" }, v), h("div", { class: "s" }, s));

function optionCard(o, d, ps, selectable, onSelect) {
  const t = o.result.totals, selected = d.selected_option === o.id;
  const rng = Object.fromEntries(o.scenario_range.map((r) => [r.scenario, r.on_time_units]));
  const max = Math.max(1, t.demand_units - 0, ...Object.values(rng));
  const mini = ["best", "base", "worst"].map((k) => h("div", { class: "mb" }, h("span", { class: "muted" }, k), h("div", { class: "track" }, h("i", { style: `width:${(100 * rng[k]) / max}%` })), h("span", { class: "num" }, fmt.int(rng[k]))));
  return h("div", { class: `opt ${o.recommended ? "rec" : ""} ${selected ? "sel" : ""}` },
    h("div", { class: "row sb" }, h("div", { class: "row" }, badge(`#${o.rank}`, "info"), h("h3", null, o.title)), h("div", { class: "row" }, o.recommended ? badge("recommended", "warn") : null, selected ? badge("selected", "ok") : null, o.result.verified ? badge("verified", "ok") : badge("unverified", "bad"))),
    h("div", { class: "muted small" }, o.description),
    h("div", { class: "row", style: "align-items:flex-end;gap:22px" }, h("div", null, h("div", { class: "big" }, fmt.int(t.on_time_units)), h("div", { class: "muted small" }, "on-time units")),
      h("div", null, h("div", { class: "big" }, o.incremental_cost_vs_no_spend > 0 ? fmt.usd(o.incremental_cost_vs_no_spend) : "$0"), h("div", { class: "muted small" }, "extra spend vs no-spend"))),
    h("div", { class: "kv" }, h("span", null, "Delivered (incl. late)"), h("span", null, fmt.int(t.delivered_units)), h("span", null, "Late units"), h("span", null, fmt.int(t.late_units)),
      h("span", null, "Unfulfilled"), h("span", null, fmt.int(t.unfulfilled_units)), h("span", null, "Penalty + route cost"), h("span", null, fmt.usd(t.penalty_cost + t.route_cost)),
      h("span", null, "Cost per extra on-time unit"), h("span", null, o.cost_per_extra_on_time_unit ? fmt.usd(o.cost_per_extra_on_time_unit) : "-")),
    h("div", null, h("div", { class: "muted small", style: "margin-bottom:4px" }, "On-time units if reality is…"), h("div", { class: "minibars" }, mini)),
    o.result.levers.length ? h("div", null, h("div", { class: "muted small" }, "Levers used"), o.result.levers.map((l) => h("div", { class: "small" }, h("b", null, l.lever_id), ` · ${l.type.replaceAll("_", " ")} · ${fmt.usd(l.cost)}`))) : null,
    h("div", null, o.required_approvals.map((r) => h("span", { class: "chip", title: r.reasons.join(" "), style: "margin:0 4px 4px 0" }, r.role))),
    o.risks.length ? h("ul", null, o.risks.map((r) => h("li", null, r))) : null,
    h("details", null, h("summary", null, "Orders, schedule and explanation"),
      table([{ label: "Order", key: "order_id" }, { label: "Outcome", render: (x) => h("div", { style: "min-width:130px" }, bar(x.on_time, x.late, x.unfulfilled)) },
        { label: "On time", num: true, render: (x) => fmt.int(x.on_time) }, { label: "Late", num: true, render: (x) => fmt.int(x.late) }, { label: "Short", num: true, render: (x) => fmt.int(x.unfulfilled) },
        { label: "Last arrival", render: (x) => weekDate(ps, x.last_arrival_week) }], o.result.orders),
      o.explanations.length ? h("ul", { style: "color:var(--ink);font-size:13px;margin-top:8px" }, o.explanations.map((e) => h("li", null, e))) : null,
      h("div", { class: "muted small", style: "margin-top:6px" }, "Solver levels: ", o.result.levels.map((l) => `${l.name.replaceAll("_", " ")} ${fmt.int(l.value)}${l.gap_proven ? "" : ` (bound ${fmt.int(l.bound)})`}`).join(" · "))),
    selectable ? h("button", { class: `btn ${o.recommended ? "primary" : ""}`, onclick: onSelect }, selected ? "Selected" : "Select this option") : null);
}

function approvals(d, ctx, act, reload) {
  const me = ctx.me;
  const latest = {};
  d.approvals.filter((a) => a.plan_hash === d.plan_hash).forEach((a) => { latest[a.role] = a; });
  const open = d.state === "pending_approval";
  // mirror the server's four-eyes rules so people are never offered an action that will be refused
  const mineApproved = d.approvals.find((a) => a.plan_hash === d.plan_hash && a.user === me.user_id && a.decision === "approve");
  const canAct = (role) => open && d.selected_by !== me.user_id && me.roles.includes(role) && latest[role]?.decision !== "approve" && (!mineApproved || mineApproved.role === role);
  return h("div", { class: "card", style: "margin-top:16px" }, h("h2", null, "Approvals"),
    h("p", { class: "muted small" }, `Option ${d.selected_option.replace("opt_", "")} selected by ${d.selected_by_name || d.selected_by}. Four-eyes: the proposer cannot approve, and one person cannot satisfy two roles. Changing the option voids these approvals.`),
    table([{ label: "Role", render: (r) => h("b", null, r.role) }, { label: "Why required", render: (r) => h("div", { class: "small" }, r.reasons.map((x) => h("div", null, x))) },
      { label: "Status", render: (r) => latest[r.role] ? h("div", null, stateBadge(latest[r.role].decision === "approve" ? "confirmed" : "declined"), h("div", { class: "muted small" }, `${latest[r.role].user_name || latest[r.role].user}${latest[r.role].comment ? ": " + latest[r.role].comment : ""}`)) : stateBadge("pending") },
      { label: "", render: (r) => canAct(r.role) ? h("div", { class: "row" },
        h("button", { class: "btn sm primary", onclick: async () => { const c = await askText(`Approve as ${r.role}`, "Comment (optional)", { required: false, confirm: "Approve" }); if (c === null) return; if (await act(() => post(`/v1/incidents/${d.id}/decisions`, { decision: "approve", role: r.role, comment: c }), "Approved")) reload(); } }, "Approve"),
        h("button", { class: "btn sm danger", onclick: async () => { const c = await askText(`Reject as ${r.role}`, "Reason for rejection", { confirm: "Reject", danger: true }); if (!c) return; if (await act(() => post(`/v1/incidents/${d.id}/decisions`, { decision: "reject", role: r.role, comment: c }), "Rejected")) reload(); } }, "Reject")) : "" },
    ], d.required_approvals),
    open && d.selected_by === me.user_id ? h("div", { class: "note warn", style: "margin-top:10px" }, "You selected this option, so someone else must approve it.") : null);
}

function actionsCard(d, ctx, canWrite, act, reload, ps) {
  const canExec = canWrite && ["approved", "executing"].includes(d.state);
  const confirmModal = async (a, outcome) => {
    if (outcome === "declined") { const n = await askText("Counterparty declined", "What did they say?", { confirm: "Record decline", danger: true }); if (!n) return; if (await act(() => post(`/v1/actions/${a.id}/confirmation`, { outcome, note: n }), "Recorded; re-planning")) reload(); return; }
    const qty = h("input", { type: "number", value: a.payload.qty ?? "" }), date = h("input", { type: "date", value: a.payload.date ?? "" });
    const ok = await modal({ title: "Counterparty confirmed", confirm: "Record confirmation", body: h("div", null, h("p", { class: "muted small" }, "The confirmed quantity and date become a sourced fact in a new snapshot."), h("label", null, "Confirmed quantity"), qty, h("label", null, "Confirmed date"), date) });
    if (!ok) return;
    if (await act(() => post(`/v1/actions/${a.id}/confirmation`, { outcome, qty: qty.value ? Number(qty.value) : null, date: date.value || null }), "Confirmation applied to the plan")) reload();
  };
  const rows = d.actions;
  return h("div", { class: "card", style: "margin-top:16px" }, h("h2", null, "Actions", h("span", { class: "grow" }),
    canExec ? h("button", { class: "btn primary", onclick: async () => { if (await act(() => post(`/v1/incidents/${d.id}/execute`), ctx.me.mode === "execute" ? "Executed" : "Drafts created")) reload(); } }, d.state === "approved" ? (ctx.me.mode === "execute" ? "Execute approved plan" : "Create drafts") : "Retry failed / pending") : null),
    ctx.me.mode === "shadow" ? h("div", { class: "note warn" }, "Shadow mode: recommendations only, no actions are produced.") : null,
    rows.length ? table([{ label: "Type", render: (a) => h("div", null, h("b", null, a.type.replaceAll("_", " ")), a.external ? h("div", null, badge("external", "warn")) : null) }, { label: "Target", key: "target" },
      { label: "Status", render: (a) => h("div", null, stateBadge(a.status), a.last_error ? h("div", { class: "small", style: "color:var(--bad)" }, a.last_error) : null, a.attempts > 1 ? h("div", { class: "muted small" }, `${a.attempts} attempts`) : null) },
      { label: "Content", render: (a) => h("details", null, h("summary", null, a.payload.subject || a.payload.change || a.payload.option || a.payload.action || "details"), a.payload.body ? h("pre", { class: "pay" }, a.payload.body) : jsonPre(a.payload)) },
      { label: "", render: (a) => canWrite ? h("div", { class: "row" },
        ["drafted", "failed"].includes(a.status) ? h("button", { class: "btn sm", onclick: async () => { if (await act(() => post(`/v1/actions/${a.id}/mark-sent?note=sent+manually`), "Marked sent")) reload(); } }, "Mark sent") : null,
        a.status === "sent" && a.lever_id ? [h("button", { class: "btn sm primary", onclick: () => confirmModal(a, "confirmed") }, "Confirmed"), h("button", { class: "btn sm danger", onclick: () => confirmModal(a, "declined") }, "Declined")] : null,
        ["sent", "confirmed"].includes(a.status) ? h("button", { class: "btn sm", onclick: async () => { const r = await askText("Revert action", "Reason", { confirm: "Create compensating action" }); if (!r) return; if (await act(() => post(`/v1/actions/${a.id}/revert`, { reason: r }), "Compensating action drafted")) reload(); } }, "Revert") : null) : "" },
    ], rows) : h("div", { class: "empty" }, "Approved. Create the actions to proceed."),
    canWrite ? h("div", { class: "row", style: "margin-top:12px" }, h("button", { class: "btn", onclick: () => replan(d, ctx) }, "Re-plan on latest data")) : null);
}

async function replan(d, ctx) {
  const reason = await askText("Re-plan", "Why re-plan?", { confirm: "Re-plan" }); if (!reason) return;
  try { const r = await post(`/v1/incidents/${d.id}/replan`, { reason, mode: "async" }); ctx.nav(`#/incidents/${r.id}`); } catch (e) { toast(e.message, "err"); }
}

async function auditCard(id) {
  const rows = await get(`/v1/audit?entity_type=incident&entity_id=${id}`);
  return h("div", { class: "card", style: "margin-top:16px" }, h("details", null, h("summary", null, `Audit trail for this incident (${rows.length})`),
    table([{ label: "#", key: "seq" }, { label: "When", render: (r) => fmt.dt(r.ts) }, { label: "Who", key: "actor" }, { label: "What", render: (r) => h("b", null, r.action) },
      { label: "Data", render: (r) => h("span", { class: "mono small" }, JSON.stringify(r.data).slice(0, 140)) }], rows)));
}
