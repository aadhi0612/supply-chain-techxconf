import { S, reset, onChange, emit } from "./state.js";
import { h, clear } from "./util.js";

const TABS = [["track", "Track", () => import("./views/track.js")], ["trace", "Trace", () => import("./views/trace.js")], ["disrupt", "Disrupt", () => import("./views/disrupt.js")],
  ["ledger", "Ledger", () => import("./views/ledger.js")], ["built", "How it's built", () => import("./views/built.js")]];
const app = document.getElementById("app");
let page, tick = 0;

const current = () => TABS.find((t) => t[0] === (location.hash.split("/")[1] || "track")) ?? TABS[0];
async function render() {
  const [id, label, load] = current(), mine = ++tick;
  document.querySelectorAll(".xtabs a").forEach((a) => a.classList.toggle("on", a.dataset.id === id));
  document.title = `${label} · SiliconFlow experience`;
  const out = h("div", null);
  try { (await load()).default(out); } catch (e) { out.append(h("div", { class: "note bad" }, "Could not load this view: " + e.message)); }
  if (mine === tick) clear(page).append(out);
}

app.append(h("header", { class: "xhead" }, h("a", { class: "brand", href: "../" }, h("i"), "SiliconFlow"), h("span", { class: "badge warn" }, "simulated · no server"),
    h("nav", { class: "xtabs" }, TABS.map(([id, l]) => h("a", { href: `#/${id}`, "data-id": id }, l))), h("span", { class: "grow" }),
    h("a", { class: "small", href: "../console/" }, "Console"), h("a", { class: "small", href: "https://github.com/aadhi0612/supply-chain-techxconf", target: "_blank", rel: "noopener" }, "GitHub")),
  (page = h("main", { class: "page" })));
reset();
onChange(render);
addEventListener("hashchange", render);
setInterval(() => { if (S.playing) { S.clock = Math.min(16, S.clock + 0.05); if (S.clock >= 16) S.playing = false; emit(); } }, 80);
render();
