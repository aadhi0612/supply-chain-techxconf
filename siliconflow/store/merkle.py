"""Merkle tree over audit-row hashes. Leaves and inner nodes are domain-separated; an odd node is promoted unchanged."""
from __future__ import annotations

import hashlib


def _leaf(h: str) -> bytes:
    return hashlib.sha256(b"\x00" + bytes.fromhex(h)).digest()


def _node(l: bytes, r: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + l + r).digest()


def _levels(hashes: list[str]) -> list[list[bytes]]:
    if not hashes:
        raise ValueError("no leaves")
    level = [_leaf(h) for h in hashes]
    levels = [level]
    while len(level) > 1:
        level = [_node(level[i], level[i + 1]) if i + 1 < len(level) else level[i] for i in range(0, len(level), 2)]
        levels.append(level)
    return levels


def root(hashes: list[str]) -> str:
    return _levels(hashes)[-1][0].hex()


def proof(hashes: list[str], index: int) -> list[dict]:
    """Sibling path from leaf `index` to the root: [{"side": "left"|"right", "hash": hex}, ...]."""
    levels = _levels(hashes)
    path, i = [], index
    for level in levels[:-1]:
        sib = i ^ 1
        if sib < len(level):
            path.append({"side": "left" if sib < i else "right", "hash": level[sib].hex()})
        i //= 2
    return path


def verify_path(leaf_hash: str, path: list[dict], expected_root: str) -> bool:
    cur = _leaf(leaf_hash)
    for step in path:
        sib = bytes.fromhex(step["hash"])
        cur = _node(sib, cur) if step["side"] == "left" else _node(cur, sib)
    return cur.hex() == expected_root
