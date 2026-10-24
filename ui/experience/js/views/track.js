// Trackability: where is every lot and shipment right now, and what does that mean for each customer order.
import { NODES, ORDERS, WEEKS, PLAN_START, shipmentsFor } from "../data.js";
import { S, emit } from "../state.js";
import { h, svg, fmt, badge, bar, legend, kpi, table, weekDate } from "../util.js";

const STAGE_TEXT = { normal: "Pre-incident plan", disrupted: "After the supplier notice, nobody has acted", recovered: "After the approved recovery plan" };
const state = (s, t) => (t < s.dep ? "scheduled" : t >= s.arr ? "delivered" : "in transit");
const lerp = (a, b, p) => a + (b - a) * p;

function map(ships, t) {
  const root = svg("svg", { viewBox: "0 0 1000 470", role: "img", "aria-label": "Supply network with shipments in transit", class: "net" });
  [["MATERIALS", 80], ["PACKAGING", 390], ["TEST", 630], ["CUSTOMERS", 905]].forEach(([l, x]) => root.append(svg("text", { x, y: 14, class: "colhead", "text-anchor": "middle" }, l)));
  const lanes = new Map(ships.map((s) => [`${s.from}>${s.to}`, s]));
  for (const s of lanes.values()) {
    const a = NODES[s.from], b = NODES[s.to];
    root.append(svg("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: "lane" + (s.late ? " late" : "") }));
  }
  const cap = S.stage !== "normal" && t >= 4 && t <= 9;
  for (const [id, n] of Object.entries(NODES)) {
    const g = svg("g", { transform: `translate(${n.x},${n.y})`, class: "node" + (id === "OSAT-A" && S.stage !== "normal" ? " hit" : "") + (n.held ? " held" : "") });
    g.append(svg("rect", { x: -62, y: -20, width: 124, height: 40, rx: 8 }), svg("text", { y: -2, "text-anchor": "middle", class: "nl" }, n.label), svg("text", { y: 12, "text-anchor": "middle", class: "ns" }, n.sub));
    if (id === "OSAT-A" && S.stage !== "normal") g.append(svg("text", { y: 34, "text-anchor": "middle", class: "flag" }, cap ? "PKG-A -25 % (wk 4-9)" : "PKG-A cut: wk 4-9"));
    if (id === "OSAT-B" && S.stage === "recovered") g.append(svg("text", { y: 34, "text-anchor": "middle", class: "good" }, "+300/wk spot block"));
    if (n.held) g.append(svg("text", { y: 34, "text-anchor": "middle", class: "flag" }, `compliance: ${n.held}`));
    root.append(g);
  }
  for (const s of ships) {
    if (state(s, t) !== "in transit") continue;
    const a = NODES[s.from], b = NODES[s.to], p = (t - s.dep) / (s.arr - s.dep);
    const held = s.holdable && S.stage !== "normal";
    root.append(svg("g", { transform: `translate(${lerp(a.x, b.x, p)},${lerp(a.y, b.y, p)})`, class: "dot" + (held ? " held" : "") + (s.late ? " late" : "") },
      svg("circle", { r: 7 }), svg("text", { y: -11, "text-anchor": "middle" }, `${s.id}${held ? " · HOLD" : ""}`)));
  }
  return root;
}

export default function track(root) {
  const stage = S.stage, ships = shipmentsFor(stage), t = S.clock;
  const out = ORDERS.filter((o) => !o.gate).map((o) => o.out[stage]);
  const ot = out.reduce((a, x) => a + x[0], 0), late = out.reduce((a, x) => a + x[1], 0), un = out.reduce((a, x) => a + x[2], 0);
  const slider = h("input", { type: "range", min: 0, max: WEEKS, step: 0.1, value: t, "aria-label": "Simulated time in weeks", oninput: (e) => { S.clock = +e.target.value; S.playing = false; emit(); } });
  const play = h("button", { class: "btn", onclick: () => { S.playing = !S.playing; if (S.playing && S.clock >= WEEKS) S.clock = 0; emit(); } }, S.playing ? "⏸ Pause" : "▶ Play");
  const shown = ships.filter((s) => s.kind.startsWith("finished") || (t >= s.dep - 1.5 && t <= s.arr + 1.5) || (S.stage !== "normal" && s.lever));

  root.append(
    h("div", { class: "page-head" }, h("div", null, h("h1", null, "Track"), h("div", { class: "sub" },
      "Trackability: every logic-die, HBM, substrate and module lot as a shipment between sites, on a simulated clock. Scrub the clock, or run the incident in the ",
      h("a", { href: "#/disrupt" }, "Disrupt"), " tab and come back to see the network change."))),
    h("div", { class: "grid g4" },
      kpi("Plan state", badge(stage === "normal" ? "on plan" : stage === "disrupted" ? "disrupted" : "recovering", stage === "normal" ? "ok" : stage === "disrupted" ? "bad" : "warn"), STAGE_TEXT[stage]),
      kpi("Delivered on time", fmt.int(ot), "of 10,000 plannable units (2,700 held by compliance)"),
      kpi("Late / unfulfilled", `${fmt.int(late)} / ${fmt.int(un)}`, "plannable orders only"),
      kpi("Simulated date", weekDate(PLAN_START, Math.floor(t)), `week ${t.toFixed(1)} of ${WEEKS}`)),
    h("div", { class: "card", style: "margin-top:16px" }, h("div", { class: "row", style: "margin-bottom:8px" }, play, h("div", { class: "grow" }, slider), h("span", { class: "mono small" }, `wk ${t.toFixed(1)}`)), map(ships, t),
      h("div", { class: "row small muted", style: "margin-top:6px" }, h("span", null, "● dot = lot in transit"), h("span", null, "red = quality hold / late"), h("span", null, "lines = lanes in the plan"))),
    h("div", { class: "grid g2", style: "margin-top:16px" },
      h("div", { class: "card" }, h("h2", null, "Orders ", h("span", { class: "grow" }), legend()),
        table([{ label: "Order", render: (o) => h("div", null, h("b", null, o.id), h("div", { class: "muted small" }, `${o.cust} · ${o.dest}`)) },
          { label: "Qty", num: true, render: (o) => fmt.int(o.qty) },
          { label: "Due", render: (o) => `wk ${o.due}` },
          { label: "Outcome", render: (o) => o.gate ? h("div", null, badge(o.gate, "bad"), h("div", { class: "muted small" }, o.why)) : h("div", null, bar(...o.out[stage]), h("div", { class: "muted small" }, `${fmt.int(o.out[stage][0])} on time` + (o.out[stage][1] ? ` · ${o.out[stage][1]} late` : "") + (o.out[stage][2] ? ` · ${fmt.int(o.out[stage][2])} unfulfilled` : ""))) }], ORDERS)),
      h("div", { class: "card" }, h("h2", null, "Shipments & lots"),
        table([{ label: "Id", render: (s) => h("span", { class: "mono" }, s.id) }, { label: "Lot", render: (s) => h("span", { class: "mono" }, s.lot) },
          { label: "Leg", render: (s) => `${NODES[s.from].label} → ${NODES[s.to].label}` }, { label: "Qty", num: true, render: (s) => fmt.int(s.qty) },
          { label: "Status", render: (s) => { const st = state(s, t), hold = s.holdable && S.stage !== "normal";
            return h("div", null, hold ? badge("quality hold", "bad") : badge(st, { delivered: "ok", "in transit": "info", scheduled: "" }[st]), s.late ? badge("late", "warn") : null,
              s.risk ? h("div", { class: "small", style: "color:var(--bad)" }, `${s.risk} units at risk`) : null, s.note ? h("div", { class: "muted small" }, s.note) : null); } }], shown),
        h("div", { class: "muted small", style: "margin-top:8px" }, "Shows shipments near the clock, every customer delivery and any lever-driven shipment."))));
}
