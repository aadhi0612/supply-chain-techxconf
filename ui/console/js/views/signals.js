import { get, post } from "../api.js";
import { askText, badge, fmt, h, jsonPre, spinner, stateBadge, tabs, toast } from "../util.js";

const SOURCES = ["supplier_portal", "edi", "erp", "aps", "email", "spreadsheet", "manual"];
const sel = (opts, value) => h("select", { value }, opts.map((o) => h("option", { value: Array.isArray(o) ? o[0] : o, selected: (Array.isArray(o) ? o[0] : o) === value }, Array.isArray(o) ? o[1] : o)));

export default async function (root, ctx) {
  const [dataset, proposals, conflicts] = await Promise.all([get("/v1/dataset").catch(() => null), get("/v1/proposals?status=pending"), get("/v1/conflicts")]);
  const plan = dataset && dataset.plan;
  const canWrite = ctx.me.roles.includes("planner") || ctx.me.roles.includes("admin");
  root.append(h("div", { class: "page-head" }, h("div", { class: "grow" }, h("h1", null, "Signals"),
    h("div", { class: "sub" }, "Tell the system what changed. Unstructured notices become proposals with verbatim evidence that a human confirms; structured events apply directly. Either way a material change opens an incident.")),
    canWrite ? null : badge("read only: planner role required to submit", "warn")));
  if (!plan) { root.append(h("div", { class: "note warn" }, "Import a dataset first (Dataset page).")); return; }

  const body = h("div", null);
  root.append(tabs([["notice", "Supplier notice"], ["event", "Structured event"], ["queue", `Proposals (${proposals.length})`], ["conflicts", `Conflicts (${conflicts.length})`]], proposals.length ? "queue" : "notice", (t) => {
    body.replaceChildren(t === "notice" ? noticeView(ctx, canWrite) : t === "event" ? eventView(plan, ctx, canWrite) : t === "queue" ? proposalsView(proposals, ctx, canWrite) : conflictsView(conflicts, ctx, canWrite));
  }), body);
}

const EXAMPLE = "Subject: Reserved capacity adjustment - OSAT-A\nFollowing a tool outage, OSAT-A will reduce your reserved 2.5D-L packaging capacity (PKG-A) by 25% in weeks 4-9.\nSeparately, substrate lot LOT-S-77 is on quality hold pending failure analysis.";

function noticeView(ctx, canWrite) {
  const text = h("textarea", { style: "min-height:170px", placeholder: "Paste a supplier email, portal message or notice…" });
  const src = sel(SOURCES, "email");
  const out = h("div", { style: "margin-top:16px" });
  const run = async () => {
    if (!text.value.trim()) return toast("Paste a notice first", "err");
    out.replaceChildren(spinner("Extracting…"));
    try {
      const r = await post("/v1/documents", { source: src.value, text: text.value });
      out.replaceChildren(r.duplicate ? h("div", { class: "note warn" }, "This exact notice was already ingested.") : null,
        r.proposals.length ? proposalCards(r.proposals, ctx) : h("div", { class: "note" }, "No actionable supply event found. Nothing changes without a grounded, validated proposal. Use a structured event if you know what changed."));
      ctx.badges();
    } catch (e) { out.replaceChildren(h("div", { class: "note bad" }, e.message)); }
  };
  return h("div", { class: "card" }, h("h2", null, "Paste a notice"),
    h("div", { class: "form-row" }, h("div", null, h("label", null, "Source (sets precedence)"), src)),
    h("label", null, "Notice text"), text,
    h("div", { class: "row", style: "margin-top:12px" }, h("button", { class: "btn primary", disabled: !canWrite, onclick: run }, "Extract proposals"),
      h("button", { class: "btn", onclick: () => { text.value = EXAMPLE; } }, "Use example notice")),
    h("p", { class: "muted small" }, "Extraction never changes planning state. Ambiguous wording (relative weeks, several matching resources) is flagged for you to confirm."), out);
}

function proposalCards(list, ctx) {
  const checks = new Map();
  const ack = h("input", { type: "checkbox" }), note = h("input", { type: "text", placeholder: "Optional note for the audit log" });
  const wrap = h("div", null);
  const anyAmb = list.some((p) => p.ambiguities.length);
  wrap.append(...list.map((p) => {
    const cb = h("input", { type: "checkbox", checked: true }); checks.set(p.id, cb);
    return h("div", { class: "card", style: "margin-bottom:10px" }, h("div", { class: "row sb" },
      h("label", { class: "inline" }, cb, h("b", null, p.event.type.replaceAll("_", " "))), h("div", { class: "row" }, badge(p.method, "info"), badge(`confidence ${Math.round(p.confidence * 100)}%`, p.confidence >= 0.8 ? "ok" : "warn"))),
      h("div", { class: "mono", style: "margin:8px 0" }, summarise(p.event)),
      p.quotes.map((q) => h("div", { class: "small" }, "Evidence: ", h("span", { class: "quote" }, `“${q}”`))),
      p.ambiguities.map((a) => h("div", { class: "note warn small", style: "margin-top:6px" }, a)),
      p.warnings.map((a) => h("div", { class: "muted small" }, a)),
      h("details", null, h("summary", null, "Full event"), jsonPre(p.event)));
  }));
  wrap.append(h("div", { class: "card" },
    anyAmb ? h("label", { class: "inline" }, ack, "I have reviewed the ambiguities above and confirm the interpretation") : null, h("div", { style: "margin-top:8px" }, note),
    h("div", { class: "row", style: "margin-top:12px" },
      h("button", { class: "btn primary", onclick: async () => {
        const ids = [...checks].filter(([, c]) => c.checked).map(([id]) => id);
        if (!ids.length) return toast("Select at least one proposal", "err");
        try {
          const r = await post("/v1/proposals/confirm", { ids, acknowledge_ambiguities: ack.checked, note: note.value, mode: "async" });
          toast("Applied: snapshot v" + r.snapshot_version, "ok");
          ctx.nav(r.incident_id ? `#/incidents/${r.incident_id}` : "#/dashboard");
        } catch (e) { toast(e.message, "err"); }
      } }, "Confirm selected and open incident"),
      h("button", { class: "btn danger", onclick: async () => {
        const reason = await askText("Reject proposals", "Why are these not valid?", { confirm: "Reject", danger: true }); if (!reason) return;
        for (const [id, c] of checks) if (c.checked) await post(`/v1/proposals/${id}/reject`, { note: reason }).catch((e) => toast(e.message, "err"));
        toast("Rejected", "ok"); ctx.refresh();
      } }, "Reject selected"))));
  return wrap;
}

function summarise(ev) {
  switch (ev.type) {
    case "capacity_change": return `${ev.resource_id}: ${ev.mode.replace("_", " ")} ${ev.value}${ev.mode === "reduce_pct" ? "%" : ""}, ${ev.from_week} → ${ev.to_week}`;
    case "lot_status": return `lot ${ev.lot_id} → ${ev.status}${ev.release_week ? " (release " + ev.release_week + ")" : ""}`;
    case "commitment_change": return `commitment ${ev.commitment_id}: ` + ["qty", "promised_week", "status"].filter((k) => ev[k] != null).map((k) => `${k}=${ev[k]}`).join(", ");
    case "yield_change": return `route ${ev.route_id} yield base ${ev.base}‰`;
    case "logistics_delay": return `lane ${ev.destination}: ${ev.delta_weeks > 0 ? "+" : ""}${ev.delta_weeks} week(s)`;
    case "demand_change": return ev.cancel ? `order ${ev.order_id} cancelled` : `order ${ev.order_id}: ` + ["qty", "committed_week", "latest_week"].filter((k) => ev[k] != null).map((k) => `${k}=${ev[k]}`).join(", ");
    default: return ev.type;
  }
}

function proposalsView(list, ctx, canWrite) {
  if (!list.length) return h("div", { class: "card empty" }, "No pending proposals.");
  return canWrite ? proposalCards(list, ctx) : h("div", null, list.map((p) => h("div", { class: "card" }, h("b", null, summarise(p.event)))));
}

const EVENT_TYPES = {
  capacity_change: { label: "Capacity change", fields: [["resource_id", "Resource", "res"], ["from_week", "From week", "date"], ["to_week", "To week", "date"], ["mode", "Mode", ["reduce_pct", "set", "add"]], ["value", "Value", "int"]] },
  lot_status: { label: "Lot status", fields: [["lot_id", "Lot", "lot"], ["status", "Status", ["hold", "quarantine", "available", "reserved", "in_transit"]], ["release_week", "Release week (optional)", "date?"]] },
  commitment_change: { label: "Supplier commitment change", fields: [["commitment_id", "Commitment", "com"], ["qty", "New quantity (optional)", "int?"], ["promised_week", "New promised week (optional)", "date?"], ["status", "Status (optional)", ["", "confirmed", "acknowledged", "unconfirmed", "delayed", "cancelled"]]] },
  yield_change: { label: "Yield change", fields: [["route_id", "Route", "route"], ["base", "Base yield (permille, e.g. 880)", "int"], ["low", "Low (optional)", "int?"], ["high", "High (optional)", "int?"]] },
  logistics_delay: { label: "Logistics delay", fields: [["destination", "Destination lane", "lane"], ["delta_weeks", "Change in transit weeks", "int"]] },
  demand_change: { label: "Demand change", fields: [["order_id", "Order", "order"], ["qty", "New quantity (optional)", "int?"], ["committed_week", "New committed week (optional)", "date?"], ["latest_week", "New latest week (optional)", "date?"], ["cancel", "Cancel order", "bool"]] },
  route_qualification: { label: "Route qualification", fields: [["route_id", "Route", "route"], ["qualification", "Qualification", ["qualified", "pending", "disqualified"]], ["qualified_from", "Qualified from (optional)", "date?"]] },
};

function eventView(plan, ctx, canWrite) {
  const lists = { res: plan.resources.map((r) => r.id), lot: plan.inventory.map((l) => l.id), com: plan.commitments.map((c) => c.id), route: plan.routes.map((r) => r.id), lane: plan.lanes.map((l) => l.destination), order: plan.orders.map((o) => o.id) };
  const type = sel(Object.entries(EVENT_TYPES).map(([k, v]) => [k, v.label]), "capacity_change");
  const source = sel(SOURCES, "supplier_portal"), title = h("input", { type: "text", placeholder: "Optional incident title" });
  const fieldsBox = h("div", { class: "form-row" }), inputs = {};
  const draw = () => {
    fieldsBox.replaceChildren(); for (const k of Object.keys(inputs)) delete inputs[k];
    for (const [key, label, kind] of EVENT_TYPES[type.value].fields) {
      let el;
      if (typeof kind === "string" && lists[kind]) el = sel(lists[kind]);
      else if (Array.isArray(kind)) el = sel(kind);
      else if (kind === "bool") el = h("input", { type: "checkbox" });
      else if (kind.startsWith("date")) el = h("input", { type: "date" });
      else el = h("input", { type: "number", step: "1" });
      inputs[key] = { el, kind };
      fieldsBox.append(h("div", null, h("label", null, label), el));
    }
  };
  type.addEventListener("change", draw); draw();
  const submit = async () => {
    const ev = { type: type.value, source: source.value };
    for (const [k, { el, kind }] of Object.entries(inputs)) {
      if (kind === "bool") { ev[k] = el.checked; continue; }
      if (el.value === "") { if (typeof kind === "string" && kind.endsWith("?")) continue; if (Array.isArray(kind)) continue; return toast(`${k} is required`, "err"); }
      ev[k] = (kind === "int" || kind === "int?") ? Number(el.value) : el.value;
    }
    try {
      const r = await post("/v1/events?mode=async", { events: [ev], title: title.value || null });
      if (r.conflicts.length) toast(`${r.conflicts.length} conflict(s) opened: lower-authority source`, "");
      if (!r.changed) return toast("No change: " + (r.ignored[0] || "event had no effect"), "");
      toast("Applied: snapshot v" + r.snapshot_version, "ok");
      ctx.nav(r.incident_id ? `#/incidents/${r.incident_id}` : "#/dashboard");
    } catch (e) { toast(e.message, "err"); }
  };
  return h("div", { class: "card" }, h("h2", null, "Structured event"),
    h("div", { class: "form-row" }, h("div", null, h("label", null, "Event type"), type), h("div", null, h("label", null, "Source (sets precedence)"), source), h("div", null, h("label", null, "Incident title"), title)),
    h("div", { style: "margin-top:6px" }, fieldsBox),
    h("div", { class: "row", style: "margin-top:14px" }, h("button", { class: "btn primary", disabled: !canWrite, onclick: submit }, "Apply and analyse")),
    h("p", { class: "muted small" }, "A less authoritative source can worsen the plan at once but cannot improve it without confirmation: those cases appear under Conflicts."));
}

function conflictsView(list, ctx, canWrite) {
  if (!list.length) return h("div", { class: "card empty" }, "No open conflicts. All facts are reconciled.");
  const byEvent = new Map();
  list.forEach((c) => byEvent.set(c.event_id, [...(byEvent.get(c.event_id) || []), c]));
  const resolve = async (ids, accept) => {
    const note = await askText("Resolve conflict", accept === "proposed" ? "Why is the new value correct?" : "Why keep the current value?", { confirm: "Resolve" }); if (!note) return;
    try { await post("/v1/conflicts/resolve", { accept, ids, note }); toast("Resolved", "ok"); ctx.refresh(); } catch (e) { toast(e.message, "err"); }
  };
  return h("div", null, [...byEvent].map(([ev, cs]) => h("div", { class: "card", style: "margin-bottom:12px" },
    h("div", { class: "row sb" }, h("div", null, h("b", null, `${cs[0].entity_type} ${cs[0].entity_id}${cs.length > 1 ? ` (+${cs.length - 1} more)` : ""}`), h("div", { class: "muted small" }, cs[0].reason)),
      h("div", { class: "row" }, stateBadge(cs[0].applied ? "applied conservatively" : "not applied"), h("span", { class: "mono muted" }, ev))),
    h("div", { class: "small", style: "margin:8px 0" }, `${cs[0].field}: current ${cs[0].current_value} (${cs[0].current_source}) vs proposed ${cs[0].proposed_value} (${cs[0].proposed_source})`),
    canWrite ? h("div", { class: "row" }, h("button", { class: "btn sm", onclick: () => resolve(cs.map((c) => c.id), "proposed") }, "Accept proposed value"), h("button", { class: "btn sm", onclick: () => resolve(cs.map((c) => c.id), "current") }, "Keep current value")) : null)));
}
