"""Project-scoped strategic dependency graph nodes and edges."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

NODE_TYPES = frozenset({"milestone", "backlog", "task", "evidence", "external"})
NODE_STATUSES = frozenset({"open", "ready", "blocked", "done", "stale", "cancelled"})
EDGE_RELATIONS = frozenset({"blocks", "depends_on", "enables", "evidences"})
EDGE_STATUSES = frozenset({"active", "satisfied", "ignored"})
BLOCKING_RELATIONS = frozenset({"blocks", "depends_on"})
SNAPSHOT_SOURCES = frozenset({"manual", "roadmap_md", "commander", "repair"})


@dataclass(frozen=True)
class RoadmapNode:
    id: str
    project_id: str
    node_type: str
    ref_table: str | None
    ref_id: str | None
    title: str
    status: str
    priority: int
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RoadmapEdge:
    id: str
    project_id: str
    from_node_id: str
    to_node_id: str
    relation: str
    status: str
    rationale: str
    created_at: str
    updated_at: str


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row_to_node(row: sqlite3.Row) -> RoadmapNode:
    return RoadmapNode(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        node_type=str(row["node_type"]),
        ref_table=str(row["ref_table"]) if row["ref_table"] else None,
        ref_id=str(row["ref_id"]) if row["ref_id"] else None,
        title=str(row["title"]),
        status=str(row["status"]),
        priority=int(row["priority"]),
        metadata=json.loads(row["metadata_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_edge(row: sqlite3.Row) -> RoadmapEdge:
    return RoadmapEdge(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        from_node_id=str(row["from_node_id"]),
        to_node_id=str(row["to_node_id"]),
        relation=str(row["relation"]),
        status=str(row["status"]),
        rationale=str(row["rationale"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def roadmap_graph_enabled(conn: sqlite3.Connection, project_id: str) -> bool:
    row = conn.execute(
        "select roadmap_graph_enabled from projects where id = ?",
        (project_id,),
    ).fetchone()
    return bool(row and int(row["roadmap_graph_enabled"]))


def set_roadmap_graph_enabled(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    enabled: bool,
    commit: bool = True,
) -> None:
    conn.execute(
        "update projects set roadmap_graph_enabled = ?, updated_at = current_timestamp where id = ?",
        (1 if enabled else 0, project_id),
    )
    if commit:
        conn.commit()


def get_roadmap_node(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    node_id: str,
) -> RoadmapNode | None:
    row = conn.execute(
        "select * from roadmap_nodes where project_id = ? and id = ?",
        (project_id, node_id),
    ).fetchone()
    return _row_to_node(row) if row else None


def upsert_roadmap_node(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    node_type: str,
    title: str,
    ref_table: str | None = None,
    ref_id: str | None = None,
    priority: int = 50,
    metadata: Mapping[str, Any] | None = None,
    status: str = "open",
    commit: bool = True,
) -> str:
    if node_type not in NODE_TYPES:
        raise ValueError(f"unsupported node_type: {node_type!r}")
    if status not in NODE_STATUSES:
        raise ValueError(f"unsupported node status: {status!r}")

    now = _iso_now()
    meta_json = json.dumps(dict(metadata or {}))
    existing = None
    if ref_table is not None or ref_id is not None:
        existing = conn.execute(
            """
            select id from roadmap_nodes
            where project_id = ? and node_type = ? and ref_table is ? and ref_id is ?
            """,
            (project_id, node_type, ref_table, ref_id),
        ).fetchone()
    if existing is not None:
        node_id = str(existing["id"])
        conn.execute(
            """
            update roadmap_nodes
            set title = ?, priority = ?, metadata_json = ?, updated_at = ?
            where id = ?
            """,
            (title.strip(), priority, meta_json, now, node_id),
        )
    else:
        node_id = f"road-{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            insert into roadmap_nodes(
                id, project_id, node_type, ref_table, ref_id, title, status,
                priority, metadata_json, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node_id,
                project_id,
                node_type,
                ref_table,
                ref_id,
                title.strip(),
                status,
                priority,
                meta_json,
                now,
                now,
            ),
        )
    if commit:
        conn.commit()
    return node_id


def _would_create_cycle(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    from_node_id: str,
    to_node_id: str,
) -> bool:
    if from_node_id == to_node_id:
        return True
    stack = [from_node_id]
    visited: set[str] = set()
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        rows = conn.execute(
            """
            select to_node_id from roadmap_edges
            where project_id = ?
              and from_node_id = ?
              and relation in ('blocks', 'depends_on')
              and status = 'active'
            """,
            (project_id, current),
        ).fetchall()
        for row in rows:
            next_id = str(row["to_node_id"])
            if next_id == to_node_id:
                return True
            if next_id not in visited:
                stack.append(next_id)
    return False


def add_roadmap_edge(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    from_node_id: str,
    to_node_id: str,
    relation: str,
    rationale: str = "",
    status: str = "active",
    commit: bool = True,
) -> str:
    if relation not in EDGE_RELATIONS:
        raise ValueError(f"unsupported edge relation: {relation!r}")
    if status not in EDGE_STATUSES:
        raise ValueError(f"unsupported edge status: {status!r}")

    from_row = conn.execute(
        "select * from roadmap_nodes where id = ?", (from_node_id,)
    ).fetchone()
    to_row = conn.execute(
        "select * from roadmap_nodes where id = ?", (to_node_id,)
    ).fetchone()
    if from_row is None or to_row is None:
        raise ValueError("roadmap node not found")
    from_node = _row_to_node(from_row)
    to_node = _row_to_node(to_row)
    if (
        from_node.project_id != project_id
        or to_node.project_id != project_id
        or from_node.project_id != to_node.project_id
    ):
        raise ValueError("cross_project_dependency_rejected")

    if relation in BLOCKING_RELATIONS and _would_create_cycle(
        conn,
        project_id=project_id,
        from_node_id=to_node_id,
        to_node_id=from_node_id,
    ):
        raise ValueError("roadmap_cycle_rejected")

    now = _iso_now()
    existing = conn.execute(
        """
        select id from roadmap_edges
        where project_id = ? and from_node_id = ? and to_node_id = ? and relation = ?
        """,
        (project_id, from_node_id, to_node_id, relation),
    ).fetchone()
    if existing is not None:
        edge_id = str(existing["id"])
        conn.execute(
            """
            update roadmap_edges
            set rationale = ?, status = ?, updated_at = ?
            where id = ?
            """,
            (rationale, status, now, edge_id),
        )
    else:
        edge_id = f"redge-{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            insert into roadmap_edges(
                id, project_id, from_node_id, to_node_id, relation, status,
                rationale, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge_id,
                project_id,
                from_node_id,
                to_node_id,
                relation,
                status,
                rationale,
                now,
                now,
            ),
        )
    if commit:
        conn.commit()
    return edge_id


def list_roadmap_nodes(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    status: str | None = None,
    limit: int = 100,
) -> list[RoadmapNode]:
    if status is None:
        rows = conn.execute(
            """
            select * from roadmap_nodes
            where project_id = ?
            order by priority desc, created_at asc, id asc
            limit ?
            """,
            (project_id, max(1, limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select * from roadmap_nodes
            where project_id = ? and status = ?
            order by priority desc, created_at asc, id asc
            limit ?
            """,
            (project_id, status, max(1, limit)),
        ).fetchall()
    return [_row_to_node(row) for row in rows]


def list_roadmap_edges(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    node_id: str | None = None,
) -> list[RoadmapEdge]:
    if node_id is None:
        rows = conn.execute(
            "select * from roadmap_edges where project_id = ? order by created_at asc",
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select * from roadmap_edges
            where project_id = ?
              and (from_node_id = ? or to_node_id = ?)
            order by created_at asc
            """,
            (project_id, node_id, node_id),
        ).fetchall()
    return [_row_to_edge(row) for row in rows]


def mark_edge_satisfied(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    edge_id: str,
    commit: bool = True,
) -> None:
    conn.execute(
        """
        update roadmap_edges
        set status = 'satisfied', updated_at = ?
        where project_id = ? and id = ?
        """,
        (_iso_now(), project_id, edge_id),
    )
    if commit:
        conn.commit()


def update_roadmap_node_status(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    node_id: str,
    status: str,
    commit: bool = True,
) -> None:
    if status not in NODE_STATUSES:
        raise ValueError(f"unsupported node status: {status!r}")
    conn.execute(
        """
        update roadmap_nodes
        set status = ?, updated_at = ?
        where project_id = ? and id = ?
        """,
        (status, _iso_now(), project_id, node_id),
    )
    if commit:
        conn.commit()


def find_node_for_ref(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    node_type: str,
    ref_table: str,
    ref_id: str,
) -> RoadmapNode | None:
    row = conn.execute(
        """
        select * from roadmap_nodes
        where project_id = ? and node_type = ? and ref_table = ? and ref_id = ?
        """,
        (project_id, node_type, ref_table, ref_id),
    ).fetchone()
    return _row_to_node(row) if row else None


def compute_roadmap_hash(conn: sqlite3.Connection, *, project_id: str) -> str:
    node_rows = conn.execute(
        """
        select id, status, priority from roadmap_nodes
        where project_id = ?
        order by id asc
        """,
        (project_id,),
    ).fetchall()
    edge_rows = conn.execute(
        """
        select id, status, relation from roadmap_edges
        where project_id = ?
        order by id asc
        """,
        (project_id,),
    ).fetchall()
    payload = json.dumps(
        {
            "nodes": [dict(row) for row in node_rows],
            "edges": [dict(row) for row in edge_rows],
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def record_roadmap_snapshot(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    source: str,
    summary: Mapping[str, Any],
    commit: bool = True,
) -> str:
    if source not in SNAPSHOT_SOURCES:
        raise ValueError(f"unsupported snapshot source: {source!r}")
    snapshot_id = f"rsnap-{uuid.uuid4().hex[:12]}"
    graph_hash = compute_roadmap_hash(conn, project_id=project_id)
    conn.execute(
        """
        insert into roadmap_snapshots(
            id, project_id, source, summary_json, graph_hash, created_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            project_id,
            source,
            json.dumps(dict(summary)),
            graph_hash,
            _iso_now(),
        ),
    )
    if commit:
        conn.commit()
    return snapshot_id