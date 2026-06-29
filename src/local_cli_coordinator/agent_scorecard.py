"""Local agent scorecards and routing hints for Phase 7."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import AgentConfig, CoordinatorConfig, iter_agents_by_role


@dataclass(frozen=True)
class AgentScorecard:
    agent_id: str
    role: str
    successes: int
    failures: int
    timeouts: int
    cancellations: int
    avg_runtime_seconds: float | None
    last_success_at: str | None
    last_failure_at: str | None
    cooldown_until: str | None = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _row_to_scorecard(row: sqlite3.Row | None, *, agent_id: str, role: str) -> AgentScorecard:
    if row is None:
        return AgentScorecard(
            agent_id=agent_id,
            role=role,
            successes=0,
            failures=0,
            timeouts=0,
            cancellations=0,
            avg_runtime_seconds=None,
            last_success_at=None,
            last_failure_at=None,
            cooldown_until=None,
        )
    return AgentScorecard(
        agent_id=str(row["agent_id"]),
        role=str(row["role"]),
        successes=int(row["successes"]),
        failures=int(row["failures"]),
        timeouts=int(row["timeouts"]),
        cancellations=int(row["cancellations"]),
        avg_runtime_seconds=row["avg_runtime_seconds"],
        last_success_at=row["last_success_at"],
        last_failure_at=row["last_failure_at"],
        cooldown_until=row["cooldown_until"],
    )


def get_agent_scorecard(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    role: str = "worker",
) -> AgentScorecard:
    row = conn.execute(
        "select * from agent_scorecards where agent_id = ?",
        (agent_id,),
    ).fetchone()
    return _row_to_scorecard(row, agent_id=agent_id, role=role)


def is_agent_available(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    now: datetime | None = None,
) -> bool:
    row = conn.execute(
        "select cooldown_until from agent_scorecards where agent_id = ?",
        (agent_id,),
    ).fetchone()
    if row is None or not row["cooldown_until"]:
        return True
    cooldown = _parse_iso(str(row["cooldown_until"]))
    if cooldown is None:
        return True
    current = now or datetime.now(timezone.utc)
    return current >= cooldown


def set_agent_cooldown(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    cooldown_until: str,
    role: str = "worker",
    commit: bool = True,
) -> None:
    existing = conn.execute(
        "select agent_id from agent_scorecards where agent_id = ?",
        (agent_id,),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            insert into agent_scorecards(agent_id, role, cooldown_until)
            values (?, ?, ?)
            """,
            (agent_id, role, cooldown_until),
        )
    else:
        conn.execute(
            "update agent_scorecards set cooldown_until = ? where agent_id = ?",
            (cooldown_until, agent_id),
        )
    if commit:
        conn.commit()


def record_agent_outcome(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    role: str,
    outcome: str,
    runtime_seconds: float | None = None,
    commit: bool = True,
) -> None:
    """Update scorecard counters for one worker outcome."""
    now = _iso_now()
    row = conn.execute(
        "select * from agent_scorecards where agent_id = ?",
        (agent_id,),
    ).fetchone()
    if row is None:
        conn.execute(
            """
            insert into agent_scorecards(
                agent_id, role, successes, failures, timeouts, cancellations,
                avg_runtime_seconds, last_success_at, last_failure_at
            ) values (?, ?, 0, 0, 0, 0, ?, ?, ?)
            """,
            (agent_id, role, runtime_seconds, None, None),
        )
        row = conn.execute(
            "select * from agent_scorecards where agent_id = ?",
            (agent_id,),
        ).fetchone()

    successes = int(row["successes"])
    failures = int(row["failures"])
    timeouts = int(row["timeouts"])
    cancellations = int(row["cancellations"])
    avg_runtime = row["avg_runtime_seconds"]
    last_success = row["last_success_at"]
    last_failure = row["last_failure_at"]

    if outcome == "success":
        successes += 1
        last_success = now
        if runtime_seconds is not None:
            if avg_runtime is None:
                avg_runtime = runtime_seconds
            else:
                total = successes + failures + timeouts + cancellations
                avg_runtime = (
                    (float(avg_runtime) * (total - 1) + runtime_seconds) / total
                )
    elif outcome == "failure":
        failures += 1
        last_failure = now
    elif outcome == "timeout":
        timeouts += 1
        last_failure = now
    elif outcome == "cancellation":
        cancellations += 1
        last_failure = now

    conn.execute(
        """
        update agent_scorecards
        set successes = ?, failures = ?, timeouts = ?, cancellations = ?,
            avg_runtime_seconds = ?, last_success_at = ?, last_failure_at = ?
        where agent_id = ?
        """,
        (
            successes,
            failures,
            timeouts,
            cancellations,
            avg_runtime,
            last_success,
            last_failure,
            agent_id,
        ),
    )
    if commit:
        conn.commit()


def _agent_score(conn: sqlite3.Connection, agent_id: str) -> int:
    card = get_agent_scorecard(conn, agent_id=agent_id)
    return card.successes - card.failures - card.timeouts


def rank_workers_for_capabilities(
    config: CoordinatorConfig,
    conn: sqlite3.Connection,
    *,
    capabilities: list[str],
) -> list[str]:
    """Return capable worker ids in preferred order with deterministic ties."""
    candidates: list[tuple[int, int, str]] = []
    for index, agent in enumerate(
        iter_agents_by_role(config, "worker", capabilities)
    ):
        if not is_agent_available(conn, agent_id=agent.id):
            continue
        score = _agent_score(conn, agent.id)
        candidates.append((-score, index, agent.id))
    candidates.sort()
    return [agent_id for _, _, agent_id in candidates]