from __future__ import annotations


def make_users(pf, tenant="demo"):
    users = {}
    for name, roles in [("lead", ["planner"]), ("planner2", ["planner"]), ("procurement", ["procurement"]), ("finance", ["finance"]),
                        ("sales", ["sales"]), ("compliance", ["compliance"]), ("quality", ["quality"]), ("viewer", ["viewer"])]:
        users[name] = pf.authenticate(pf.create_user(tenant, name, roles)[1])
    return users
