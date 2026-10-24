"""CSV importers for the high-volume operational tables (customer-authorised exports).

Master data (parts, SKUs/BOM, routes, resources, lanes, compliance) is supplied as JSON; the tables below
are what planners actually re-export every day and are layered on top of that master data.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from pydantic import ValidationError

from ..domain import CapacityBucket, InventoryLot, Order, PlanInput, Provenance, SupplyCommitment

TRUE = {"1", "true", "yes", "y", "t"}


class ImportError_(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors[:10]) + (f" (+{len(errors) - 10} more)" if len(errors) > 10 else ""))
        self.errors = errors


def _rows(text: str):
    rdr = csv.DictReader(io.StringIO(text.strip()))
    for i, row in enumerate(rdr, start=2):
        yield i, {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}


def _b(v: str, default=False) -> bool:
    return default if v in (None, "") else v.lower() in TRUE


def _prov(row: dict, default_source: str) -> Provenance:
    obs = row.get("observed_at")
    kw = {"source": row.get("source") or default_source, "method": "csv"}
    if obs:
        kw["observed_at"] = datetime.fromisoformat(obs.replace("Z", "+00:00")).astimezone(timezone.utc)
    if row.get("confidence"):
        kw["confidence"] = float(row["confidence"])
    return Provenance(**kw)


def _opt(row, key):
    v = row.get(key)
    return v if v not in (None, "") else None


def parse_orders(text: str) -> list[Order]:
    out, errs = [], []
    for ln, r in _rows(text):
        try:
            out.append(Order(
                id=r["id"], customer_id=r["customer_id"], sku_id=r["sku_id"], qty=int(r["qty"]),
                requested_week=r["requested_week"], committed_week=r["committed_week"],
                latest_week=r.get("latest_week") or r["committed_week"], priority=int(r.get("priority") or 2),
                protected=_b(r.get("protected")), unit_price=float(r.get("unit_price") or 0),
                penalty_per_unit_week=float(r.get("penalty_per_unit_week") or 0), allow_partial=_b(r.get("allow_partial"), True),
                min_ship_qty=int(r.get("min_ship_qty") or 0), destination=r["destination"], end_user=r.get("end_user", ""),
                end_use=r.get("end_use", "")))
        except (KeyError, ValueError, ValidationError) as e:
            errs.append(f"orders.csv line {ln}: {_short(e)}")
    if errs:
        raise ImportError_(errs)
    return out


def parse_commitments(text: str) -> list[SupplyCommitment]:
    out, errs = [], []
    for ln, r in _rows(text):
        try:
            out.append(SupplyCommitment(
                id=r["id"], part_id=r["part_id"], qty=int(r["qty"]), promised_week=r["promised_week"],
                status=r.get("status") or "confirmed", po_ref=_opt(r, "po_ref"), confidence=float(r.get("confidence") or 1.0),
                provenance=_prov(r, "erp")))
        except (KeyError, ValueError, ValidationError) as e:
            errs.append(f"commitments.csv line {ln}: {_short(e)}")
    if errs:
        raise ImportError_(errs)
    return out


def parse_inventory(text: str) -> list[InventoryLot]:
    out, errs = [], []
    for ln, r in _rows(text):
        try:
            out.append(InventoryLot(
                id=r["id"], part_id=r["part_id"], qty=int(r["qty"]), status=r.get("status") or "available",
                available_week=r["available_week"], release_week=_opt(r, "release_week"), location=r.get("location", ""),
                provenance=_prov(r, "erp")))
        except (KeyError, ValueError, ValidationError) as e:
            errs.append(f"inventory.csv line {ln}: {_short(e)}")
    if errs:
        raise ImportError_(errs)
    return out


def parse_capacity(text: str) -> list[CapacityBucket]:
    out, errs = [], []
    for ln, r in _rows(text):
        try:
            out.append(CapacityBucket(
                resource_id=r["resource_id"], week=r["week"], nominal=int(r["nominal"]), reserved=int(r["reserved"]),
                status=r.get("status") or "confirmed", provenance=_prov(r, "supplier_portal")))
        except (KeyError, ValueError, ValidationError) as e:
            errs.append(f"capacity.csv line {ln}: {_short(e)}")
    if errs:
        raise ImportError_(errs)
    return out


def _short(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:3])
    return f"{type(e).__name__}: {e}"


PARSERS = {"orders": ("orders", parse_orders), "commitments": ("commitments", parse_commitments),
           "inventory": ("inventory", parse_inventory), "capacity": ("capacity", parse_capacity)}


def layer_tables(base: PlanInput, tables: dict[str, str]) -> PlanInput:
    """Replace whole operational tables on top of `base` master data. Unknown table names are rejected."""
    unknown = set(tables) - set(PARSERS)
    if unknown:
        raise ImportError_([f"unknown table(s): {sorted(unknown)}; expected {sorted(PARSERS)}"])
    new = base.copy_deep()
    errs: list[str] = []
    for name, text in tables.items():
        attr, fn = PARSERS[name]
        try:
            setattr(new, attr, fn(text))
        except ImportError_ as e:
            errs += e.errors
    if errs:
        raise ImportError_(errs)
    new.as_of = datetime.now(timezone.utc)
    return new
