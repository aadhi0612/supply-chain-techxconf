// Provenance chatbot widget: one self-mounting module for the landing page and the Experience tab. All dynamic text goes through
// text nodes (no innerHTML). The server redacts by viewer role before the model sees anything; this file only displays the result.
import { h } from "../console/js/util.js";
import { API } from "./config.js";

const VIEWS = {
  oem: ["OEM (brand owner)", "You see your Tier-1 packager by name. Everything below is pseudonymous and vouched for by the packager."],
  tier1: ["Tier-1 supplier (OSAT)", "You see yourself and your Tier-2 suppliers by name. Tier 3 and below are pseudonymous."],
  tier2: ["Tier-2 supplier", "You see your Tier-3 suppliers by name. Tier 4 and below are pseudonymous."],
};
const CHIPS = ["Where is package 1124544 coming from?", "Is package 1124101 safe to ship?", "Which packages use LOT-S-77?", "What does the trace show for ORD-1002?"];
let viewer = "oem";
let busy = false;

const css = document.createElement("link");
css.rel = "stylesheet";
css.href = new URL("chat.css", import.meta.url).href;
document.head.append(css);

const msgs = h("div", { class: "sfc-msgs", "aria-live": "polite" });
const note = h("div", { class: "sfc-note" }, VIEWS[viewer][1]);
const input = h("input", { type: "text", maxlength: 500, placeholder: "Ask about a package id, lot or order…", "aria-label": "Your question", autocomplete: "off" });
const send = h("button", { type: "submit" }, "Ask");
const select = h("select", { "aria-label": "Who is asking", onchange: (e) => { viewer = e.target.value; note.textContent = VIEWS[viewer][1]; add("b", `Now answering as: ${VIEWS[viewer][0]}.`); } },
  Object.entries(VIEWS).map(([k, v]) => h("option", { value: k }, v[0])));
const panel = h("section", { class: "sfc", role: "dialog", "aria-label": "Provenance assistant" },
  h("div", { class: "sfc-head" }, h("div", { class: "sfc-title" }, h("i"), "Provenance assistant", h("span", { class: "grow" }), h("button", { class: "sfc-x", "aria-label": "Close", onclick: () => toggle(false) }, "×")),
    h("div", { class: "sfc-view" }, "Ask as", select), note),
  msgs,
  h("div", { class: "sfc-chips" }, CHIPS.map((c) => h("button", { class: "sfc-chip", onclick: () => ask(c) }, c))),
  h("form", { class: "sfc-form", onsubmit: (e) => { e.preventDefault(); ask(input.value); } }, input, send));
const fab = h("button", { class: "sfc-fab", "aria-label": "Open provenance assistant", onclick: () => toggle(true) },
  h("span", null, "Trace a package"));
fab.prepend(svgIcon());

function svgIcon() {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor"); s.setAttribute("stroke-width", "2");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", "M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"); p.setAttribute("stroke-linejoin", "round");
  s.append(p); return s;
}
function toggle(open) { panel.classList.toggle("open", open); fab.style.display = open ? "none" : ""; if (open) { input.focus(); if (!msgs.children.length) greet(); } }
function greet() { add("b", "I can trace any package back through every supplier tier, or show where a lot went. Pick who you are asking as, then try a question below. Data is synthetic."); }
function add(who, text) { const el = h("div", { class: `sfc-m ${who}` }, who === "u" ? text : h("div", { class: "sfc-txt" }, text)); msgs.append(el); msgs.scrollTop = msgs.scrollHeight; return el; }

function node(n) {
  return h("div", { class: `sfc-node ${n.named ? "named" : "hid"}` }, h("span", null, n.named ? "●" : "🔒"),
    h("div", { class: "l" }, h("b", null, n.supplier), h("small", null, `${n.part} · lot ${n.lot} · ${n.where} · ${n.made}`)));
}
function card(r) {
  if (r.kind !== "package") return null;
  const tiers = {}; r.chain.forEach((n) => (tiers[n.tier] ??= []).push(n));
  const v = r.verdict;
  return h("details", { class: "sfc-card", open: true }, h("summary", null, `Trace · package ${r.package.id} · as ${r.viewer_label}`),
    h("div", { class: "in" }, h("span", { class: `sfc-ver ${v.genuine ? "ok" : "bad"}` }, v.genuine ? "genuine · verified" : "attention"),
      Object.keys(tiers).sort().map((t) => h("div", { class: "sfc-tier" }, h("h5", null, `Tier ${t}`), tiers[t].map(node))),
      r.attestations.map((a) => h("div", { class: `sfc-att ${a.verified ? "" : "bad"}` }, `${a.verified ? "✓" : "✗"} ${a.attester} (Tier ${a.attester_tier}) vouches for ${a.covers_lots} lots from ${a.covers_suppliers} undisclosed suppliers, tiers ${a.tiers[0]}–${a.tiers[1]} · ${a.digest}…`)),
      r.alerts.map((a) => h("div", { class: "sfc-alert" }, "⚠ " + a.text))));
}

async function ask(q) {
  q = (q || "").trim();
  if (!q || busy) return;
  busy = true; send.disabled = true; input.value = "";
  add("u", q);
  const wait = add("b", "");
  wait.firstChild.replaceChildren(h("span", { class: "sfc-dots" }, h("span"), h("span"), h("span")));
  try {
    const res = await fetch(`${API}/chat`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ message: q, viewer }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    wait.replaceChildren(h("div", { class: "sfc-txt" }, data.answer), card(data.result), h("div", { class: "sfc-tag" }, data.mode === "model" ? "AI-narrated · Amazon Bedrock" : data.mode === "guard" ? "Template answer (output guard)" : "Template answer (model unavailable)"));
  } catch (e) {
    wait.replaceChildren(h("div", { class: "sfc-txt" }, e.message === "Failed to fetch" ? "Could not reach the assistant. Try again in a moment." : e.message));
  } finally { busy = false; send.disabled = false; msgs.scrollTop = msgs.scrollHeight; input.focus(); }
}

document.body.append(fab, panel);
addEventListener("keydown", (e) => e.key === "Escape" && toggle(false));
