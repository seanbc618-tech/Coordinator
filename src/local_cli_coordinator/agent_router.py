"""Explainable agent routing with capability, health, and benchmark scoring."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .agent_benchmarks import get_latest_benchmark_scores
from .agent_capabilities import (
    AgentCapabilityProfile,
    agent_has_skills,
    load_capability_profiles,
)
from .agent_health import compute_agent_health
from .agent_scorecard import is_agent_available
from .config import AgentConfig, CoordinatorConfig, RepoConfig, iter_agents_by_role
from .db import active_lease_count


@dataclass(frozen=True)
class CandidateScore:
    agent_id: str
    score: float
    eligible: bool
    reason: str


@dataclass(frozen=True)
class RouteDecision:
    id: str
    project_id: str
    task_id: str
    selected_agent_id: str
    candidate_scores: list[CandidateScore]
    reason: str
    fallback_from_agent_id: str | None = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _health_penalty(status: str) -> float:
    if status == "healthy":
        return 0.0
    if status == "degraded":
        return -25.0
    return -100.0


def _repo_allows_agent(repo: RepoConfig | None, agent: AgentConfig) -> bool:
    if repo is None:
        return True
    if not repo.allow_push and agent.permissions.mode == "danger":
        return False
    return True


def _benchmark_bonus(scores: dict[str, float]) -> float:
    if not scores:
        return 0.0
    return sum(scores.values()) / len(scores) * 10.0


def score_agent_candidates(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    *,
    project_id: str,
    capabilities: list[str],
    repo_id: str | None = None,
    preferred_role: str = "worker",
    profiles: dict[str, AgentCapabilityProfile] | None = None,
) -> list[CandidateScore]:
    """Score capable agents; ineligible agents remain with score 0."""
    profiles = profiles or load_capability_profiles(conn, config, sync=False)
    repo = config.repos.get(repo_id) if repo_id else None
    health_by_agent = {
        item["agent_id"]: item
        for item in compute_agent_health(
            conn, config=config, project_id=project_id
        )
    }
    candidates: list[CandidateScore] = []

    for index, agent in enumerate(
        iter_agents_by_role(config, preferred_role, capabilities)
    ):
        profile = profiles.get(agent.id)
        reasons: list[str] = []
        eligible = True
        score = 100.0 - index

        if profile is None:
            eligible = False
            reasons.append("missing capability profile")
        elif not profile.enabled:
            eligible = False
            reasons.append("disabled")
        elif not agent_has_skills(profile, capabilities):
            eligible = False
            reasons.append("missing skills")

        if active_lease_count(conn, agent.id) >= agent.max_concurrency:
            eligible = False
            reasons.append("at concurrency limit")

        if conn is not None and not is_agent_available(conn, agent_id=agent.id):
            eligible = False
            reasons.append("cooldown active")

        if not _repo_allows_agent(repo, agent):
            eligible = False
            reasons.append("disallowed by repo policy")

        health = health_by_agent.get(agent.id, {})
        health_status = str(health.get("status") or "healthy")
        penalty = _health_penalty(health_status)
        if penalty <= -100.0:
            eligible = False
            reasons.append(f"health {health_status}")
        else:
            score += penalty
            if penalty < 0:
                reasons.append(f"health {health_status}")

        if profile is not None and profile.role == preferred_role:
            score += 5.0
            reasons.append("role match")

        benchmark_scores = get_latest_benchmark_scores(conn, agent_id=agent.id)
        bonus = _benchmark_bonus(benchmark_scores)
        if bonus > 0:
            score += bonus
            reasons.append("benchmark evidence")

        from .preference_rules import routing_preference_hints

        for hint in routing_preference_hints(conn, project_id=project_id):
            if hint.agent_id == agent.id:
                score += hint.score_delta
                reasons.append(hint.message)

        if not reasons:
            reasons.append("eligible default")

        candidates.append(
            CandidateScore(
                agent_id=agent.id,
                score=score if eligible else 0.0,
                eligible=eligible,
                reason="; ".join(reasons),
            )
        )

    candidates.sort(
        key=lambda item: (-item.score if item.eligible else -1.0, item.agent_id)
    )
    return candidates


def rank_agents_for_task(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    *,
    project_id: str,
    capabilities: list[str],
    task_id: str | None = None,
    repo_id: str | None = None,
) -> list[str]:
    """Return eligible agent ids in preferred routing order."""
    del task_id  # reserved for future task-specific routing hints
    candidates = score_agent_candidates(
        conn,
        config,
        project_id=project_id,
        capabilities=capabilities,
        repo_id=repo_id,
    )
    return [item.agent_id for item in candidates if item.eligible]


def record_route_decision(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    selected_agent_id: str,
    candidate_scores: list[CandidateScore],
    reason: str,
    fallback_from_agent_id: str | None = None,
    commit: bool = True,
) -> str:
    decision_id = str(uuid.uuid4())
    payload = [
        {
            "agent_id": item.agent_id,
            "score": item.score,
            "eligible": item.eligible,
            "reason": item.reason,
        }
        for item in candidate_scores
    ]
    conn.execute(
        """
        insert into agent_route_decisions(
            id, project_id, task_id, selected_agent_id,
            candidate_scores_json, reason, fallback_from_agent_id, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision_id,
            project_id,
            task_id,
            selected_agent_id,
            json.dumps(payload),
            reason,
            fallback_from_agent_id,
            _iso_now(),
        ),
    )
    if commit:
        conn.commit()
    return decision_id


def get_route_decision(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    project_id: str | None = None,
) -> dict[str, Any] | None:
    if project_id is None:
        row = conn.execute(
            """
            select * from agent_route_decisions
            where task_id = ?
            order by created_at desc
            limit 1
            """,
            (task_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            select * from agent_route_decisions
            where task_id = ? and project_id = ?
            order by created_at desc
            limit 1
            """,
            (task_id, project_id),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "task_id": row["task_id"],
        "selected_agent_id": row["selected_agent_id"],
        "candidate_scores": json.loads(row["candidate_scores_json"]),
        "reason": row["reason"],
        "fallback_from_agent_id": row["fallback_from_agent_id"],
        "created_at": row["created_at"],
    }


def preview_route(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    *,
    project_id: str,
    task_id: str,
) -> RouteDecision | None:
    row = conn.execute(
        "select * from tasks where id = ? and project_id = ?",
        (task_id, project_id),
    ).fetchone()
    if row is None:
        return None
    capabilities = [part for part in row["capabilities"].split(",") if part]
    if not capabilities:
        return None
    candidates = score_agent_candidates(
        conn,
        config,
        project_id=project_id,
        capabilities=capabilities,
        repo_id=str(row["repo"]) if row["repo"] else None,
    )
    eligible = [item for item in candidates if item.eligible]
    if not eligible:
        return RouteDecision(
            id="preview",
            project_id=project_id,
            task_id=task_id,
            selected_agent_id="",
            candidate_scores=candidates,
            reason="no eligible agents",
        )
    selected = eligible[0]
    return RouteDecision(
        id="preview",
        project_id=project_id,
        task_id=task_id,
        selected_agent_id=selected.agent_id,
        candidate_scores=candidates,
        reason=f"selected {selected.agent_id}: {selected.reason}",
    )


def route_and_record(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    *,
    project_id: str,
    task_id: str,
    capabilities: list[str],
    repo_id: str | None = None,
    selected_agent_id: str | None = None,
    fallback_from_agent_id: str | None = None,
    commit: bool = False,
) -> tuple[str | None, list[CandidateScore], str]:
    """Pick the best agent and optionally persist the routing decision."""
    candidates = score_agent_candidates(
        conn,
        config,
        project_id=project_id,
        capabilities=capabilities,
        repo_id=repo_id,
    )
    eligible = [item for item in candidates if item.eligible]
    if selected_agent_id is not None:
        selected = next(
            (item for item in candidates if item.agent_id == selected_agent_id),
            None,
        )
        if selected is None:
            selected = CandidateScore(
                agent_id=selected_agent_id,
                score=0.0,
                eligible=False,
                reason="leased agent",
            )
            candidates = [*candidates, selected]
    elif eligible:
        selected = eligible[0]
    else:
        return None, candidates, "no eligible agents"
    reason = f"selected {selected.agent_id}: {selected.reason}"
    if selected_agent_id is not None and eligible:
        top_agent = eligible[0].agent_id
        if selected_agent_id != top_agent:
            from .preference_observer import observe_route_override

            observe_route_override(
                conn,
                project_id=project_id,
                task_id=task_id,
                selected_agent_id=selected_agent_id,
                reason=reason,
                commit=False,
            )
    record_route_decision(
        conn,
        project_id=project_id,
        task_id=task_id,
        selected_agent_id=selected.agent_id,
        candidate_scores=candidates,
        reason=reason,
        fallback_from_agent_id=fallback_from_agent_id,
        commit=commit,
    )
    return selected.agent_id, candidates, reason