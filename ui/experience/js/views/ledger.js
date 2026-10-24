// Provenance: every step in the other tabs is a block in a hash chain; edit one and verification names the broken link.
import { S, verify, tamper, repair, merkle, proofOk, reset, emit } from "../state.js";
import { h, badge, kpi, table, short, toast } from "../util.js";

let picked = null;
export default function ledger(root) {
  const bad = verify(), leaves = S.chain.map((b) => b.hash), { root: mroot } = merkle(leaves);
  const sel = picked != null ? S.chain[picked] : null;
  const m = sel ? merkle(leaves, picked) : null;
  root.append(
    h("div", { class: "page-head" }, h("div", null, h("h1", null, "Ledger"), h("div", { class: "sub" },
      "Provenance and non-repudiation. Each block hashes its content plus the previous block's hash (SHA-256, computed live in your browser). Change anything and verification fails at that block."))),
    h("div", { class: "grid g3" }, kpi("Blocks", S.chain.length, "grows as you use the other tabs"),
      kpi("Chain", bad ? badge("broken at #" + bad.seq, "bad") : badge("intact", "ok"), bad ? bad.why : "every link and hash re-computed"),
      kpi("Merkle root", h("span", { class: "mono", style: "font-size:15px" }, short(mroot)), "the single digest you would anchor externally")),
    bad ? h("div", { class: "note bad", style: "margin-top:16px" }, h("b", null, `Block #${bad.seq}: `), bad.why, h("div", { class: "mono small" }, `expected ${short(bad.expected)} · computed ${short(bad.got)}`)) : null,
    h("div", { class: "card", style: "margin-top:16px" }, h("div", { class: "row sb", style: "margin-bottom:8px" }, h("h2", null, "Blocks"), h("div", { class: "row" },
        h("button", { class: "btn sm danger", disabled: picked == null, onclick: () => { tamper(picked); toast(`Edited block #${picked} after the fact`, "err"); } }, "Tamper with selected"),
        h("button", { class: "btn sm", disabled: picked == null, onclick: () => repair(picked) }, "Repair"), h("button", { class: "btn sm", onclick: () => { picked = null; reset(); emit(); } }, "Reset all"))),
      table([{ label: "#", num: true, key: "seq" }, { label: "Actor", key: "actor" }, { label: "Event", render: (b) => h("span", { class: "mono" }, b.type) }, { label: "Payload", render: (b) => h("span", { class: "mono small" }, JSON.stringify(b.payload)) },
        { label: "Hash", render: (b) => h("span", { class: "mono small" }, short(b.hash)) }, { label: "", render: (b) => (bad && b.seq === bad.seq ? badge("broken", "bad") : picked === b.seq ? badge("selected", "info") : "") }],
        S.chain, { onRow: (b) => { picked = b.seq; emit(); } })),
    sel ? h("div", { class: "card", style: "margin-top:16px" }, h("h2", null, `Inclusion proof for block #${sel.seq}`),
      h("div", { class: "muted small" }, "A counterparty holding only the Merkle root can check this block was part of the history without seeing any other block's contents."),
      h("pre", { class: "pay" }, JSON.stringify({ leaf: sel.hash, path: m.proof.map((p) => (p.left ? "L:" : "R:") + short(p.hash)), root: mroot }, null, 2)),
      proofOk(sel.hash, m.proof, mroot) ? badge("proof valid for the current root", "ok") : badge("proof invalid", "bad"),
      bad ? h("div", { class: "muted small" }, "The root is computed from stored hashes, so it still verifies; only a full chain verification (above) catches the edited payload.") : null) : null);
}
