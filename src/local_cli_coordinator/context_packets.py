"""Bounded, redacted context packets for Commander and workers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .project_brain import (
    get_latest_snapshot,
    list_brain_cards,
    list_memories_for_context,
    persist_context_packet,
    redact_text,
)
from .project_indexer import index_repository

CARD_PRIORITY = {
    "overview": 0,
    "entrypoint": 1,
    "component": 2,
    "config": 3,
    "test": 4,
    "command": 5,
    "migration": 6,
    "hazard": 7,
    "workflow": 8,
}
MEMORY_PRIORITY = {
    "verification": 1,
    "decision": 2,
    "success": 3,
    "hazard": 4,
    "failure": 5,
    "review_blocker": 6,
}


class ContextPacketBudgetError(RuntimeError):
    """Raised when core context cannot fit within the token budget."""


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _card_payload(card: Any) -> dict[str, Any]:
    return {
        "card_type": card.card_type,
        "title": card.title,
        "summary": card.summary,
        "citations": card.citations,
        "confidence": card.confidence,
    }


def _memory_payload(memory: Any) -> dict[str, Any]:
    return {
        "memory_type": memory.memory_type,
        "title": memory.title,
        "summary": memory.summary,
    }


def _stale_warning(
    snapshot: Any | None, live_head: str, live_dirty: bool
) -> str:
    if snapshot is None:
        return ""
    if live_dirty or snapshot.git_head != live_head:
        return "[STALE/DIRTY CONTEXT] Repository changed since last brain snapshot."
    return ""


def build_context_packet(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    purpose: str,
    token_budget: int,
    query: str = "",
    repo_path: Path | None = None,
    task_id: str | None = None,
    required_summary: str = "",
) -> dict[str, Any]:
    snapshot = get_latest_snapshot(conn, project_id=project_id)
    stale = ""
    if repo_path is not None:
        live = index_repository(repo_path.resolve())
        stale = _stale_warning(snapshot, live.git_head, live.git_dirty)

    cards = list_brain_cards(conn, project_id=project_id)
    cards_sorted = sorted(
        cards,
        key=lambda c: CARD_PRIORITY.get(c.card_type, 99),
    )
    memories = list_memories_for_context(
        conn, project_id=project_id, purpose=purpose
    )
    memories_sorted = sorted(
        memories,
        key=lambda m: MEMORY_PRIORITY.get(m.memory_type, 99),
    )

    summary = redact_text(required_summary or query or "Project context")
    if stale:
        summary = f"{stale}\n{summary}"

    redaction_count = 0
    selected_cards: list[dict[str, Any]] = []
    selected_memories: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    pruned_cards = 0
    pruned_memories = 0

    def total_tokens() -> int:
        blob = json.dumps({
            "summary": summary,
            "cards": selected_cards,
            "memories": selected_memories,
            "citations": citations,
        })
        return _estimate_tokens(blob)

    for card in cards_sorted:
        payload = _card_payload(card)
        selected_cards.append(payload)
        citations.extend(card.citations)
        while total_tokens() > token_budget and len(selected_cards) > 1:
            selected_cards.pop(0)
            pruned_cards += 1

    for memory in memories_sorted:
        payload = _memory_payload(memory)
        selected_memories.append(payload)
        while total_tokens() > token_budget and len(selected_memories) > 0:
            selected_memories.pop(0)
            pruned_memories += 1

    while total_tokens() > token_budget and selected_cards:
        selected_cards.pop(0)
        pruned_cards += 1

    if total_tokens() > token_budget:
        raise ContextPacketBudgetError(
            f"core context exceeds token budget {token_budget}"
        )

    blob = json.dumps({"summary": summary, "cards": selected_cards})
    if "[REDACTED]" in blob:
        redaction_count += blob.count("[REDACTED]")

    packet: dict[str, Any] = {
        "project_id": project_id,
        "purpose": purpose,
        "token_budget": token_budget,
        "summary": summary,
        "cards": selected_cards,
        "citations": citations[:20],
        "memories": selected_memories,
        "redactions": {"count": redaction_count, "patterns": ["secret", "token"]},
        "pruned": {"cards": pruned_cards, "memories": pruned_memories},
    }
    if stale:
        packet["stale_warning"] = stale
    return packet


def build_and_persist_context_packet(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    purpose: str,
    token_budget: int,
    repo_path: Path | None = None,
    task_id: str | None = None,
    query: str = "",
) -> tuple[dict[str, Any], str]:
    packet = build_context_packet(
        conn,
        project_id=project_id,
        purpose=purpose,
        token_budget=token_budget,
        query=query,
        repo_path=repo_path,
        task_id=task_id,
    )
    saved = persist_context_packet(
        conn,
        project_id=project_id,
        purpose=purpose,
        token_budget=token_budget,
        packet=packet,
        task_id=task_id,
    )
    packet["packet_id"] = saved.id
    return packet, saved.id