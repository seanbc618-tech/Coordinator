"""Project brain: durable snapshots, cards, memories, and context packets."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

VALID_CARD_TYPES = frozenset({
    "overview",
    "component",
    "test",
    "command",
    "config",
    "entrypoint",
    "migration",
    "hazard",
    "workflow",
})
VALID_MEMORY_TYPES = frozenset({
    "failure",
    "success",
    "review_blocker",
    "hazard",
    "decision",
    "verification",
})
VALID_MEMORY_STATUS = frozenset({"active", "inactive", "resolved"})
VALID_PURPOSES = frozenset({
    "commander_chat",
    "task_prompt",
    "review",
    "impact",
    "user_query",
})

_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|secret|password|token)\s*[=:]\s*)(\S+)"
)


@dataclass(frozen=True)
class BrainSnapshot:
    id: str
    project_id: str
    repo_path: str
    git_head: str
    git_dirty: bool
    status: str
    summary: str
    file_count: int
    indexed_at: str
    updated_at: str


@dataclass(frozen=True)
class BrainCard:
    id: str
    project_id: str
    snapshot_id: str
    card_type: str
    title: str
    summary: str
    data: dict[str, Any]
    citations: list[dict[str, Any]]
    confidence: float
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class BrainMemory:
    id: str
    project_id: str
    source_type: str
    source_id: str
    memory_type: str
    title: str
    summary: str
    status: str
    data: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class ContextPacketRecord:
    id: str
    project_id: str
    purpose: str
    token_budget: int
    task_id: str | None
    goal_id: int | None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _validate_enum(value: str, allowed: frozenset[str], field: str) -> str:
    text = value.strip()
    if text not in allowed:
        raise ValueError(f"invalid {field}: {value!r}")
    return text


def redact_text(text: str) -> str:
    return _SECRET_RE.sub(r"\1[REDACTED]", text)


def _row_snapshot(row: sqlite3.Row) -> BrainSnapshot:
    return BrainSnapshot(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        repo_path=str(row["repo_path"]),
        git_head=str(row["git_head"]),
        git_dirty=bool(row["git_dirty"]),
        status=str(row["status"]),
        summary=redact_text(str(row["summary"])),
        file_count=int(row["file_count"]),
        indexed_at=str(row["indexed_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_card(row: sqlite3.Row) -> BrainCard:
    return BrainCard(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        snapshot_id=str(row["snapshot_id"]),
        card_type=str(row["card_type"]),
        title=redact_text(str(row["title"])),
        summary=redact_text(str(row["summary"])),
        data=json.loads(row["data_json"]),
        citations=json.loads(row["citations_json"]),
        confidence=float(row["confidence"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_memory(row: sqlite3.Row) -> BrainMemory:
    return BrainMemory(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        source_type=str(row["source_type"]),
        source_id=str(row["source_id"]),
        memory_type=str(row["memory_type"]),
        title=redact_text(str(row["title"])),
        summary=redact_text(str(row["summary"])),
        status=str(row["status"]),
        data=json.loads(row["data_json"]),
        created_at=str(row["created_at"]),
    )


def get_latest_snapshot(
    conn: sqlite3.Connection, *, project_id: str
) -> BrainSnapshot | None:
    row = conn.execute(
        """
        select * from project_brain_snapshots
        where project_id = ?
        order by updated_at desc
        limit 1
        """,
        (project_id,),
    ).fetchone()
    return _row_snapshot(row) if row else None


def create_brain_snapshot(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    repo_path: Path,
    commit: bool = False,
) -> BrainSnapshot:
    from .project_indexer import index_repository

    repo_path = repo_path.resolve()
    index = index_repository(repo_path)
    now = _iso_now()
    snapshot_id = _new_id("pbsnap")
    summary = redact_text(f"Indexed {index.file_count} files at {index.git_head[:8]}")
    conn.execute(
        """
        insert into project_brain_snapshots(
            id, project_id, repo_path, git_head, git_dirty, status, summary,
            file_count, indexed_at, updated_at
        ) values (?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            project_id,
            str(repo_path),
            index.git_head,
            1 if index.git_dirty else 0,
            summary,
            index.file_count,
            now,
            now,
        ),
    )
    from .project_indexer import generate_brain_cards_from_index

    cards = generate_brain_cards_from_index(index)
    for card in cards:
        upsert_brain_card(
            conn,
            project_id=project_id,
            snapshot_id=snapshot_id,
            card_type=card["card_type"],
            title=card["title"],
            summary=card["summary"],
            citations=card.get("citations", []),
            confidence=card.get("confidence", 0.5),
            data=card.get("data", {}),
        )
    if commit:
        conn.commit()
    row = conn.execute(
        "select * from project_brain_snapshots where id = ?",
        (snapshot_id,),
    ).fetchone()
    assert row is not None
    return _row_snapshot(row)


def upsert_brain_card(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    snapshot_id: str,
    card_type: str,
    title: str,
    summary: str,
    citations: list[dict[str, Any]] | None = None,
    confidence: float = 0.5,
    data: Mapping[str, Any] | None = None,
    commit: bool = False,
) -> BrainCard:
    _validate_enum(card_type, VALID_CARD_TYPES, "card_type")
    now = _iso_now()
    title_red = redact_text(title)
    summary_red = redact_text(summary)
    citations_json = json.dumps(citations or [])
    data_json = json.dumps(dict(data or {}))
    existing = conn.execute(
        """
        select id from project_brain_cards
        where project_id = ? and snapshot_id = ? and card_type = ? and title = ?
        """,
        (project_id, snapshot_id, card_type, title_red),
    ).fetchone()
    if existing:
        card_id = str(existing["id"])
        conn.execute(
            """
            update project_brain_cards
            set summary = ?, data_json = ?, citations_json = ?, confidence = ?, updated_at = ?
            where id = ?
            """,
            (summary_red, data_json, citations_json, confidence, now, card_id),
        )
    else:
        card_id = _new_id("pbcard")
        conn.execute(
            """
            insert into project_brain_cards(
                id, project_id, snapshot_id, card_type, title, summary,
                data_json, citations_json, confidence, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                card_id,
                project_id,
                snapshot_id,
                card_type,
                title_red,
                summary_red,
                data_json,
                citations_json,
                confidence,
                now,
                now,
            ),
        )
    if commit:
        conn.commit()
    row = conn.execute(
        "select * from project_brain_cards where id = ?",
        (card_id,),
    ).fetchone()
    assert row is not None
    return _row_card(row)


def list_brain_cards(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    snapshot_id: str | None = None,
    card_type: str | None = None,
) -> list[BrainCard]:
    query = "select * from project_brain_cards where project_id = ?"
    params: list[Any] = [project_id]
    if snapshot_id:
        query += " and snapshot_id = ?"
        params.append(snapshot_id)
    if card_type:
        query += " and card_type = ?"
        params.append(card_type)
    query += " order by card_type, title"
    rows = conn.execute(query, params).fetchall()
    return [_row_card(row) for row in rows]


def upsert_brain_memory(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    source_type: str,
    source_id: str,
    memory_type: str,
    title: str,
    summary: str,
    status: str = "active",
    data: Mapping[str, Any] | None = None,
    commit: bool = False,
) -> BrainMemory:
    _validate_enum(memory_type, VALID_MEMORY_TYPES, "memory_type")
    _validate_enum(status, VALID_MEMORY_STATUS, "status")
    now = _iso_now()
    title_red = redact_text(title)
    summary_red = redact_text(summary)
    data_json = json.dumps(dict(data or {}))
    existing = conn.execute(
        """
        select id from project_brain_memories
        where project_id = ? and source_type = ? and source_id = ?
          and memory_type = ? and title = ?
        """,
        (project_id, source_type, source_id, memory_type, title_red),
    ).fetchone()
    if existing:
        mem_id = str(existing["id"])
        conn.execute(
            """
            update project_brain_memories
            set summary = ?, status = ?, data_json = ?
            where id = ?
            """,
            (summary_red, status, data_json, mem_id),
        )
    else:
        mem_id = _new_id("pbmem")
        conn.execute(
            """
            insert into project_brain_memories(
                id, project_id, source_type, source_id, memory_type,
                title, summary, status, data_json, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mem_id,
                project_id,
                source_type,
                source_id,
                memory_type,
                title_red,
                summary_red,
                status,
                data_json,
                now,
            ),
        )
    if commit:
        conn.commit()
    row = conn.execute(
        "select * from project_brain_memories where id = ?",
        (mem_id,),
    ).fetchone()
    assert row is not None
    return _row_memory(row)


def list_memories_for_context(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    purpose: str,
) -> list[BrainMemory]:
    _validate_enum(purpose, VALID_PURPOSES, "purpose")
    rows = conn.execute(
        """
        select * from project_brain_memories
        where project_id = ? and status = 'active'
        order by created_at desc
        """,
        (project_id,),
    ).fetchall()
    return [_row_memory(row) for row in rows]


def persist_context_packet(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    purpose: str,
    token_budget: int,
    packet: Mapping[str, Any],
    task_id: str | None = None,
    goal_id: int | None = None,
    commit: bool = False,
) -> ContextPacketRecord:
    _validate_enum(purpose, VALID_PURPOSES, "purpose")
    now = _iso_now()
    packet_id = _new_id("pkt")
    redactions = packet.get("redactions", {})
    conn.execute(
        """
        insert into project_context_packets(
            id, project_id, task_id, goal_id, purpose, token_budget,
            packet_json, redaction_report_json, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            packet_id,
            project_id,
            task_id,
            goal_id,
            purpose,
            token_budget,
            json.dumps(dict(packet)),
            json.dumps(redactions),
            now,
        ),
    )
    if commit:
        conn.commit()
    return ContextPacketRecord(
        id=packet_id,
        project_id=project_id,
        purpose=purpose,
        token_budget=token_budget,
        task_id=task_id,
        goal_id=goal_id,
    )


def ensure_brain_indexed(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    repo_path: Path,
) -> BrainSnapshot:
    from .project_indexer import index_repository

    repo_path = repo_path.resolve()
    live = index_repository(repo_path)
    latest = get_latest_snapshot(conn, project_id=project_id)
    if (
        latest is None
        or latest.git_head != live.git_head
        or bool(latest.git_dirty) != live.git_dirty
    ):
        return create_brain_snapshot(conn, project_id=project_id, repo_path=repo_path)
    return latest


def learn_from_task_outcome(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> BrainMemory | None:
    row = conn.execute(
        "select id, title, state from tasks where id = ? and project_id = ?",
        (task_id, project_id),
    ).fetchone()
    if row is None:
        return None
    state = str(row["state"])
    title = str(row["title"])
    if state == "failed":
        return upsert_brain_memory(
            conn,
            project_id=project_id,
            source_type="task",
            source_id=task_id,
            memory_type="failure",
            title=f"Task failed: {title}",
            summary=f"Task {task_id} ended in failed state",
            status="active",
        )
    if state in {"done", "verified"}:
        mem = upsert_brain_memory(
            conn,
            project_id=project_id,
            source_type="task",
            source_id=task_id,
            memory_type="success",
            title=f"Task succeeded: {title}",
            summary=f"Task {task_id} completed successfully",
            status="active",
        )
        _deactivate_related_failures(conn, project_id=project_id, task_id=task_id)
        return mem
    return None


def _deactivate_related_failures(
    conn: sqlite3.Connection, *, project_id: str, task_id: str
) -> None:
    conn.execute(
        """
        update project_brain_memories
        set status = 'inactive'
        where project_id = ? and memory_type in ('failure', 'review_blocker')
          and source_id != ? and status = 'active'
        """,
        (project_id, task_id),
    )


def build_brain_payload(
    conn: sqlite3.Connection, *, project_id: str, repo_path: Path
) -> dict[str, Any]:
    snapshot = ensure_brain_indexed(
        conn, project_id=project_id, repo_path=repo_path
    )
    cards = list_brain_cards(conn, project_id=project_id, snapshot_id=snapshot.id)
    return {
        "project_id": project_id,
        "snapshot": {
            "id": snapshot.id,
            "git_head": snapshot.git_head,
            "git_dirty": snapshot.git_dirty,
            "status": snapshot.status,
            "summary": snapshot.summary,
            "file_count": snapshot.file_count,
            "indexed_at": snapshot.indexed_at,
        },
        "card_count": len(cards),
    }


def build_map_payload(
    conn: sqlite3.Connection, *, project_id: str, repo_path: Path
) -> dict[str, Any]:
    snapshot = ensure_brain_indexed(
        conn, project_id=project_id, repo_path=repo_path
    )
    cards = list_brain_cards(conn, project_id=project_id, snapshot_id=snapshot.id)
    return {
        "project_id": project_id,
        "snapshot_id": snapshot.id,
        "cards": [
            {
                "card_type": c.card_type,
                "title": c.title,
                "summary": c.summary,
                "citations": c.citations,
                "confidence": c.confidence,
            }
            for c in cards
        ],
    }