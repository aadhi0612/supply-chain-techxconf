"""Viewer-aware provenance resolver.

The confidentiality rule is enforced HERE, in code, before anything reaches a language model: a viewer sees names, real lot ids,
countries and exact dates only down to its direct suppliers. Everything deeper is replaced by a keyed alias / token, a region, an
ISO week, and an HMAC attestation from the deepest visible supplier. Hidden names never enter the model's context, so no prompt
can make the model reveal them.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections import Counter

from .graph import ORDERS, OSAT_SITE, SUPPLIERS, TODAY, Graph, Lot, build_graph

VIEWERS = {"oem": 0, "tier1": 1, "tier2": 2}  # level L sees tiers 1..L+1 by name
LABEL = {"oem": "OEM (brand owner)", "tier1": "Tier-1 supplier (OSAT)", "tier2": "Tier-2 supplier"}
EXPORT = {"US": "allowed", "JP": "allowed", "DE": "allowed", "SG": "licence LIC-0042 on file"}


def _iso_week(d) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


class Resolver:
    def __init__(self, graph: Graph | None = None, secret: str = "demo-secret"):
        self.g = graph or build_graph()
        self._secret = secret.encode()
        self._tokens = {self.token(l): l for l in self.g.lots}
        self._att = {l: self._digest(l) for l in self.g.lots}  # "signed" once, at build time
        self._pkg_att = {p: self._pkg_digest(p) for p in self.g.packages}

    # ---- keyed pseudonyms and attestations -------------------------------------------------------
    def _mac(self, *parts: str) -> str:
        return hmac.new(self._secret, "|".join(parts).encode(), hashlib.sha256).hexdigest()

    def token(self, lot_id: str) -> str:
        return "LOT-" + self._mac("lot", lot_id)[:8].upper()

    def alias(self, supplier: str) -> str:
        return f"Undisclosed Tier-{SUPPLIERS[supplier][1]} supplier #{self._mac('sup', supplier)[:4].upper()}"

    def _digest(self, lot_id: str) -> str:
        """The lot's supplier vouches for its whole upstream subtree; keyed per supplier so it cannot be brute-forced from ids."""
        g, lot = self.g, self.g.lots[lot_id]
        body = f"{lot.id}|{lot.supplier}|{lot.made}|" + "|".join(sorted(f"{a}:{g.lots[a].supplier}:{g.lots[a].made}" for a in g.ancestors[lot_id]))
        return self._mac("att", lot.supplier, body)

    def _pkg_digest(self, pid: int) -> str:
        """The Tier-1 packager vouches for every component lot (and everything above them) in one package."""
        pk = self.g.packages[pid]
        ids = sorted({c for c in pk.lots.values()} | {a for c in pk.lots.values() for a in self.g.ancestors[c]})
        return self._mac("att", pk.site, f"{pid}|{pk.site}|" + "|".join(f"{i}:{self.g.lots[i].supplier}:{self.g.lots[i].made}" for i in ids))

    def verify_package(self, pid: int) -> bool:
        return hmac.compare_digest(self._pkg_att[pid], self._pkg_digest(pid))

    def verify(self, lot_id: str) -> bool:
        return hmac.compare_digest(self._att[lot_id], self._digest(lot_id))

    # ---- visibility -------------------------------------------------------------------------------
    @staticmethod
    def level(viewer: str) -> int:
        if viewer not in VIEWERS:
            raise ValueError(f"unknown viewer {viewer!r}")
        return VIEWERS[viewer]

    def tier(self, lot_id: str) -> int:
        return SUPPLIERS[self.g.lots[lot_id].supplier][1]

    def named(self, lot_id: str, viewer: str) -> bool:
        return self.tier(lot_id) <= self.level(viewer) + 1

    def ref(self, lot_id: str, viewer: str) -> str:
        return lot_id if self.named(lot_id, viewer) else self.token(lot_id)

    def supplier_label(self, supplier: str, viewer: str) -> str:
        return SUPPLIERS[supplier][0] if SUPPLIERS[supplier][1] <= self.level(viewer) + 1 else self.alias(supplier)

    def hidden_terms(self, viewer: str) -> set[str]:
        """Everything this viewer must never see: used by the output guard and by the leak tests."""
        lv = self.level(viewer)
        terms = {n for s, (n, t, *_r) in SUPPLIERS.items() if t > lv + 1} | {s for s, v in SUPPLIERS.items() if v[1] > lv + 1}
        terms |= {lid for lid in self.g.lots if self.tier(lid) > lv + 1}
        terms |= {f"{SUPPLIERS[s][3]}" for s in ()}
        return terms

    # ---- views ------------------------------------------------------------------------------------
    def _node(self, lot: Lot, viewer: str) -> dict:
        named = self.named(lot.id, viewer)
        sup = SUPPLIERS[lot.supplier]
        return {
            "tier": sup[1],
            "named": named,
            "supplier": self.supplier_label(lot.supplier, viewer),
            "does": sup[2],
            "part": lot.part,
            "lot": self.ref(lot.id, viewer),
            "where": sup[3] if named else sup[4],
            "made": lot.made.isoformat() if named else _iso_week(lot.made),
            "hold": (lot.hold or "quality hold") if lot.hold and named else ("quality hold" if lot.hold else None),
            "parents": [self.ref(p, viewer) for p in lot.parents],
        }

    def _chain(self, pk, viewer: str) -> tuple[list[dict], list[dict], list[dict]]:
        g = self.g
        ids: list[str] = []
        for role in ("die", "hbm", "substrate", "bump"):
            for lid in [pk.lots[role], *sorted(g.ancestors[pk.lots[role]], key=lambda x: (self.tier(x), x))]:
                if lid not in ids:
                    ids.append(lid)
        ids.sort(key=lambda x: (self.tier(x), x))
        nodes = [self._node(g.lots[i], viewer) for i in ids]
        asm = next(e["d"] for e in pk.events if e["stage"] == "assembly")
        sup = SUPPLIERS[pk.site]
        nodes.insert(
            0,
            {
                "tier": 1,
                "named": True,
                "supplier": sup[0],
                "does": sup[2],
                "part": "assembled AX-900 module",
                "lot": f"PKG-{pk.id}",
                "where": sup[3],
                "made": asm.isoformat(),
                "hold": None,
                "parents": [self.ref(pk.lots[r], viewer) for r in ("die", "hbm", "substrate", "bump")],
            },
        )
        atts, alerts = [], []
        hid = {a for i in ids for a in [i] if not self.named(a, viewer)}
        if hid:
            hs = {g.lots[h].supplier for h in hid}
            atts.append(
                {
                    "attester": sup[0],
                    "attester_tier": 1,
                    "lot": f"PKG-{pk.id}",
                    "covers_lots": len(hid),
                    "covers_suppliers": len(hs),
                    "tiers": [min(SUPPLIERS[x][1] for x in hs), max(SUPPLIERS[x][1] for x in hs)],
                    "digest": self._pkg_att[pk.id][:16],
                    "verified": self.verify_package(pk.id),
                    "statement": "Simulated per-supplier HMAC over its upstream lots, suppliers and dates; the names stay private.",
                }
            )
            if any(g.lots[h].hold for h in hid):
                alerts.append({"severity": "high", "text": f"An undisclosed upstream lot under {sup[0]}'s supply chain is on quality hold."})
        for i in ids:
            if not self.named(i, viewer):
                continue
            hidden = {a for p in g.lots[i].parents for a in ({p} | g.ancestors[p]) if not self.named(a, viewer)}
            if not hidden:
                continue
            sups = {g.lots[h].supplier for h in hidden}
            ok = self.verify(i)
            atts.append(
                {
                    "attester": SUPPLIERS[g.lots[i].supplier][0],
                    "attester_tier": self.tier(i),
                    "lot": i,
                    "covers_lots": len(hidden),
                    "covers_suppliers": len(sups),
                    "tiers": [min(SUPPLIERS[s][1] for s in sups), max(SUPPLIERS[s][1] for s in sups)],
                    "digest": self._att[i][:16],
                    "verified": ok,
                    "statement": "Simulated per-supplier HMAC over its upstream lots, suppliers and dates; the names stay private.",
                }
            )
            if any(g.lots[h].hold for h in hidden):
                alerts.append({"severity": "high", "text": f"An undisclosed upstream lot under {SUPPLIERS[g.lots[i].supplier][0]} is on quality hold."})
        for i in ids:
            if g.lots[i].hold and self.named(i, viewer):
                alerts.append({"severity": "high", "text": f"Lot {i} ({SUPPLIERS[g.lots[i].supplier][0]}) is on {g.lots[i].hold}."})
        return nodes, atts, alerts

    def _events(self, pk, viewer: str) -> list[dict]:
        out = []
        for e in pk.events:
            st, d = e["stage"], e["d"].isoformat()
            if st == "inbound":
                lot = self.g.lots[e["lot"]]
                txt = f"Received {lot.part} lot {self.ref(lot.id, viewer)} from {self.supplier_label(lot.supplier, viewer)}"
            elif st == "assembly":
                txt = f"2.5D assembly at {OSAT_SITE[pk.site]}, tool {e['tool']}"
            elif st == "underfill":
                txt = "Underfill and cure"
            elif st == "test":
                txt = f"Final test {e['result']}: {e['detail']}"
            elif st == "ship":
                txt = f"Shipped ({e['carrier']})"
            elif st == "customs":
                dest = next(o[2] for o in ORDERS if o[0] == pk.order)
                txt = f"Export check: {EXPORT[dest]} (destination {dest})" if viewer == "oem" else "Export check passed"
            else:
                txt = "Delivered to customer"
            out.append({"date": d, "stage": st, "text": txt})
        return out

    def package(self, pid: int, viewer: str) -> dict | None:
        pk = self.g.packages.get(pid)
        if not pk:
            return None
        nodes, atts, alerts = self._chain(pk, viewer)
        if pk.status == "on_hold" and not alerts:
            alerts.append({"severity": "high", "text": "Package is quarantined."})
        o = next(o for o in ORDERS if o[0] == pk.order)
        head = {"id": pid, "sku": "AX-900 accelerator module", "status": pk.status, "assembled_at": OSAT_SITE[pk.site], "test_bin": pk.test["bin"]}
        if viewer == "oem":
            head |= {"order": o[0], "customer": o[1], "destination": o[2]}
        ok = all(a["verified"] for a in atts)
        return {
            "kind": "package",
            "viewer": viewer,
            "viewer_label": LABEL[viewer],
            "package": head,
            "chain": nodes,
            "events": self._events(pk, viewer),
            "attestations": atts,
            "alerts": alerts,
            "verdict": {
                "genuine": ok and not alerts,
                "attestations_verified": ok,
                "summary": (
                    "All upstream attestations verify and nothing is on hold."
                    if ok and not alerts
                    else "Attestation failed verification."
                    if not ok
                    else "Provenance verifies, but the package is affected by a hold."
                ),
            },
        }

    def lot(self, ref: str, viewer: str) -> dict | None:
        ref = ref.upper()
        lot_id = self._tokens.get(ref) or (ref if ref in self.g.lots and self.named(ref, viewer) else None)
        if not lot_id:
            return None  # a real id of a hidden lot is indistinguishable from "does not exist"
        pks = [self.g.packages[p] for p in self.g.lot_packages.get(lot_id, [])]
        st = Counter(p.status for p in pks)
        out = {
            "kind": "lot",
            "viewer": viewer,
            "viewer_label": LABEL[viewer],
            "lot": self._node(self.g.lots[lot_id], viewer),
            "packages": {"count": len(pks), "sample": [p.id for p in pks[:12]], "by_status": dict(st)},
        }
        if viewer == "oem":
            out["packages"]["by_order"] = dict(Counter(p.order for p in pks))
        return out

    def order(self, order_id: str, viewer: str) -> dict | None:
        o = next((o for o in ORDERS if o[0] == order_id.upper()), None)
        if not o or viewer != "oem":
            return None
        pks = [p for p in self.g.packages.values() if p.order == o[0]]
        return {
            "kind": "order",
            "viewer": viewer,
            "viewer_label": LABEL[viewer],
            "order": {"id": o[0], "customer": o[1], "destination": o[2]},
            "packages": {"count": len(pks), "by_status": dict(Counter(p.status for p in pks)), "sample": [p.id for p in pks[:12]]},
        }

    def catalog(self, viewer: str) -> dict:
        pk = self.g.packages
        held = next(p for p, v in pk.items() if v.status == "on_hold")
        return {
            "kind": "catalog",
            "viewer": viewer,
            "viewer_label": LABEL[viewer],
            "packages": len(pk),
            "lots": len(self.g.lots),
            "tiers": 5,
            "as_of": TODAY.isoformat(),
            "by_status": dict(Counter(p.status for p in pk.values())),
            "try": {"healthy": 1124544, "on_hold": held, "in_transit": next(p for p, v in pk.items() if v.status == "in_transit")},
        }

    # ---- lookup from free text --------------------------------------------------------------------
    def refs(self, text: str) -> dict:
        up = text.upper()
        return {
            "packages": [int(m) for m in re.findall(r"\b\d{7}\b", text) if int(m) in self.g.packages][:1],
            "lots": [m for m in re.findall(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\b", up) if m in self.g.lots or m in self._tokens and not m.startswith("ORD-")][:1],
            "orders": [m for m in re.findall(r"\bORD-\d{4}\b", up)][:1],
        }

    def lookup(self, text: str, viewer: str) -> dict:
        r = self.refs(text)
        res = (
            (self.package(r["packages"][0], viewer) if r["packages"] else None)
            or (self.lot(r["lots"][0], viewer) if r["lots"] else None)
            or (self.order(r["orders"][0], viewer) if r["orders"] else None)
        )
        return res or self.catalog(viewer)
