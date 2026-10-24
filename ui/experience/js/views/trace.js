// Traceability: backward genealogy for one module, forward "where did this lot go" for one lot.
import { buildUnits, LOT_INFO } from "../data.js";
import { S, append } from "../state.js";
import { sha256 } from "../sha.js";
import { h, clear, badge, table, kpi, fmt, short } from "../util.js";

const UNITS = buildUnits();
const CUST = { "ORD-1001": "Hyperion Cloud", "ORD-1002": "Nimbus AI", "ORD-1003": "Orion Systems", "ORD-1004": "Vega Labs" };
let query = "", rerun = () => {};
const held = (lot) => lot === "LOT-S-77" && S.stage !== "normal";
const att = (u, k, v) => short(sha256(`${u.serial}|${k}|${v}`));
const status = (u) => (held(u.sub) ? badge("quarantine", "bad") : badge("released", "ok"));

function node(title, id, sub, hash, kind = "") {
  return h("li", null, h("div", { class: `tn ${kind}` }, h("div", { class: "tt" }, title), h("div", { class: "tid mono" }, id), sub ? h("div", { class: "muted small" }, sub) : null, h("div", { class: "mono small att" }, `attestation ${hash}`)));
}
function genealogy(u) {
  const sh = held(u.sub);
  return h("ul", { class: "tree" },
    h("li", null, h("div", { class: "tn root" + (sh ? " bad" : "") }, h("div", { class: "tt" }, "Module"), h("div", { class: "tid mono" }, u.serial),
      h("div", { class: "muted small" }, `${u.order} · ${CUST[u.order]}`), sh ? badge("quarantine: contains LOT-S-77", "bad") : badge("released", "ok")),
      h("ul", null,
        node("Delivered via", u.ship, "TEST-1 → customer", att(u, "ship", u.ship)),
        node("Final test", `TEST-1 · SLT ${u.slt} · bin ${u.bin}`, "system-level test", att(u, "test", u.bin)),
        h("li", null, h("div", { class: "tn" }, h("div", { class: "tt" }, "Packaged at"), h("div", { class: "tid mono" }, `${u.site} · ${u.pkgLot} · ${u.tool}`), h("div", { class: "mono small att" }, `attestation ${att(u, "pkg", u.pkgLot)}`)),
          h("ul", null,
            node("Logic die", u.logic, `${LOT_INFO[u.logic].supplier} · wafer lot`, att(u, "logic", u.logic)),
            node("HBM stacks ×8", u.hbm, `${LOT_INFO[u.hbm].supplier} · HBM3E 12-Hi`, att(u, "hbm", u.hbm)),
            node("Substrate", u.sub, `${LOT_INFO[u.sub].supplier} · ${LOT_INFO[u.sub].part}` + (sh ? " · QUALITY HOLD" : ""), att(u, "sub", u.sub), sh ? "bad" : ""))))));
}
function lotView(lot, go) {
  const us = UNITS.filter((u) => [u.logic, u.hbm, u.sub].includes(lot)), by = {};
  us.forEach((u) => { by[u.order] = (by[u.order] ?? 0) + 1; });
  const info = LOT_INFO[lot];
  return h("div", { class: "stack" },
    h("div", { class: "grid g3" }, kpi("Lot", h("span", { class: "mono", style: "font-size:20px" }, lot), info ? `${info.part} · ${info.supplier}` : "unknown lot"),
      kpi("Modules containing it", fmt.int(us.length), `in this ${UNITS.length}-serial sample`),
      kpi("Customer orders touched", Object.keys(by).length, Object.entries(by).map(([o, n]) => `${o}: ${n}`).join(" · ") || "-")),
    held(lot) ? h("div", { class: "note bad" }, h("b", null, "Recall scope in one query. "), `LOT-S-77 is on quality hold; ${us.length} sample modules built with it are quarantined. Without genealogy this is a manual search across OSAT, test and shipping records.`) : null,
    table([{ label: "Serial", render: (u) => h("a", { href: "#/trace", onclick: (e) => { e.preventDefault(); go(u.serial); } }, h("span", { class: "mono" }, u.serial)) },
      { label: "Order", key: "order" }, { label: "Packaged at", key: "site" }, { label: "Substrate", render: (u) => h("span", { class: "mono" }, u.sub) }, { label: "Status", render: status }], us));
}

export default function trace(root) {
  const input = h("input", { type: "text", placeholder: "serial, order or lot: ORD-1002 · LOT-S-77", value: query, "aria-label": "Search serial, order or lot", onkeydown: (e) => e.key === "Enter" && go(input.value) });
  const out = h("div", { style: "margin-top:16px" });
  const go = (q, log = true) => {
    query = q.trim(); clear(out); input.value = query; if (!query) return;
    const Q = query.toUpperCase(), u = UNITS.find((x) => x.serial === Q);
    if (log) append("Pat Planner", "trace.query", { query: Q, kind: u ? "serial" : /^ORD-/.test(Q) ? "order" : "lot" });
    if (u) out.append(h("div", { class: "card" }, h("h2", null, "Backward genealogy"), genealogy(u), h("div", { class: "muted small", style: "margin-top:10px" }, "Each node carries a hash attestation of its custody event. In production the supplier would sign it; here it is derived in the browser.")));
    else if (/^ORD-/.test(Q)) {
      const us = UNITS.filter((x) => x.order === Q);
      out.append(us.length ? h("div", { class: "card" }, h("h2", null, `${Q}: ${us.length} sample modules`), table([{ label: "Serial", render: (x) => h("a", { href: "#/trace", onclick: (e) => { e.preventDefault(); go(x.serial); } }, h("span", { class: "mono" }, x.serial)) },
        { label: "Site", key: "site" }, { label: "Substrate", render: (x) => h("span", { class: "mono" }, x.sub) }, { label: "Status", render: status }], us))
        : h("div", { class: "note warn" }, "No sample modules for that order (ORD-1005 and ORD-1006 are compliance-held and never built)."));
    } else if (LOT_INFO[Q]) out.append(lotView(Q, go));
    else out.append(h("div", { class: "note warn" }, "Nothing found. Try one of the examples."));
  };
  const chip = (t) => h("button", { class: "btn sm", onclick: () => go(t) }, t);
  root.append(h("div", { class: "page-head" }, h("div", null, h("h1", null, "Trace"), h("div", { class: "sub" },
      "Traceability both ways: pick a module to see every lot and site that made it; pick a lot to see every module and customer it reached. Run the incident, then search ", h("span", { class: "mono" }, "LOT-S-77"), " for the blast radius."))),
    h("div", { class: "card" }, h("div", { class: "row" }, h("div", { class: "grow" }, input), h("button", { class: "btn primary", onclick: () => go(input.value) }, "Trace")),
      h("div", { class: "row", style: "margin-top:10px" }, h("span", { class: "muted small" }, "Try:"), chip(UNITS[0].serial), chip(UNITS.find((u) => u.sub === "LOT-S-77").serial), chip("LOT-S-77"), chip("ORD-1002"))), out);
  if (query) go(query, false);
}
