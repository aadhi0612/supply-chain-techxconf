import { get, post } from "../api.js";
import { badge, fmt, h, jsonPre, kpi, modal, table, tabs, toast } from "../util.js";

const short = (x, n = 10) => (x ? String(x).slice(0, n) : "-");
const CORRECTABLE = { commitment_change: ["qty", "number"], demand_change: ["qty", "number"], capacity_change: ["value", "number"], yield_change: ["base", "number"], lot_status: ["status", "text"] };
const val = (v) => (v && typeof v === "object" ? JSON.stringify(v) : String(v ?? "-"));

export default async function (root, ctx) {
  const canWrite = ctx.me.roles.some((r) => r === "planner" || r === "admin");
  const demo = await get("/healthz", { quiet: true }).then((x) => !!x.demo).catch(() => false);
  root.append(h("div", { class: "page-head" }, h("div", { class: "grow" }, h("h1", null, "Traceability"),
    h("div", { class: "sub" }, "Who changed a fact, when, from which source, who approved the plan built on it, and whether that history still matches what was published to the ledger. The ledger holds only digests, never your data."))));
  const body = h("div", { class: "card" });
  root.append(tabs([["history", "Record history"], ["anchor", "Anchoring & proof"], ...(demo ? [["lab", "Tamper lab (demo)"]] : [])], "history", async (t) => {
    body.replaceChildren(h("div", { class: "empty" }, "…"));
    try {
      body.replaceChildren(...await (t === "history" ? history(ctx, canWrite) : t === "anchor" ? anchoring(ctx, canWrite) : lab(ctx, canWrite)));
    } catch (e) { body.replaceChildren(h("div", { class: "note bad" }, e.message)); }
  }), body);
}

// ---------------------------------------------------------------------------------------------- record history
async function history(ctx, canWrite) {
  const ents = await get("/v1/trace");
  const kinds = Object.keys(ents).filter((k) => ents[k].length);
  const kind = h("select", { id: "tr-kind" }, kinds.map((k) => h("option", { value: k }, k)));
  const id = h("select", { id: "tr-id" });
  const out = h("div", { style: "margin-top:14px" });
  const fill = () => id.replaceChildren(...ents[kind.value].map((x) => h("option", { value: x }, x)));
  const load = async () => {
    out.replaceChildren(h("div", { class: "empty" }, "Loading…"));
    try { out.replaceChildren(render(await get(`/v1/trace/${kind.value}/${encodeURIComponent(id.value)}`))); } catch (e) { out.replaceChildren(h("div", { class: "note bad" }, e.message)); }
  };
  const render = (t) => {
    const evs = t.history;
    return h("div", null,
      h("div", { class: "grid g3" }, kpi("Record", `${t.kind} ${t.id}`, "current values below"), kpi("Changes since import", evs.length, evs.filter((e) => e.superseded_by).length + " later corrected"),
        kpi("Anchored through audit #", t.anchored_through || "none", "entries up to here are in the ledger")),
      h("h3", { style: "margin:16px 0 6px" }, "Current value"), jsonPre(t.current),
      t.baseline ? h("div", { class: "note", style: "margin-top:12px" }, h("b", null, "Baseline. "), `Imported by ${t.baseline.imported_by} on ${fmt.dt(t.baseline.at)} (${t.baseline.reason}). Source: ${t.baseline.provenance?.source ?? "-"}. `,
        h("span", { class: "mono small" }, `audit #${t.baseline.audit_seq ?? "-"} `), t.baseline.anchored ? badge("anchored", "ok") : badge("not anchored", "warn")) : null,
      h("h3", { style: "margin:16px 0 6px" }, "History"),
      table([
        { label: "When", render: (e) => fmt.dt(e.applied_at) }, { label: "Who", render: (e) => h("b", null, e.actor_name) },
        { label: "Source", render: (e) => `${e.source} · ${Math.round((e.confidence ?? 1) * 100)}%${e.forced ? " · human override" : ""}` },
        { label: "What changed", render: (e) => h("div", null, e.changes.slice(0, 4).map((c) => h("div", { class: "mono small" }, `${c.path}: ${val(c.old)} → ${val(c.new)}`)), e.note ? h("div", { class: "muted small" }, `“${e.note}”`) : null) },
        { label: "Status", render: (e) => h("div", null, e.superseded_by ? badge("corrected later", "warn") : badge("current", "ok"), e.supersedes ? h("div", { class: "small muted" }, "corrects ", h("span", { class: "mono" }, short(e.supersedes, 14))) : null, e.superseded_by ? h("div", { class: "small muted" }, "by ", h("span", { class: "mono" }, short(e.superseded_by, 14))) : null) },
        { label: "Audit entry", render: (e) => h("div", null, h("span", { class: "mono" }, `#${e.audit_seq ?? "-"} ${short(e.audit_hash, 8)}`), h("div", null, e.anchored ? badge("anchored", "ok") : badge("not anchored", "warn"))) },
        { label: "Plan approved by", render: (e) => e.incident ? h("div", null, h("a", { href: `#/incidents/${e.incident.id}` }, e.incident.state.replaceAll("_", " ")), e.incident.approvals.map((a) => h("div", { class: "small" }, `${a.decision === "approve" ? "✓" : "✗"} ${a.by} (${a.role}) ${fmt.dt(a.at)}`)), e.incident.plan_hash ? h("div", { class: "mono small muted" }, `plan ${short(e.incident.plan_hash, 8)}`) : null) : h("span", { class: "muted small" }, "no incident") },
        { label: "", render: (e) => canWrite && !e.superseded_by && CORRECTABLE[e.type] ? h("button", { class: "btn sm", onclick: () => correct(e, t, ctx, load) }, "Mark wrong & correct") : "" },
      ], evs.slice().reverse(), { empty: "No change events for this record since import. Report one from the Signals tab, then come back." }));
  };
  kind.addEventListener("change", () => { fill(); load(); }); id.addEventListener("change", load);
  fill();
  queueMicrotask(load);
  return [h("div", { class: "form-row", style: "max-width:520px" }, h("div", null, h("label", { for: "tr-kind" }, "Kind"), kind), h("div", null, h("label", { for: "tr-id" }, "Record"), id)), out];
}

async function correct(e, t, ctx, reload) {
  const [field, type] = CORRECTABLE[e.type];
  const was = e.event[field];
  const input = h("input", { id: "corr-val", type, value: was ?? "" });
  const why = h("textarea", { id: "corr-why", style: "min-height:70px;font-family:var(--ui);font-size:14px", placeholder: "Why was the earlier value wrong?" });
  const rerun = h("input", { id: "corr-run", type: "checkbox" });
  const ok = await modal({ title: `Correct ${t.kind} ${t.id}`, confirm: "Record correction", body: h("div", null,
    h("div", { class: "note warn" }, `This does not delete anything. It adds a new entry that points back to ${short(e.event_id, 14)} and keeps both in the history.`),
    h("label", { for: "corr-val", style: "margin-top:10px" }, `Correct ${field} (was ${val(was)})`), input,
    h("label", { for: "corr-why", style: "margin-top:10px" }, "Reason (required)"), why,
    h("label", { class: "row", for: "corr-run", style: "margin-top:10px" }, rerun, "Also re-run the recovery analysis (opens an incident)")) });
  if (!ok) return;
  if (!why.value.trim()) return toast("A reason is required", "err");
  const ev = { ...e.event, supersedes: e.event_id, note: why.value.trim(), force: true, source: "manual", method: "correction", observed_at: new Date().toISOString() };
  delete ev.id;
  ev[field] = type === "number" ? Number(input.value) : input.value;
  try { await post("/v1/events", { events: [ev], create_incident: rerun.checked, title: `Correction of ${t.kind} ${t.id}` }); toast("Correction recorded", "ok"); reload(); ctx.badges(); }
  catch (err) { toast(err.message, "err"); }
}

// ---------------------------------------------------------------------------------------------- anchoring & proof
async function anchoring(ctx, canWrite) {
  const [v, anchors, entries] = await Promise.all([get("/v1/audit/verify"), get("/v1/audit/anchors"), get("/v1/audit?limit=40")]);
  const A = v.anchors, panel = h("div", { style: "margin-top:16px" });
  const refresh = () => ctx.refresh();
  const run = async (fn, msg) => { try { const r = await fn(); toast(msg(r), "ok"); refresh(); } catch (e) { toast(e.message, "err"); } };
  const proofFor = async (seq) => {
    panel.replaceChildren(h("div", { class: "empty" }, "Checking…"));
    try {
      const p = await get(`/v1/audit/proof/${seq}`);
      panel.replaceChildren(h("div", { class: "card" }, h("h2", null, `Entry #${seq}`),
        p.anchored ? h("div", { class: `note ${p.valid ? "ok" : "bad"}` }, h("b", null, p.valid ? "Proof valid. " : "Proof FAILED. "), p.reason) : h("div", { class: "note warn" }, p.reason),
        h("div", { class: "grid g2", style: "margin-top:12px" },
          h("div", null, h("h3", null, "Entry as stored now"), jsonPre({ ...p.entry, data: p.entry.data })),
          p.anchored ? h("div", null, h("h3", null, "Check against the ledger"), jsonPre({ recomputed_from_content: p.recomputed_leaf, stored_hash: p.entry.stored_hash, merkle_path_steps: (p.path || []).length,
            root_recomputed_from_rows: p.computed_root, root_on_ledger: p.ledger_root, ledger_time: p.ledger_ts, ledger_block: p.anchor?.ledger_index })) : null)));
    } catch (e) { panel.replaceChildren(h("div", { class: "note bad" }, e.message)); }
  };
  const seqIn = h("input", { id: "pf-seq", type: "number", min: 1, placeholder: "audit #" });
  return [
    h("div", { class: "grid g4" },
      kpi("Audit chain", v.chain_ok ? badge("intact", "ok") : badge(`broken at #${v.broken_at}`, "bad"), `${v.entries} entries`),
      kpi("Ledger match", A.failures.length ? badge("MISMATCH", "bad") : A.count ? badge("matches", "ok") : badge("nothing anchored", "warn"), `${A.verified}/${A.count} anchors verified`),
      kpi("Anchored through", A.anchored_through ? `#${A.anchored_through}` : "none", A.unanchored_entries ? `${A.unanchored_entries} newer entries not anchored (since #${A.unanchored_since})` : "everything is anchored"),
      kpi("Ledger", v.ledger.reachable ? badge("reachable", "ok") : badge("unreachable", "warn"), v.ledger.name + (v.ledger.blocks != null ? ` · ${v.ledger.blocks} blocks` : ""))),
    A.failures.length ? h("div", { class: "note bad", style: "margin-top:14px" }, h("b", null, "History no longer matches. "), A.failures.map((f) => h("div", null, f.why))) : null,
    !v.chain_ok ? h("div", { class: "note bad", style: "margin-top:14px" }, h("b", null, `The audit chain breaks at entry #${v.broken_at}. `), v.detail ?? "That entry or an earlier one was edited after it was written.") : null,
    h("div", { class: "row", style: "margin:16px 0" },
      h("button", { class: "btn primary", disabled: !canWrite, title: canWrite ? "" : "Needs the planner or admin role", onclick: () => run(() => post("/v1/audit/anchor"), (r) => (r.anchored ? `Anchored entries #${r.seq_from}-#${r.seq_to} as ledger block ${r.ledger_index}` : r.reason)) }, "Anchor audit log now"),
      h("button", { class: "btn", onclick: refresh }, "Re-verify"),
      h("span", { class: "grow" }), h("label", { for: "pf-seq", class: "small muted" }, "Check one entry"), seqIn, h("button", { class: "btn", onclick: () => seqIn.value && proofFor(seqIn.value) }, "Prove")),
    h("h3", { style: "margin-bottom:6px" }, "Anchors"),
    table([{ label: "#", key: "id" }, { label: "Audit entries", render: (a) => `#${a.seq_from}–#${a.seq_to} (${a.entries})` }, { label: "Merkle root", render: (a) => h("span", { class: "mono" }, short(a.root, 16)) },
      { label: "Ledger block", render: (a) => `${a.ledger} #${a.ledger_index}` }, { label: "Ledger time", render: (a) => fmt.dt(a.ledger_ts) }, { label: "By", render: (a) => h("span", { class: "mono small" }, a.created_by) }],
      anchors, { empty: "Nothing anchored yet. Press “Anchor audit log now”." }),
    h("h3", { style: "margin:16px 0 6px" }, "Latest audit entries (click one for its proof)"),
    table([{ label: "#", key: "seq" }, { label: "When", render: (r) => fmt.dt(r.ts) }, { label: "Who", render: (r) => h("span", { class: "mono" }, r.actor) }, { label: "Action", render: (r) => h("b", null, r.action) },
      { label: "Entity", render: (r) => h("span", { class: "mono small" }, `${r.entity_type}:${r.entity_id}`) }, { label: "State", render: (r) => (r.seq <= A.anchored_through ? badge("anchored", "ok") : badge("not anchored", "warn")) }],
      entries, { onRow: (r) => proofFor(r.seq) }), panel];
}

// ---------------------------------------------------------------------------------------------- tamper lab (demo mode only)
async function lab(ctx, canWrite) {
  const v = await get("/v1/audit/verify");
  const seq = h("input", { id: "lab-seq", type: "number", min: 1, value: 2, style: "max-width:120px" });
  const out = h("div", { style: "margin-top:14px" });
  const show = async (title) => {
    const r = await get("/v1/audit/verify");
    out.replaceChildren(h("div", { class: `note ${r.ok ? "ok" : "bad"}` }, h("b", null, `${title}: `),
      r.ok ? "verification passes." : r.chain_ok ? "the audit chain looks consistent on its own, but it no longer matches the root on the ledger." : `the audit chain breaks at entry #${r.broken_at}.`,
      r.anchors.failures.map((f) => h("div", null, f.why)), !r.anchors.ledger_reachable ? h("div", null, `Ledger unreachable: ${r.anchors.unanchored_entries} entries are unanchored since #${r.anchors.unanchored_since ?? "-"}.`) : null));
  };
  const act = (title, fn) => async () => { try { await fn(); await show(title); } catch (e) { toast(e.message, "err"); } };
  const btn = (label, cls, fn, tip) => h("button", { class: `btn ${cls}`, disabled: !canWrite, title: canWrite ? tip : "Needs the planner or admin role", onclick: fn }, label);
  return [
    h("div", { class: "note warn" }, "Demo mode only. These controls change stored audit rows on purpose so you can watch detection work. Anchor first (Anchoring & proof tab), then tamper."),
    h("div", { class: "row", style: "margin:14px 0" }, h("label", { for: "lab-seq" }, "Audit entry #"), seq),
    h("div", { class: "grid g3" },
      h("div", { class: "card" }, h("h2", null, "1 · Edit one entry"), h("p", { class: "muted small" }, "Changes who did it on a single entry. The chain breaks at exactly that entry."),
        btn("Edit entry", "danger", act("Edited", () => post("/v1/demo/tamper", { seq: Number(seq.value), rebuild: false })))),
      h("div", { class: "card" }, h("h2", null, "2 · Rewrite history"), h("p", { class: "muted small" }, "An insider edits the entry and recomputes every later hash. The platform's own check passes; the ledger does not."),
        btn("Rewrite history", "danger", act("Rewritten", () => post("/v1/demo/tamper", { seq: Number(seq.value), rebuild: true })))),
      h("div", { class: "card" }, h("h2", null, "3 · Undo"), h("p", { class: "muted small" }, "Restore the original rows (not possible if new entries were written after a rewrite)."),
        btn("Restore original", "", act("Restored", () => post("/v1/demo/untamper"))))),
    h("div", { class: "card", style: "margin-top:16px" }, h("h2", null, "Ledger outage"), h("p", { class: "muted small" }, "Take the ledger offline. Work continues; anchoring fails with a clear message; verification reports which entries are unanchored."),
      h("div", { class: "row" }, btn("Take ledger offline", "", act("Ledger offline", () => post("/v1/demo/ledger", { available: false }))), btn("Bring ledger back", "", act("Ledger online", () => post("/v1/demo/ledger", { available: true }))))),
    out, v.ok ? null : h("div", { class: "note bad", style: "margin-top:14px" }, "Verification currently fails; see the Anchoring & proof tab.")];
}
