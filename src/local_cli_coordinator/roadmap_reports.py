"""Shared roadmap status reports for CLI, RPC, and TUI."""

from __future__ import annotations

import sqlite3
from typing import Any

from .roadmap_graph import (
    compute_roadmap_hash,
    list_roadmap_edges,
    list_roadmap_nodes,
    roadmap_graph_enabled,
)
from .roadmap_readiness import select_next_best_work


def build_roadmap_status_report(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> dict[str, Any]:
    nodes = list_roadmap_nodes(conn, project_id=project_id, limit=500)
    edges = list_roadmap_edges(conn, project_id=project_id)
    next_work = select_next_best_work(conn, project_id=project_id, limit=5)
    status_counts: dict[str, int] = {}
    for node in nodes:
        status_counts[node.status] = status_counts.get(node.status, 0) + 1
    return {
        "project_id": project_id,
        "graph_enabled": roadmap_graph_enabled(conn, project_id),
        "graph_hash": compute_roadmap_hash(conn, project_id=project_id),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "status_counts": status_counts,
        "ready_count": next_work["ready_count"],
        "blocked_count": next_work["blocked_count"],
        "next_items": next_work["items"],
    }


def build_roadmap_blocked_report(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    limit: int = 20,
) -> dict[str, Any]:
    from .roadmap_readiness import evaluate_node_readiness

    items: list[dict[str, Any]] = []
    for node in list_roadmap_nodes(conn, project_id=project_id, limit=500):
        readiness = evaluate_node_readiness(conn, project_id=project_id, node_id=node.id)
        if readiness["status"] != "blocked":
            continue
        items.append(
            {
                "node_id": node.id,
                "title": node.title,
                "node_type": node.node_type,
                "reason": readiness.get("reason", ""),
                "blockers": readiness.get("blockers") or [],
                "cycle": bool(readiness.get("cycle")),
            }
        )
        if len(items) >= limit:
            break
    return {"project_id": project_id, "items": items, "count": len(items)}