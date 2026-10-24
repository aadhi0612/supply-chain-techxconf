// Logic test for the static Experience ledger (anchoring, proofs, edit vs insider rewrite). No browser needed:  node ui/e2e/ledger.mjs
import * as s from "../experience/js/state.js";

let failed = 0;
const t = (c, m) => { if (!c) { failed++; console.log("FAIL", m); } else console.log("  ok", m); };

s.reset(); s.append("Pat", "a", { n: 1 }); s.append("Sam", "b", { n: 2 });
t(!s.verify() && s.proofFor(1).anchored === false, "fresh chain is intact and unanchored");
const a = s.anchorNow();
t(a.from === 0 && a.to === 3, "anchors every block so far");
t(s.verifyAnchors().failures.length === 0 && s.proofFor(1).valid, "anchors verify and the proof is valid");
s.tamper(1);
t(s.verify()?.seq === 1, "editing a block breaks the chain at exactly that block");
t(!s.proofFor(1).valid && /edited/.test(s.proofFor(1).reason), "its proof fails because the content changed");
t(!!s.anchorNow().error, "a broken chain is never anchored");
s.restore();
t(!s.verify() && s.verifyAnchors().failures.length === 0, "restore puts history back");
s.rewrite(1);
t(!s.verify(), "an insider rewrite leaves the chain internally consistent");
t(s.verifyAnchors().failures.length === 1, "but the ledger exposes it");
t(!s.proofFor(1).valid && /rewritten/.test(s.proofFor(1).reason), "and the proof fails");
s.restore();
t(s.verifyAnchors().failures.length === 0 && s.proofFor(3).valid, "restored history verifies again");
s.append("x", "c", {});
t(s.anchoredThrough() === 3 && s.anchorNow().from === 4 && s.LEDGER.blocks.length === 2, "new activity is anchored in a second ledger block");
process.exit(failed ? 1 : 0);
