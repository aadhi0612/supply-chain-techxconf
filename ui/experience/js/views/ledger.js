// Provenance: every step in the other tabs is a block in a hash chain. A separate ledger holds only Merkle roots; edit history and the
// chain breaks, rewrite it consistently and the ledger still catches it.
import { S, verify, tamper, rewrite, restore, anchorNow, verifyAnchors, proofFor, anchoredThrough, LEDGER, reset, emit } from "../state.js";
import { h, badge, kpi, table, short, toast } from "../util.js";

let picked = null;
export default function ledger(root) {
  const bad = verify(), va = verifyAnchors(), through = anchoredThrough(), pf = picked != null ? proofFor(picked) : null;
  const modified = !!S.backup;
  root.append(
    h("div", { class: "page-head" }, h("div", null, h("h1", null, "Ledger"), h("div", { class: "sub" },
      "Provenance and non-repudiation. Every action is a block hashed with the one before it (SHA-256, computed in your browser). Press Anchor to publish a Merkle root to a separate ledger, then try to cheat: edit a block, or rewrite history so the chain still verifies."))),
    h("div", { class: "grid g4" },
      kpi("Chain", bad ? badge("broken at #" + bad.seq, "bad") : badge("intact", "ok"), bad ? bad.why : `${S.chain.length} blocks, every link recomputed`),
      kpi("Ledger match", va.failures.length ? badge("MISMATCH", "bad") : va.count ? badge("matches", "ok") : badge("nothing anchored", "warn"), `${va.count} anchors checked`),
      kpi("Anchored through", through >= 0 ? "#" + through : "none", S.chain.length - 1 > through ? `${S.chain.length - 1 - through} newer blocks not anchored` : "everything is anchored"),
      kpi("Ledger blocks", LEDGER.blocks.length, "separate store, own chain and clock")),
    va.failures.length ? h("div", { class: "note bad", style: "margin-top:16px" }, h("b", null, "History no longer matches the ledger. "), va.failures.map((f) => h("div", null, f.why))) : null,
    bad ? h("div", { class: "note bad", style: "margin-top:12px" }, h("b", null, `Block #${bad.seq}: `), bad.why, h("div", { class: "mono small" }, `expected ${short(bad.expected)} · computed ${short(bad.got)}`)) : null,
    !bad && va.failures.length ? h("div", { class: "note warn", style: "margin-top:12px" }, "The chain on its own looks fine, because every later hash was recomputed. Only the ledger, which the editor cannot reach, exposes the rewrite.") : null,
    h("div", { class: "card", style: "margin-top:16px" }, h("div", { class: "row sb", style: "margin-bottom:8px" }, h("h2", null, "Blocks (click one for its proof)"), h("div", { class: "row" },
        h("button", { class: "btn sm primary", onclick: () => { const r = anchorNow(); toast(r.error ?? `Anchored blocks #${r.from}-#${r.to} as ledger block ${r.idx}`, r.error ? "err" : "ok"); } }, "Anchor now"),
        h("button", { class: "btn sm danger", disabled: picked == null, title: "Change one block; the chain breaks exactly there", onclick: () => { tamper(picked); toast(`Edited block #${picked} after the fact`, "err"); } }, "Edit selected"),
        h("button", { class: "btn sm danger", disabled: picked == null, title: "Edit the block and recompute every later hash, like an insider", onclick: () => { rewrite(picked); toast(`Rewrote history from block #${picked}`, "err"); } }, "Rewrite history from selected"),
        h("button", { class: "btn sm", disabled: !modified, onclick: restore }, "Restore"), h("button", { class: "btn sm", onclick: () => { picked = null; reset(); emit(); } }, "Reset all"))),
      table([{ label: "#", num: true, key: "seq" }, { label: "Actor", key: "actor" }, { label: "Event", render: (b) => h("span", { class: "mono" }, b.type) }, { label: "Payload", render: (b) => h("span", { class: "mono small" }, JSON.stringify(b.payload)) },
        { label: "Hash", render: (b) => h("span", { class: "mono small" }, short(b.hash)) },
        { label: "State", render: (b) => (bad && b.seq === bad.seq ? badge("broken", "bad") : picked === b.seq ? badge("selected", "info") : b.seq <= through ? badge("anchored", "ok") : badge("not anchored", "warn")) }],
        S.chain, { onRow: (b) => { picked = b.seq; emit(); } })),
    LEDGER.blocks.length ? h("div", { class: "card", style: "margin-top:16px" }, h("h2", null, "Ledger"),
      table([{ label: "Block", key: "idx" }, { label: "Covers", render: (a) => `#${a.from}–#${a.to}` }, { label: "Merkle root", render: (a) => h("span", { class: "mono small" }, short(a.root)) }, { label: "Ledger time", render: (a) => a.ts.slice(11, 19) + " UTC" }], LEDGER.blocks)) : null,
    pf ? h("div", { class: "card", style: "margin-top:16px" }, h("h2", null, `Inclusion proof for block #${picked}`),
      !pf.anchored ? h("div", { class: "note warn" }, "This block is not anchored yet. Press “Anchor now” first.") : h("div", null,
        h("div", { class: `note ${pf.valid ? "ok" : "bad"}` }, h("b", null, pf.valid ? "Proof valid. " : "Proof FAILED. "), pf.reason),
        h("pre", { class: "pay" }, JSON.stringify({ recomputed_from_content: short(pf.leaf), stored_hash: short(pf.stored), path_steps: pf.proof.length, root_recomputed: short(pf.root), root_on_ledger: short(pf.a.root), ledger_block: pf.a.idx }, null, 2)),
        h("div", { class: "muted small" }, "A counterparty holding only the ledger root can check a block without seeing any other block's contents."))) : null);
}
