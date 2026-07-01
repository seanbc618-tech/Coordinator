"""Bounded capability-safe agent fallback graph."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import AgentConfig, CoordinatorConfig, iter_agents_by_role

DEFAULT_MAX_HOPS = 1


@dataclass(frozen=True)
class FallbackEdge:
    id: str
    from_agent_id: str
    to_agent_id: str
    capability_filter: tuple[str, ...]
    max_hops: int
    enabled: bool
    created_at: str


@dataclass(frozen=True)
class FallbackResult:
    agent_id: str | None
    reason: str
    hops_used: int


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row_to_edge(row: sqlite3.Row) -> FallbackEdge:
    caps = json.loads(row["capability_filter_json"])
    return FallbackEdge(
        id=str(row["id"]),
        from_agent_id=str(row["from_agent_id"]),
        to_agent_id=str(row["to_agent_id"]),
        capability_filter=tuple(str(item) for item in caps),
        max_hops=int(row["max_hops"]),
        enabled=bool(row["enabled"]),
        created_at=str(row["created_at"]),
    )


def record_fallback_edge(
    conn: sqlite3.Connection,
    *,
    from_agent_id: str,
    to_agent_id: str,
    capability_filter: list[str] | None = None,
    max_hops: int = DEFAULT_MAX_HOPS,
    enabled: bool = True,
    commit: bool = True,
) -> str:
    edge_id = str(uuid.uuid4())
    conn.execute(
        """
        insert into agent_fallback_edges(
            id, from_agent_id, to_agent_id, capability_filter_json,
            max_hops, enabled, created_at
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            edge_id,
            from_agent_id,
            to_agent_id,
            json.dumps(capability_filter or []),
            int(max_hops),
            1 if enabled else 0,
            _iso_now(),
        ),
    )
    if commit:
        conn.commit()
    return edge_id


def list_fallback_edges(
    conn: sqlite3.Connection,
    *,
    from_agent_id: str | None = None,
) -> list[FallbackEdge]:
    if from_agent_id is None:
        rows = conn.execute(
            "select * from agent_fallback_edges order by from_agent_id, to_agent_id"
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select * from agent_fallback_edges
            where from_agent_id = ?
            order by to_agent_id
            """,
            (from_agent_id,),
        ).fetchall()
    return [_row_to_edge(row) for row in rows]


def sync_fallback_edges_from_config(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
) -> int:
    """Seed fallback edges from configured agent fallback_agents lists."""
    created = 0
    existing = {
        (edge.from_agent_id, edge.to_agent_id)
        for edge in list_fallback_edges(conn)
    }
    for agent_id, agent in config.agents.items():
        for fallback_id in agent.fallback_agents:
            key = (agent_id, fallback_id)
            if key in existing:
                continue
            record_fallback_edge(
                conn,
                from_agent_id=agent_id,
                to_agent_id=fallback_id,
                capability_filter=[],
                max_hops=DEFAULT_MAX_HOPS,
                commit=False,
            )
            existing.add(key)
            created += 1
    conn.commit()
    return created


def _edge_matches_capabilities(
    edge: FallbackEdge,
    required_capabilities: list[str],
) -> bool:
    if not edge.capability_filter:
        return True
    return set(edge.capability_filter).issubset(set(required_capabilities))


def _eligible_target(
    config: CoordinatorConfig,
    *,
    agent_id: str,
    required_capabilities: list[str],
    unavailable_ids: set[str],
    disabled_ids: set[str],
) -> AgentConfig | None:
    if agent_id in unavailable_ids or agent_id in disabled_ids:
        return None
    agent = config.agents.get(agent_id)
    if agent is None or agent.role != "worker":
        return None
    if not set(required_capabilities).issubset(set(agent.capabilities)):
        return None
    return agent


def find_fallback_agent(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    *,
    from_agent_id: str,
    required_capabilities: list[str],
    unavailable_ids: set[str] | None = None,
    disabled_ids: set[str] | None = None,
    visited: set[str] | None = None,
    hops_remaining: int | None = None,
) -> FallbackResult:
    """Return the next capability-safe fallback agent within hop limits."""
    unavailable = set(unavailable_ids or set())
    disabled = set(disabled_ids or set())
    seen = set(visited or set())
    seen.add(from_agent_id)

    if hops_remaining is not None and hops_remaining <= 0:
        return FallbackResult(
            agent_id=None,
            reason="fallback hop limit reached",
            hops_used=0,
        )

    edges = [
        edge
        for edge in list_fallback_edges(conn, from_agent_id=from_agent_id)
        if edge.enabled and _edge_matches_capabilities(edge, required_capabilities)
    ]
    if not edges:
        primary = config.agents.get(from_agent_id)
        if primary is not None:
            for fallback_id in primary.fallback_agents:
                if fallback_id not in {edge.to_agent_id for edge in edges}:
                    edges.append(
                        FallbackEdge(
                            id="config",
                            from_agent_id=from_agent_id,
                            to_agent_id=fallback_id,
                            capability_filter=(),
                            max_hops=DEFAULT_MAX_HOPS,
                            enabled=True,
                            created_at="",
                        )
                    )

    if not edges:
        return FallbackResult(
            agent_id=None,
            reason="no fallback edges configured",
            hops_used=0,
        )

    max_hops = hops_remaining
    if max_hops is None:
        max_hops = max(edge.max_hops for edge in edges)
    if max_hops <= 0:
        return FallbackResult(
            agent_id=None,
            reason="fallback hop limit reached",
            hops_used=0,
        )

    for edge in edges:
        if edge.to_agent_id in seen:
            continue
        target = _eligible_target(
            config,
            agent_id=edge.to_agent_id,
            required_capabilities=required_capabilities,
            unavailable_ids=unavailable,
            disabled_ids=disabled,
        )
        if target is not None:
            return FallbackResult(
                agent_id=target.id,
                reason=f"fallback from {from_agent_id} to {target.id}",
                hops_used=1,
            )
        nested = find_fallback_agent(
            conn,
            config,
            from_agent_id=edge.to_agent_id,
            required_capabilities=required_capabilities,
            unavailable_ids=unavailable,
            disabled_ids=disabled,
            visited=seen,
            hops_remaining=max_hops - 1,
        )
        if nested.agent_id is not None:
            return FallbackResult(
                agent_id=nested.agent_id,
                reason=(
                    f"fallback from {from_agent_id} via {edge.to_agent_id} "
                    f"to {nested.agent_id}"
                ),
                hops_used=1 + nested.hops_used,
            )

    return FallbackResult(
        agent_id=None,
        reason="all fallback candidates degraded or ineligible",
        hops_used=0,
    )