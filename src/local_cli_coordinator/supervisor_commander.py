"""Bridge Commander chat outcomes to Supervisor broker events."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .commander_service import CommanderChatResult, send_project_chat_message
from .config import CoordinatorConfig, select_agent_by_role
from .db import get_task
from .goals import active_goal_for_project, has_live_commander_run
from .supervisor_events import EventBroker
from .supervisor_protocol import PROTOCOL_VERSION, RequestEnvelope, ResponseEnvelope

THINKING_MESSAGE = "Commander is thinking…"


def _ok(request: RequestEnvelope, result: dict[str, Any]) -> ResponseEnvelope:
    return ResponseEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id=request.request_id,
        ok=True,
        result=result,
        error=None,
    )


def _error(request: RequestEnvelope, error: str) -> ResponseEnvelope:
    return ResponseEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id=request.request_id,
        ok=False,
        result=None,
        error=error,
    )


def _task_capabilities(task: sqlite3.Row) -> list[str]:
    return [part for part in task["capabilities"].split(",") if part]


def publish_commander_chat_events(
    broker: EventBroker,
    conn: sqlite3.Connection,
    project_id: str,
    result: CommanderChatResult,
    *,
    config: CoordinatorConfig,
) -> None:
    broker.publish(
        conn,
        project_id,
        "chat.message",
        {
            "role": "coordinator",
            "text": result.message,
            "goal_id": result.goal_id,
        },
    )
    if result.run_id is not None:
        admission = result.admission
        broker.publish(
            conn,
            project_id,
            "commander.completed",
            {
                "goal_id": result.goal_id,
                "run_id": result.run_id,
                "intent": result.intent,
                "user_reply": result.message,
                "progress_summary": result.progress_summary,
                "admitted": len(admission.accepted_task_ids) if admission else 0,
                "rejected": len(admission.rejection_reasons) if admission else 0,
                "accepted_task_ids": (
                    list(admission.accepted_task_ids) if admission else []
                ),
                "rejection_reasons": (
                    list(admission.rejection_reasons) if admission else []
                ),
                "succeeded": result.succeeded,
            },
        )
    if result.admission and result.succeeded:
        for task_id in result.admission.accepted_task_ids:
            task = get_task(conn, task_id)
            if task is None:
                broker.publish(
                    conn,
                    project_id,
                    "task.created",
                    {
                        "task_id": task_id,
                        "goal_id": result.goal_id,
                        "detail_unavailable": True,
                    },
                )
                continue
            verification_commands = [
                line for line in task["verification_commands"].splitlines() if line
            ]
            capabilities = _task_capabilities(task)
            worker = select_agent_by_role(config, "worker", capabilities)
            broker.publish(
                conn,
                project_id,
                "task.created",
                {
                    "task_id": task_id,
                    "goal_id": result.goal_id,
                    "title": task["title"],
                    "state": task["state"],
                    "repo": task["repo"],
                    "goal": task["goal"],
                    "acceptance_criteria": task["acceptance_criteria"],
                    "verification_commands": verification_commands,
                    "capabilities": capabilities,
                    "agent": worker.id if worker is not None else None,
                },
            )


def handle_chat_send(
    conn: sqlite3.Connection,
    broker: EventBroker,
    config: CoordinatorConfig,
    project_root: Path,
    request: RequestEnvelope,
    *,
    project_id: str,
    text: str,
) -> ResponseEnvelope:
    goal = active_goal_for_project(conn, project_id)
    if goal is None:
        return _error(
            request,
            "No active goal. Use /goal <objective> then /goal confirm.",
        )

    status = goal["status"]
    if status == "draft":
        return _error(
            request,
            "Goal is draft. Run /goal confirm before chatting.",
        )
    if status == "paused":
        return _error(request, "Goal is paused. Resume before chatting.")
    if status == "blocked":
        return _error(request, "Goal is blocked. Resolve blocker before chatting.")
    if status != "active":
        return _error(request, f"Goal is {status}; chat requires an active goal.")

    goal_id = goal["id"]
    if has_live_commander_run(conn, goal_id):
        return _error(
            request,
            "Commander is already running; try again after the current run finishes.",
        )

    broker.publish(
        conn,
        project_id,
        "chat.message",
        {"role": "user", "text": text, "goal_id": goal_id},
    )
    broker.publish(
        conn,
        project_id,
        "chat.message",
        {"role": "system", "text": THINKING_MESSAGE, "goal_id": goal_id},
    )

    result = send_project_chat_message(
        conn,
        config,
        project_root,
        goal_id,
        text,
        project_id=project_id,
    )
    publish_commander_chat_events(broker, conn, project_id, result, config=config)

    if not result.succeeded:
        return _error(request, result.message)

    return _ok(
        request,
        {
            "received": True,
            "goal_id": result.goal_id,
            "commander_run_id": result.run_id,
            "admitted": (
                len(result.admission.accepted_task_ids) if result.admission else 0
            ),
            "rejected": (
                len(result.admission.rejection_reasons) if result.admission else 0
            ),
        },
    )