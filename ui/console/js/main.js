import { get, session, setUnauthorizedHandler } from "./api.js";
import { clear, h, toast } from "./util.js";

const ROUTES = [
  ["dashboard", "Dashboard", "◆", () => import("./views/dashboard.js")],
  ["plan", "Plan & orders", "▤", () => import("./views/plan.js")],
  ["signals", "Signals", "⚡", () => import("./views/signals.js")],
  ["incidents", "Incidents", "▲", () => import("./views/incidents.js")],
  ["dataset", "Dataset", "▦", () => import("./views/dataset.js")],
  ["governance", "Audit & learning", "✓", () => import("./views/governance.js")],
];
const app = document.getElementById("app");
const state = { me: null, demoUsers: [], cleanups: [], token: 0 };

setUnauthorizedHandler(() => { session.key = null; state.me = null; renderLogin("Your session is not valid. Sign in again."); });

function parseHash() {
  const [, route = "dashboard", ...rest] = (location.hash || "#/dashboard").split("?")[0].split("/");
  return { route: route || "dashboard", params: rest.map(decodeURIComponent) };
}
export const nav = (hash) => { location.hash = hash; };

async function boot() {
  try { state.demoUsers = await get("/v1/demo/users", { quiet: true }); } catch { state.demoUsers = []; }
  if (!session.key) return renderLogin();
  try { state.me = await get("/v1/me", { quiet: true }); } catch { session.key = null; return renderLogin(); }
  renderShell();
  route();
}

function renderLogin(msg) {
  document.title = "SiliconFlow Console - sign in";
  const input = h("input", { type: "text", placeholder: "sf_...", autocomplete: "off", spellcheck: "false" });
  const go = async (key) => {
    session.key = key.trim();
    try { state.me = await get("/v1/me", { quiet: true }); renderShell(); route(); }
    catch (e) { session.key = null; toast(e.status === 401 ? "That API key is not valid" : e.message, "err"); }
  };
  clear(app).append(h("div", { class: "login" }, h("div", { class: "box card" },
    h("h1", null, "SiliconFlow"),
    h("p", { class: "muted" }, "Constraint-aware supply allocation and recovery. Sign in with an API key issued by ", h("code", null, "siliconflow init-user"), "."),
    msg ? h("div", { class: "note warn" }, msg) : null,
    h("label", null, "API key"), input,
    h("div", { class: "row", style: "margin-top:14px" }, h("button", { class: "btn primary", onclick: () => go(input.value) }, "Sign in"),
      h("a", { href: "../" }, "← About SiliconFlow")),
    state.demoUsers.length ? h("div", { style: "margin-top:22px" },
      h("div", { class: "note warn" }, "Demo mode is on: these fixed, public keys exist for local development only."),
      h("div", { class: "demo-users" }, state.demoUsers.map((u) => h("button", { class: "btn", onclick: () => go(u.api_key) }, `${u.name} · ${u.roles.join(", ")}`)))) : null)));
  input.addEventListener("keydown", (e) => e.key === "Enter" && go(input.value));
}

function renderShell() {
  const side = h("aside", { class: "side" }, h("div", { class: "brand" }, h("i"), "SiliconFlow"),
    h("nav", { class: "nav", id: "nav" }, ROUTES.map(([id, label, icon]) => h("a", { href: `#/${id}`, "data-id": id }, h("span", null, icon), label, h("span", { class: "count", id: `c-${id}`, hidden: true })))),
    h("div", { class: "spacer" }),
    h("div", { class: "foot" }, h("a", { href: "../" }, "About"), " · ", h("a", { href: "/docs", target: "_blank", rel: "noopener" }, "API docs")));
  const roles = state.me.roles.map((r) => h("span", { class: "badge info" }, r));
  const who = state.demoUsers.length
    ? h("select", { "aria-label": "Switch demo user", onchange: (e) => { session.key = e.target.value; boot(); } },
        state.demoUsers.map((u) => h("option", { value: u.api_key, selected: u.api_key === session.key }, `${u.name} (${u.roles.join(", ")})`)))
    : null;
  const top = h("div", { class: "top" }, h("div", { class: "grow", id: "crumb" }),
    h("span", { class: `badge ${{ shadow: "", draft: "warn", execute: "ok" }[state.me.mode] ?? ""}`, title: "Execution mode: shadow = recommend only, draft = humans send, execute = adapters send" }, `mode: ${state.me.mode}`),
    h("div", { class: "who" }, who, who ? null : h("span", { class: "muted small" }, state.me.tenant), roles,
      h("button", { class: "btn sm", onclick: () => { session.key = null; state.me = null; renderLogin(); } }, "Sign out")));
  clear(app).append(h("div", { class: "shell" }, side, h("div", { class: "main" }, top, h("main", { class: "page", id: "page" }))));
}

async function refreshBadges() {
  try {
    const incs = await get("/v1/incidents", { quiet: true });
    const act = incs.filter((i) => ["options_ready", "pending_approval", "approved", "stale", "analyzing"].includes(i.state)).length;
    const el = document.getElementById("c-incidents");
    if (el) { el.textContent = act; el.hidden = !act; }
    const props = await get("/v1/proposals?status=pending", { quiet: true });
    const cf = await get("/v1/conflicts", { quiet: true });
    const s = document.getElementById("c-signals");
    if (s) { s.textContent = props.length + cf.length; s.hidden = !(props.length + cf.length); }
  } catch { /* badges are best-effort */ }
}

async function route() {
  if (!state.me) return;
  const { route: id, params } = parseHash();
  const entry = ROUTES.find((r) => r[0] === id) ?? ROUTES[0];
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.id === entry[0]));
  state.cleanups.splice(0).forEach((f) => { try { f(); } catch { /* ignore */ } });
  const mine = ++state.token;
  const page = document.getElementById("page");
  if (!page) return;
  clear(page).append(h("div", { class: "empty" }, h("span", { class: "spinner" }), " Loading…"));
  document.getElementById("crumb").textContent = entry[1];
  document.title = `${entry[1]} · SiliconFlow`;
  try {
    const mod = await entry[3]();
    const out = h("div", null);
    const ctx = { me: state.me, params, nav, onCleanup: (f) => state.cleanups.push(f), refresh: () => route(), badges: refreshBadges, isCurrent: () => mine === state.token };
    await mod.default(out, ctx);
    if (mine !== state.token) return;
    clear(page).append(out);
  } catch (e) {
    if (mine !== state.token) return;
    clear(page).append(h("div", { class: "note bad" }, h("b", null, "Could not load this view. "), e.message));
  }
  refreshBadges();
}

addEventListener("hashchange", route);
boot();
