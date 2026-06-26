"""Bridge Commander chat outcomes to Supervisor broker events."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .commander_service import CommanderChatResult, send_project_chat_message
from .context_files import (
    ContextFile,
    ContextFileError,
    format_context_error,
    load_context_files_from_params,
    public_metadata_from_context_files,
)
from .config import CoordinatorConfig, select_agent_by_role
from .execution_policy import ExecutionPolicy, derive_server_policy
from .db import get_task
from .goals import active_goal_for_project, get_goal, has_live_commander_run
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
    goal_id_override = request.params.get("goal_id")
    if goal_id_override is not None:
        try:
            goal = get_goal(conn, int(goal_id_override))
        except (KeyError, TypeError, ValueError):
            return _error(request, "goal_id is invalid")
        if goal["project_id"] != project_id:
            return _error(request, "goal_id does not belong to this project")
    else:
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

    context_params = request.params.get("context_files") or []
    context_files: list[ContextFile] = []
    if context_params:
        if not isinstance(context_params, list):
            return _error(request, "context_files must be a list")
        try:
            context_files = load_context_files_from_params(
                project_root,
                context_params,
            )
        except ContextFileError as exc:
            return _error(request, format_context_error(exc))

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

    repo_config = None
    repo_ids = json.loads(goal["repo_ids"])
    for repo_id in repo_ids:
        if repo_id in config.repos:
            repo_config = config.repos[repo_id]
            break
    if repo_config is None and config.repos:
        repo_config = next(iter(config.repos.values()))

    execution_policy_payload: dict[str, object] = {}
    raw_policy = request.params.get("execution_policy")
    if isinstance(raw_policy, dict) and repo_config is not None:
        claimed = ExecutionPolicy.from_json(raw_policy)
        server = derive_server_policy(repo_config)
        effective = ExecutionPolicy.compute_effective(server, claimed)
        execution_policy_payload = effective.to_json_dict()

    result = send_project_chat_message(
        conn,
        config,
        project_root,
        goal_id,
        text,
        project_id=project_id,
        context_files=context_files,
        execution_policy=execution_policy_payload,
    )
    publish_commander_chat_events(broker, conn, project_id, result, config=config)

    if not result.succeeded:
        return _error(request, result.message)

    accepted_task_ids = (
        list(result.admission.accepted_task_ids) if result.admission else []
    )
    return _ok(
        request,
        {
            "received": True,
            "goal_id": result.goal_id,
            "commander_run_id": result.run_id,
            "admitted": len(accepted_task_ids),
            "rejected": (
                len(result.admission.rejection_reasons) if result.admission else 0
            ),
            "user_reply": result.user_reply or result.message,
            "intent": result.intent or "conversation",
            "accepted_task_ids": accepted_task_ids,
            "context_files": public_metadata_from_context_files(context_files),
        },
    )