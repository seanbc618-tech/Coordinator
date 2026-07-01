"""Local fixture benchmarks for agent capability routing."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mock_provider import MockProviderError, render_worker_fixture

BENCHMARK_STATUSES = frozenset({"pass", "fail", "timeout", "skipped"})

BLOCKED_PROVIDER_TOKENS = frozenset({
    "codex",
    "grok",
    "claude",
    "gemini",
    "pi",
    "openai",
    "anthropic",
})

_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "worker"

BENCHMARK_FIXTURES: dict[str, dict[str, str]] = {
    "worker_smoke": {
        "fixture_name": "success.json",
        "description": "Deterministic worker success fixture",
    },
    "worker_failure": {
        "fixture_name": "failure.json",
        "description": "Deterministic worker failure fixture",
    },
}


class BenchmarkError(ValueError):
    """Raised when a benchmark invocation is unsafe or invalid."""


@dataclass(frozen=True)
class BenchmarkResult:
    run_id: str
    agent_id: str
    benchmark_name: str
    fixture_name: str
    status: str
    score: float
    duration_seconds: float
    result_json: dict[str, Any]


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_benchmark_status(status: str) -> str:
    if status not in BENCHMARK_STATUSES:
        raise ValueError(f"invalid benchmark status: {status}")
    return status


def assert_benchmark_safe_command(command: str) -> None:
    """Reject benchmarks that would invoke paid/cloud agent CLIs."""
    if not command.strip():
        return
    if "mock-provider" in command:
        return
    first = command.split()[0]
    token = Path(first).name.lower()
    for blocked in BLOCKED_PROVIDER_TOKENS:
        if blocked in token:
            raise BenchmarkError(
                f"benchmark cannot invoke external provider command: {token}"
            )


def resolve_benchmark_fixture(benchmark_name: str) -> Path:
    spec = BENCHMARK_FIXTURES.get(benchmark_name)
    if spec is None:
        raise BenchmarkError(f"unknown benchmark: {benchmark_name}")
    fixture = _FIXTURE_ROOT / spec["fixture_name"]
    if not fixture.is_file():
        raise BenchmarkError(f"benchmark fixture not found: {fixture}")
    return fixture


def record_benchmark_run(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    benchmark_name: str,
    fixture_name: str,
    status: str,
    score: float,
    duration_seconds: float,
    result_json: dict[str, Any] | None = None,
    commit: bool = True,
) -> str:
    run_id = str(uuid.uuid4())
    conn.execute(
        """
        insert into agent_benchmark_runs(
            id, agent_id, benchmark_name, fixture_name, status, score,
            duration_seconds, result_json, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            agent_id,
            benchmark_name,
            fixture_name,
            validate_benchmark_status(status),
            float(score),
            float(duration_seconds),
            json.dumps(result_json or {}),
            _iso_now(),
        ),
    )
    if commit:
        conn.commit()
    return run_id


def get_latest_benchmark_scores(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
) -> dict[str, float]:
    rows = conn.execute(
        """
        select benchmark_name, score
        from agent_benchmark_runs
        where agent_id = ?
        order by created_at desc
        """,
        (agent_id,),
    ).fetchall()
    scores: dict[str, float] = {}
    for row in rows:
        name = str(row["benchmark_name"])
        if name not in scores:
            scores[name] = float(row["score"])
    return scores


def list_benchmark_runs(
    conn: sqlite3.Connection,
    *,
    agent_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if agent_id is None:
        rows = conn.execute(
            """
            select * from agent_benchmark_runs
            order by created_at desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select * from agent_benchmark_runs
            where agent_id = ?
            order by created_at desc
            limit ?
            """,
            (agent_id, limit),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "agent_id": row["agent_id"],
            "benchmark_name": row["benchmark_name"],
            "fixture_name": row["fixture_name"],
            "status": row["status"],
            "score": row["score"],
            "duration_seconds": row["duration_seconds"],
            "result_json": json.loads(row["result_json"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def run_agent_benchmark(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    benchmark_name: str,
    agent_command: str = "",
    commit: bool = True,
) -> BenchmarkResult:
    """Run a local fixture benchmark without calling external providers."""
    assert_benchmark_safe_command(agent_command)
    fixture_path = resolve_benchmark_fixture(benchmark_name)
    started = time.monotonic()
    try:
        rendered = render_worker_fixture(fixture_path)
    except MockProviderError as exc:
        duration = time.monotonic() - started
        run_id = record_benchmark_run(
            conn,
            agent_id=agent_id,
            benchmark_name=benchmark_name,
            fixture_name=fixture_path.name,
            status="fail",
            score=0.0,
            duration_seconds=duration,
            result_json={"error": str(exc)},
            commit=False,
        )
        if commit:
            conn.commit()
        return BenchmarkResult(
            run_id=run_id,
            agent_id=agent_id,
            benchmark_name=benchmark_name,
            fixture_name=fixture_path.name,
            status="fail",
            score=0.0,
            duration_seconds=duration,
            result_json={"error": str(exc)},
        )

    duration = time.monotonic() - started
    exit_code = int(rendered["exit_code"])
    status = "pass" if exit_code == 0 else "fail"
    score = 1.0 if exit_code == 0 else 0.0
    result_json = {
        "exit_code": exit_code,
        "stdout": rendered.get("stdout", ""),
        "fixture": fixture_path.name,
        "provider": "local_fixture",
    }
    run_id = record_benchmark_run(
        conn,
        agent_id=agent_id,
        benchmark_name=benchmark_name,
        fixture_name=fixture_path.name,
        status=status,
        score=score,
        duration_seconds=duration,
        result_json=result_json,
        commit=False,
    )
    if commit:
        conn.commit()
    return BenchmarkResult(
        run_id=run_id,
        agent_id=agent_id,
        benchmark_name=benchmark_name,
        fixture_name=fixture_path.name,
        status=status,
        score=score,
        duration_seconds=duration,
        result_json=result_json,
    )


def run_all_agent_benchmarks(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    agent_command: str = "",
) -> list[BenchmarkResult]:
    return [
        run_agent_benchmark(
            conn,
            agent_id=agent_id,
            benchmark_name=name,
            agent_command=agent_command,
            commit=False,
        )
        for name in BENCHMARK_FIXTURES
    ]