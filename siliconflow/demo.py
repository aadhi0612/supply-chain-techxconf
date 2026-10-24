"""Development-only seeding: one tenant, one user per role with FIXED public keys, and the sample dataset.

Never enable in production: the keys are published in this file and served unauthenticated by /v1/demo/users.
"""
from __future__ import annotations

from .sample_data import build_sample
from .services.platform import Platform, Principal

TENANT = "demo"
USERS = [
    ("Pat Planner", ["planner"]), ("Sam Second-Planner", ["planner"]), ("Priya Procurement", ["procurement"]),
    ("Fiona Finance", ["finance"]), ("Sasha Sales", ["sales"]), ("Carl Compliance", ["compliance"]),
    ("Quinn Quality", ["quality"]), ("Vic Viewer", ["viewer"]),
]


def demo_key(name: str) -> str:
    return "sf_demo_" + name.split()[0].lower()


def demo_users() -> list[dict]:
    return [{"name": n, "roles": r, "api_key": demo_key(n)} for n, r in USERS]


def seed(pf: Platform) -> Principal:
    """Idempotent: creates missing users, imports the sample dataset only if the tenant has none."""
    lead = None
    for name, roles in USERS:
        key = demo_key(name)
        try:
            p = pf.authenticate(key)
        except Exception:
            pf.create_user(TENANT, name, roles, api_key=key)
            p = pf.authenticate(key)
        lead = lead or p
    try:
        pf.current(TENANT)
    except Exception:
        plan = build_sample()
        pf.import_plan(lead, plan, "demo sample dataset")
    return lead
