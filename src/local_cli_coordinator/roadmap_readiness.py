"""Readiness evaluation and next-best-work selection for roadmap nodes."""

from __future__ import annotations

import sqlite3
from typing import Any

from .roadmap_graph import (
    BLOCKING_RELATIONS,
    RoadmapNode,
    get_roadmap_node,
    list_roadmap_nodes,
)

_DONE_MILESTONE = frozenset({"completed"})
_DONE_BACKLOG = frozenset({"admitted", "done"})
_DONE_TASK = frozenset({"done"})
_OPEN_NODE = frozenset({"open", "ready"})


def _source_row_exists(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    node: RoadmapNode,
) -> tuple[bool, str | None]:
    if node.node_type == "external":
        return True, None
    if not node.ref_table or not node.ref_id:
        return node.node_type == "external", None

    if node.ref_table == "project_milestones":
        row = conn.execute(
            "select id, project_id, status from project_milestones where id = ?",
            (int(node.ref_id),),
        ).fetchone()
    elif node.ref_table == "project_backlog_items":
        row = conn.execute(
            "select id, project_id, status from project_backlog_items where id = ?",
            (node.ref_id,),
        ).fetchone()
    elif node.ref_table == "tasks":
        row = conn.execute(
            "select id, project_id, state from tasks where id = ?",
            (node.ref_id,),
        ).fetchone()
    elif node.ref_table == "artifacts":
        row = conn.execute(
            "select id, project_id, sha256, path from artifacts where id = ?",
            (node.ref_id,),
        ).fetchone()
    else:
        return False, None

    if row is None:
        return False, None
    if str(row["project_id"]) != project_id:
        return False, None
    return True, str(row["id"])


def _source_is_done(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    node: RoadmapNode,
) -> bool:
    exists, _ = _source_row_exists(conn, project_id=project_id, node=node)
    if not exists:
        return False
    if node.node_type == "external":
        return node.status == "done"
    if not node.ref_table or not node.ref_id:
        return node.status == "done"

    if node.ref_table == "project_milestones":
        row = conn.execute(
            "select status from project_milestones where id = ? and project_id = ?",
            (int(node.ref_id), project_id),
        ).fetchone()
        return row is not None and str(row["status"]) in _DONE_MILESTONE
    if node.ref_table == "project_backlog_items":
        row = conn.execute(
            "select status from project_backlog_items where id = ? and project_id = ?",
            (node.ref_id, project_id),
        ).fetchone()
        return row is not None and str(row["status"]) in _DONE_BACKLOG
    if node.ref_table == "tasks":
        row = conn.execute(
            "select state from tasks where id = ? and project_id = ?",
            (node.ref_id, project_id),
        ).fetchone()
        return row is not None and str(row["state"]) in _DONE_TASK
    if node.ref_table == "artifacts":
        row = conn.execute(
            "select path from artifacts where id = ? and project_id = ?",
            (node.ref_id, project_id),
        ).fetchone()
        if row is None:
            return False
        from pathlib import Path

        return Path(str(row["path"])).is_file()
    return False


def _incoming_blockers(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    node_id: str,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        select e.*, n.title as from_title, n.status as from_status
        from roadmap_edges e
        join roadmap_nodes n on n.id = e.from_node_id
        where e.project_id = ?
          and e.to_node_id = ?
          and e.relation in ('blocks', 'depends_on')
          and e.status = 'active'
        """,
        (project_id, node_id),
    ).fetchall()


def _blocker_satisfied(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    from_node_id: str,
) -> bool:
    node = get_roadmap_node(conn, project_id=project_id, node_id=from_node_id)
    if node is None:
        return False
    if node.status in {"done", "cancelled"}:
        return True
    return _source_is_done(conn, project_id=project_id, node=node)


def evaluate_node_readiness(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    node_id: str,
) -> dict[str, Any]:
    node = get_roadmap_node(conn, project_id=project_id, node_id=node_id)
    if node is None:
        return {"node_id": node_id, "status": "stale", "reason": "node missing", "blockers": []}

    exists, _ = _source_row_exists(conn, project_id=project_id, node=node)
    if node.node_type != "external" and node.ref_table and not exists:
        return {
            "node_id": node_id,
            "status": "stale",
            "reason": "linked source row missing or foreign project",
            "blockers": [],
        }

    if _source_is_done(conn, project_id=project_id, node=node):
        return {"node_id": node_id, "status": "done", "reason": "source complete", "blockers": []}

    if node.status in {"cancelled", "stale"}:
        return {"node_id": node_id, "status": node.status, "reason": f"node {node.status}", "blockers": []}

    visited: set[str] = set()
    blockers: list[dict[str, Any]] = []
    cycle_detected = False

    def walk(current_id: str) -> None:
        nonlocal cycle_detected
        if current_id in visited:
            cycle_detected = True
            return
        visited.add(current_id)
        for edge in _incoming_blockers(conn, project_id=project_id, node_id=current_id):
            from_id = str(edge["from_node_id"])
            if not _blocker_satisfied(conn, project_id=project_id, from_node_id=from_id):
                blockers.append(
                    {
                        "node_id": from_id,
                        "title": str(edge["from_title"]),
                        "relation": str(edge["relation"]),
                    }
                )
                walk(from_id)

    walk(node_id)

    if cycle_detected:
        return {
            "node_id": node_id,
            "status": "blocked",
            "reason": "dependency cycle detected",
            "blockers": blockers,
            "cycle": True,
        }

    if blockers:
        return {
            "node_id": node_id,
            "status": "blocked",
            "reason": "active blockers remain",
            "blockers": blockers,
        }

    if node.node_type == "backlog" and node.ref_id:
        row = conn.execute(
            "select status from project_backlog_items where id = ? and project_id = ?",
            (node.ref_id, project_id),
        ).fetchone()
        if row is not None and str(row["status"]) not in {"candidate", "ready"}:
            return {
                "node_id": node_id,
                "status": "blocked",
                "reason": f"backlog status {row['status']}",
                "blockers": [],
            }

    if node.status in _OPEN_NODE or node.status == "blocked":
        return {"node_id": node_id, "status": "ready", "reason": "unblocked", "blockers": []}

    return {"node_id": node_id, "status": node.status, "reason": "not actionable", "blockers": []}


def explain_blockers(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    node_id: str,
) -> dict[str, Any]:
    readiness = evaluate_node_readiness(conn, project_id=project_id, node_id=node_id)
    node = get_roadmap_node(conn, project_id=project_id, node_id=node_id)
    return {
        "project_id": project_id,
        "node_id": node_id,
        "title": node.title if node else "",
        "status": readiness["status"],
        "reason": readiness.get("reason", ""),
        "blockers": readiness.get("blockers") or [],
        "cycle": bool(readiness.get("cycle")),
    }


def list_ready_roadmap_items(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    nodes = list_roadmap_nodes(conn, project_id=project_id, limit=500)
    ready: list[dict[str, Any]] = []
    for node in nodes:
        readiness = evaluate_node_readiness(conn, project_id=project_id, node_id=node.id)
        if readiness["status"] != "ready":
            continue
        ready.append(
            {
                "node_id": node.id,
                "node_type": node.node_type,
                "title": node.title,
                "priority": node.priority,
                "reason": readiness.get("reason", ""),
                "source": (
                    {"table": node.ref_table, "id": node.ref_id}
                    if node.ref_table and node.ref_id
                    else None
                ),
            }
        )
    ready.sort(key=lambda item: (-int(item["priority"]), item["node_id"]))
    return ready[: max(1, limit)]


def select_next_best_work(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    limit: int = 5,
) -> dict[str, Any]:
    nodes = list_roadmap_nodes(conn, project_id=project_id, limit=500)
    ready_items: list[dict[str, Any]] = []
    blocked_count = 0
    ready_count = 0
    for node in nodes:
        readiness = evaluate_node_readiness(conn, project_id=project_id, node_id=node.id)
        if readiness["status"] == "ready":
            ready_count += 1
            ready_items.append(
                {
                    "node_id": node.id,
                    "node_type": node.node_type,
                    "title": node.title,
                    "priority": node.priority,
                    "reason": readiness.get("reason", "unblocked"),
                    "source": (
                        {"table": node.ref_table, "id": node.ref_id}
                        if node.ref_table and node.ref_id
                        else None
                    ),
                }
            )
        elif readiness["status"] == "blocked":
            blocked_count += 1

    ready_items.sort(
        key=lambda item: (-int(item["priority"]), item["node_id"]),
    )
    return {
        "project_id": project_id,
        "items": ready_items[: max(1, limit)],
        "blocked_count": blocked_count,
        "ready_count": ready_count,
    }


def backlog_item_roadmap_ready(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    backlog_id: str,
) -> tuple[bool, str | None]:
    from .roadmap_graph import find_node_for_ref, roadmap_graph_enabled

    if not roadmap_graph_enabled(conn, project_id):
        return True, None
    node = find_node_for_ref(
        conn,
        project_id=project_id,
        node_type="backlog",
        ref_table="project_backlog_items",
        ref_id=backlog_id,
    )
    if node is None:
        return True, None
    readiness = evaluate_node_readiness(conn, project_id=project_id, node_id=node.id)
    return readiness["status"] == "ready", node.id