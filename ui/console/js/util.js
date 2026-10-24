// Tiny DOM + formatting helpers. All dynamic text goes through textContent / createTextNode: no innerHTML anywhere.
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked" || k === "disabled" || k === "selected" || k === "open") el[k] = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, kids);
  return el;
}
export function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}
export const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export const fmt = {
  int: (n) => (n == null ? "-" : Math.round(n).toLocaleString("en-US")),
  usd: (n) => {
    if (n == null) return "-";
    const a = Math.abs(n), s = n < 0 ? "-" : "";
    if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(1)}M`;
    if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(0)}k`;
    return `${s}$${a.toFixed(0)}`;
  },
  pct: (n, d = 1) => (n == null ? "-" : `${Number(n).toFixed(d)}%`),
  date: (iso) => (iso ? new Date(iso + (iso.length === 10 ? "T00:00:00Z" : "")).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit", timeZone: "UTC" }) : "-"),
  dt: (iso) => (iso ? new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "-"),
  hours: (x) => (x == null ? "-" : x < 1 ? `${Math.round(x * 60)} min` : `${x.toFixed(1)} h`),
};
// week index (relative to plan_start, an ISO date) -> "26 Oct"
export function weekDate(planStart, w) {
  if (w == null) return "-";
  const d = new Date(planStart + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + 7 * w);
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" });
}
export function badge(text, kind = "") { return h("span", { class: `badge ${kind}` }, String(text).replaceAll("_", " ")); }
const STATE_KIND = { options_ready: "info", pending_approval: "warn", approved: "ok", executing: "info", completed: "ok", rejected: "bad", stale: "bad",
  analysis_failed: "bad", analyzing: "info", no_impact: "", superseded: "", on_time: "ok", late: "warn", partial: "warn", unfulfilled: "bad", blocked: "bad",
  allow: "ok", needs_license: "bad", review: "warn", confirmed: "ok", sent: "info", drafted: "warn", failed: "bad", declined: "bad", reverted: "", open: "warn", resolved: "ok", pending: "warn" };
export const stateBadge = (s) => badge(s, STATE_KIND[s] ?? "");

export function bar(onTime, late, unful) {
  const t = Math.max(1, onTime + late + unful);
  const seg = (c, v) => h("i", { class: c, style: `width:${(100 * v) / t}%`, title: `${c === "ot" ? "on time" : c === "lt" ? "late" : "unfulfilled"}: ${fmt.int(v)}` });
  return h("div", { class: "bar" }, seg("ot", onTime), seg("lt", late), seg("un", unful));
}
export const legend = () => h("div", { class: "legend" },
  h("span", { style: "--c:var(--ok)" }, "on time"), h("span", { style: "--c:#d9a03a" }, "late"), h("span", { style: "--c:var(--bad)" }, "unfulfilled"));

export function toast(msg, kind = "") {
  const t = h("div", { class: `toast ${kind}` }, msg);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), kind === "err" ? 7000 : 3500);
}
export function modal({ title, body, confirm = "Confirm", cancel = "Cancel", danger = false }) {
  return new Promise((resolve) => {
    const done = (v) => { bg.remove(); resolve(v); };
    const bg = h("div", { class: "modal-bg", onclick: (e) => e.target === bg && done(null) },
      h("div", { class: "modal", role: "dialog", "aria-modal": "true" }, h("h2", null, title), body,
        h("div", { class: "actions" }, h("button", { class: "btn", onclick: () => done(null) }, cancel),
          h("button", { class: `btn ${danger ? "danger" : "primary"}`, onclick: () => done(true) }, confirm))));
    document.body.append(bg);
    const first = bg.querySelector("input, textarea, select"); if (first) first.focus();
  });
}
export async function askText(title, label, { required = true, confirm = "Submit", danger = false, placeholder = "" } = {}) {
  const ta = h("textarea", { style: "min-height:80px;font-family:var(--ui);font-size:14px", placeholder });
  const ok = await modal({ title, body: h("div", null, h("label", null, label), ta), confirm, danger });
  if (!ok) return null;
  if (required && !ta.value.trim()) { toast("A reason is required", "err"); return null; }
  return ta.value.trim();
}
export function table(cols, rows, { empty = "Nothing here yet.", onRow } = {}) {
  if (!rows.length) return h("div", { class: "empty" }, empty);
  return h("div", { class: "tablewrap" }, h("table", null,
    h("thead", null, h("tr", null, cols.map((c) => h("th", { class: c.num ? "num" : "" }, c.label)))),
    h("tbody", null, rows.map((r) => h("tr", { class: onRow ? "click" : "", onclick: onRow ? () => onRow(r) : null },
      cols.map((c) => h("td", { class: (c.num ? "num " : "") + (c.cls || "") }, c.render ? c.render(r) : r[c.key])))))));
}
export const spinner = (text) => h("div", { class: "row muted" }, h("span", { class: "spinner" }), text);
export function kpi(label, value, sub) { return h("div", { class: "card kpi" }, h("div", { class: "l" }, label), h("div", { class: "v" }, value), sub ? h("div", { class: "s" }, sub) : null); }
export function tabs(items, initial, onChange) {
  const wrap = h("div", { class: "tabs" });
  const set = (id) => { $$("button", wrap).forEach((b) => b.classList.toggle("on", b.dataset.id === id)); onChange(id); };
  items.forEach(([id, label]) => wrap.append(h("button", { "data-id": id, onclick: () => set(id) }, label)));
  queueMicrotask(() => set(initial));
  return wrap;
}
export const jsonPre = (o) => h("pre", { class: "pay" }, JSON.stringify(o, null, 2));
