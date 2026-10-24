"""Where anchor roots are published, kept apart from the platform database.

`LocalLedger` is a stand-in for a shared ledger: its own SQLite file, its own hash chain and its own clock. It plays the role
of the party the platform operator cannot rewrite. A real deployment replaces it with an adapter for a consortium ledger
or a timestamp authority behind the same four methods.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Protocol

GENESIS = "0" * 64


class LedgerUnavailable(RuntimeError):
    pass


class LedgerAdapter(Protocol):
    name: str

    def publish(self, tenant: str, root: str, seq_from: int, seq_to: int) -> dict: ...
    def get(self, index: int) -> dict | None: ...
    def verify(self) -> dict: ...
    def head(self) -> dict: ...


def _block_hash(prev: str, index: int, ts: str, tenant: str, root: str, seq_from: int, seq_to: int) -> str:
    return hashlib.sha256(json.dumps([prev, index, ts, tenant, root, seq_from, seq_to], separators=(",", ":")).encode()).hexdigest()


class LocalLedger:
    name = "local-ledger"

    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self.available = True  # flipped by tests and the demo "outage" switch
        with self._lock, self._db:
            self._db.execute("CREATE TABLE IF NOT EXISTS blocks (idx INTEGER PRIMARY KEY, ts TEXT NOT NULL, tenant TEXT NOT NULL, root TEXT NOT NULL,"
                             " seq_from INTEGER NOT NULL, seq_to INTEGER NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL)")

    def _need(self):
        if not self.available:
            raise LedgerUnavailable("ledger is unreachable")

    @staticmethod
    def _row(r) -> dict:
        return {"index": r[0], "ts": r[1], "tenant": r[2], "root": r[3], "seq_from": r[4], "seq_to": r[5], "prev_hash": r[6], "hash": r[7]}

    def publish(self, tenant: str, root: str, seq_from: int, seq_to: int) -> dict:
        self._need()
        with self._lock, self._db:
            last = self._db.execute("SELECT idx, hash FROM blocks ORDER BY idx DESC LIMIT 1").fetchone()
            idx, prev = (last[0] + 1, last[1]) if last else (1, GENESIS)
            ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()  # the ledger's clock, not the platform's
            h = _block_hash(prev, idx, ts, tenant, root, seq_from, seq_to)
            self._db.execute("INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?)", (idx, ts, tenant, root, seq_from, seq_to, prev, h))
        return {"index": idx, "ts": ts, "tenant": tenant, "root": root, "seq_from": seq_from, "seq_to": seq_to, "prev_hash": prev, "hash": h}

    def get(self, index: int) -> dict | None:
        self._need()
        with self._lock:
            r = self._db.execute("SELECT * FROM blocks WHERE idx=?", (index,)).fetchone()
        return self._row(r) if r else None

    def verify(self) -> dict:
        self._need()
        prev, n = GENESIS, 0
        with self._lock:
            rows = self._db.execute("SELECT * FROM blocks ORDER BY idx").fetchall()
        for r in rows:
            b = self._row(r)
            n += 1
            if b["index"] != n or b["prev_hash"] != prev or b["hash"] != _block_hash(prev, n, b["ts"], b["tenant"], b["root"], b["seq_from"], b["seq_to"]):
                return {"ok": False, "blocks": n, "broken_at": n}
            prev = b["hash"]
        return {"ok": True, "blocks": n, "head": prev}

    def head(self) -> dict:
        v = self.verify()
        return {"blocks": v["blocks"], "head": v.get("head", GENESIS)}
