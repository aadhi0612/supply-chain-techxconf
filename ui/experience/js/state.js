// Shared in-memory state for the walkthrough. Nothing here talks to a server: the "ledger" is a real SHA-256 hash chain computed
// in the browser (same construction as siliconflow/store/audit.py), but it is a simulation, not the platform's audit log.
import { sha256 } from "./sha.js";

export const ZERO = "0".repeat(64);
export const S = { stage: "normal", step: 0, option: "max", approvals: {}, executed: false, confirmed: {}, chain: [], clock: 0, playing: false };
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

export function tamper(i) {
  const b = S.chain[i]; if (!b) return;
  const p = b.payload;
  const k = Object.keys(p).find((x) => typeof p[x] === "number");
  if (k) p[k] += 500; else p.note = "edited after the fact";
  emit();
}
export function repair(i) { const b = S.chain[i]; if (b) { b.payload = JSON.parse(b.orig); emit(); } }

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
  Object.assign(S, { stage: "normal", step: 0, option: "max", approvals: {}, executed: false, confirmed: {}, chain: [], clock: 0, playing: false });
  append("system", "tenant.created", { tenant: "demo", dataset: "AX-900 sample", note: "illustrative data" });
  append("Pat Planner", "snapshot.imported", { version: 1, orders: 6, units: 12700, plannable: 10000 });
}
