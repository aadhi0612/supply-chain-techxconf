// Shared in-memory state for the walkthrough. Nothing here talks to a server: the "ledger" is a real SHA-256 hash chain computed
// in the browser (same construction as siliconflow/store/audit.py), but it is a simulation, not the platform's audit log.
import { sha256 } from "./sha.js";

export const ZERO = "0".repeat(64);
export const S = { stage: "normal", step: 0, option: "max", approvals: {}, executed: false, confirmed: {}, chain: [], clock: 0, playing: false, backup: null };
const subs = new Set();
export const onChange = (fn) => { subs.add(fn); return () => subs.delete(fn); };
export const emit = () => subs.forEach((f) => f());

const body = (b) => JSON.stringify({ seq: b.seq, ts: b.ts, actor: b.actor, type: b.type, payload: b.payload, prev: b.prev });
export const hashBlock = (b) => sha256(body(b));

export function append(actor, type, payload) {
  const prev = S.chain.length ? S.chain[S.chain.length - 1].hash : ZERO;
  const b = { seq: S.chain.length, ts: new Date().toISOString(), actor, type, payload, prev };
  b.hash = hashBlock(b); b.orig = JSON.stringify(payload);
  S.chain.push(b); emit(); return b;
}

// First block whose link or content no longer matches, or null when the chain is intact.
export function verify(chain = S.chain) {
  let prev = ZERO;
  for (const b of chain) {
    if (b.prev !== prev) return { seq: b.seq, why: "prev-hash link does not match the previous block", expected: prev, got: b.prev };
    const h = hashBlock(b);
    if (h !== b.hash) return { seq: b.seq, why: "content no longer hashes to the stored hash", expected: b.hash, got: h };
    prev = b.hash;
  }
  return null;
}

const keepOriginal = () => { if (!S.backup) S.backup = JSON.stringify(S.chain); };

export function tamper(i) {
  const b = S.chain[i]; if (!b) return;
  keepOriginal();
  const p = b.payload;
  const k = Object.keys(p).find((x) => typeof p[x] === "number");
  if (k) p[k] += 500; else p.note = "edited after the fact";
  emit();
}
// Undo either kind of tampering: put back the chain as it was before the first edit.
export function restore() { if (S.backup) { S.chain = JSON.parse(S.backup); S.backup = null; } emit(); }

// An insider edits block i and recomputes every later link, so the chain still verifies on its own.
export function rewrite(i) {
  const b = S.chain[i]; if (!b) return;
  tamper(i);
  let prev = i ? S.chain[i - 1].hash : ZERO;
  for (let j = i; j < S.chain.length; j++) { const x = S.chain[j]; x.prev = prev; x.hash = hashBlock(x); prev = x.hash; }
  emit();
}

// The "ledger": a separate, append-only list of Merkle roots with its own hash chain and its own clock. In production this is a
// store the platform operator cannot rewrite; here it is simulated, so it lives apart from S.chain and is never touched by tamper().
export const LEDGER = { blocks: [] };
const ledgerHash = (a) => sha256(JSON.stringify([a.prev, a.idx, a.ts, a.root, a.from, a.to]));
export const anchoredThrough = () => (LEDGER.blocks.length ? LEDGER.blocks[LEDGER.blocks.length - 1].to : -1);

export function anchorNow() {
  if (verify()) return { error: "The chain is already broken, so it will not be anchored." };
  const from = anchoredThrough() + 1, to = S.chain.length - 1;
  if (to < from) return { error: "Nothing new to anchor. Use the other tabs to create activity first." };
  const prev = LEDGER.blocks.length ? LEDGER.blocks[LEDGER.blocks.length - 1].hash : ZERO;
  const a = { idx: LEDGER.blocks.length + 1, ts: new Date().toISOString(), from, to, prev, root: merkle(S.chain.slice(from, to + 1).map(hashBlock)).root };
  a.hash = ledgerHash(a);
  LEDGER.blocks.push(a); emit();
  return a;
}

// Roots are recomputed from block *content* and compared with the ledger's copy, never with hashes stored in the chain.
export function verifyAnchors() {
  const failures = []; let prev = ZERO;
  for (const a of LEDGER.blocks) {
    if (a.prev !== prev || a.hash !== ledgerHash(a)) failures.push({ idx: a.idx, why: "the ledger's own chain is broken" });
    prev = a.hash;
    const rows = S.chain.slice(a.from, a.to + 1);
    if (rows.length !== a.to - a.from + 1) failures.push({ idx: a.idx, why: `blocks #${a.from}-#${a.to} are missing` });
    else if (merkle(rows.map(hashBlock)).root !== a.root) failures.push({ idx: a.idx, why: `blocks #${a.from}-#${a.to} no longer match the root published to the ledger` });
  }
  return { failures, count: LEDGER.blocks.length, through: anchoredThrough() };
}

export function proofFor(seq) {
  const a = LEDGER.blocks.find((x) => x.from <= seq && seq <= x.to);
  if (!a) return { anchored: false };
  const leaves = S.chain.slice(a.from, a.to + 1).map(hashBlock), leaf = leaves[seq - a.from];
  const { root, proof } = merkle(leaves, seq - a.from);
  const stored = S.chain[seq].hash, valid = leaf === stored && proofOk(leaf, proof, a.root) && root === a.root;
  return { anchored: true, a, leaf, stored, proof, root, valid,
    reason: valid ? "the block recomputed from its content hashes into the root the ledger published"
      : leaf !== stored ? "the block's content no longer matches its stored hash (edited after the fact)"
      : "the block or its neighbours no longer reproduce the root the ledger published (history was rewritten after anchoring)" };
}

// Merkle root over block hashes (odd node is paired with itself) plus an inclusion proof for one leaf.
export function merkle(hashes, idx = -1) {
  if (!hashes.length) return { root: ZERO, proof: [] };
  let level = hashes.slice(), i = idx; const proof = [];
  while (level.length > 1) {
    if (level.length % 2) level.push(level[level.length - 1]);
    if (i >= 0) { const sib = i ^ 1; proof.push({ hash: level[sib], left: sib < i }); i >>= 1; }
    const next = []; for (let j = 0; j < level.length; j += 2) next.push(sha256(level[j] + level[j + 1]));
    level = next;
  }
  return { root: level[0], proof };
}
export const proofOk = (leaf, proof, root) => proof.reduce((h, s) => sha256(s.left ? s.hash + h : h + s.hash), leaf) === root;

export function reset() {
  LEDGER.blocks = [];
  Object.assign(S, { backup: null, stage: "normal", step: 0, option: "max", approvals: {}, executed: false, confirmed: {}, chain: [], clock: 0, playing: false });
  append("system", "tenant.created", { tenant: "demo", dataset: "AX-900 sample", note: "illustrative data" });
  append("Pat Planner", "snapshot.imported", { version: 1, orders: 6, units: 12700, plannable: 10000 });
}
