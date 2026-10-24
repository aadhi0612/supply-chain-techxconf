"""Deterministic dependency propagation: which orders depend on a changed entity, and how."""
from __future__ import annotations

from collections import defaultdict, deque

from ..domain import PlanInput


class DependencyGraph:
    def __init__(self, plan: PlanInput):
        self.edges: dict[str, set[str]] = defaultdict(set)  # upstream -> downstream
        self.label: dict[str, str] = {}
        sku_parts: dict[str, set[str]] = defaultdict(set)
        for s in plan.skus:
            for line in s.bom:
                for p in line.allowed_parts:
                    self._e(f"part:{p}", f"sku:{s.id}")
                    sku_parts[s.id].add(p)
        for r in plan.routes:
            self._e(f"route:{r.id}", f"sku:{r.sku_id}")
            for st in r.steps:
                self._e(f"resource:{st.resource_id}", f"route:{r.id}")
        for res in plan.resources:
            self._e(f"site:{res.site_id}", f"resource:{res.id}")
        for p in plan.parts:
            if p.supplier_id:
                self._e(f"supplier:{p.supplier_id}", f"part:{p.id}")
        for o in plan.orders:
            self._e(f"sku:{o.sku_id}", f"order:{o.id}")
            self._e(f"lane:{o.destination}", f"order:{o.id}")
            self._e("compliance:rules", f"order:{o.id}")
        for lot in plan.inventory:
            self._e(f"lot:{lot.id}", f"part:{lot.part_id}")
        for c in plan.commitments:
            self._e(f"commitment:{c.id}", f"part:{c.part_id}")
        self._plan = plan

    def _e(self, a: str, b: str) -> None:
        self.edges[a].add(b)

    def downstream_orders(self, nodes: list[str]) -> dict[str, list[str]]:
        """order_id -> one shortest dependency path from any of `nodes`."""
        out: dict[str, list[str]] = {}
        for start in nodes:
            seen = {start}
            q = deque([(start, [start])])
            while q:
                n, path = q.popleft()
                if n.startswith("order:"):
                    oid = n[6:]
                    if oid not in out or len(path) < len(out[oid]):
                        out[oid] = path
                    continue
                for nxt in sorted(self.edges.get(n, ())):
                    if nxt not in seen:
                        seen.add(nxt)
                        q.append((nxt, path + [nxt]))
        return out


SUBJECT_NODE = {
    "resource": "resource:{}", "part": "part:{}", "route": "route:{}", "lane": "lane:{}",
    "order": "order:{}", "compliance": "compliance:rules",
}


def subject_nodes(subjects: list[tuple[str, str]]) -> list[str]:
    nodes = []
    for kind, ident in sorted(set(subjects)):
        tpl = SUBJECT_NODE.get(kind)
        if tpl:
            nodes.append(tpl.format(ident))
    return nodes
