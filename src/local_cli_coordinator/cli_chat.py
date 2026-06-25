"""Headless CLI prompt client via global Supervisor RPC."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .commander_service import COMMANDER_TIMEOUT_SECONDS
from .db import connect, init_db
from .goals import active_goal_for_project, latest_non_terminal_goal_for_project
from .projects import find_project_by_path
from .runtime_paths import RuntimePaths, resolve_runtime_paths
from .supervisor_identity import INCOMPATIBLE_SUPERVISOR_MESSAGE
from .supervisor_process import (
    SupervisorIncompatibleError,
    SupervisorReadinessError,
    ensure_supervisor,
)
from .supervisor_protocol import PROTOCOL_VERSION, RequestEnvelope
from .supervisor_server import SupervisorTransportError, send_request
from .tui_launcher import NotGitRepositoryError, launch_tui, resolve_git_root

CHAT_SEND_TIMEOUT = float(COMMANDER_TIMEOUT_SECONDS) + 10.0


@dataclass(frozen=True)
class PromptOutcome:
    ok: bool
    project_id: str | None = None
    goal_id: int | None = None
    user_reply: str = ""
    intent: str = "conversation"
    admitted: int = 0
    rejected: int = 0
    accepted_task_ids: list[str] | None = None
    error_code: str | None = None
    error_message: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "project_id": self.project_id,
            "goal_id": self.goal_id,
            "user_reply": self.user_reply,
            "intent": self.intent,
            "admitted": self.admitted,
            "rejected": self.rejected,
            "accepted_task_ids": list(self.accepted_task_ids or []),
            "error": (
                {"code": self.error_code, "message": self.error_message}
                if not self.ok
                else None
            ),
        }


def _error_outcome(code: str, message: str) -> PromptOutcome:
    return PromptOutcome(ok=False, error_code=code, error_message=message)


def _resolve_project(paths: RuntimePaths, git_root: Path) -> tuple[str | None, PromptOutcome | None]:
    conn = connect(paths.database)
    try:
        init_db(conn)
        project = find_project_by_path(conn, git_root)
        if project is None:
            return None, _error_outcome(
                "project_not_registered",
                f"project not registered for {git_root}",
            )
        return project["id"], None
    finally:
        conn.close()


def _resolve_goal_id(
    paths: RuntimePaths,
    project_id: str,
    *,
    continue_goal: bool,
) -> tuple[int | None, PromptOutcome | None]:
    conn = connect(paths.database)
    try:
        init_db(conn)
        if continue_goal:
            goal = latest_non_terminal_goal_for_project(conn, project_id)
        else:
            goal = active_goal_for_project(conn, project_id)
        if goal is None:
            return None, _error_outcome(
                "no_goal",
                "No continuable goal for this project.",
            )
        return goal["id"], None
    finally:
        conn.close()


def _send_rpc(
    paths: RuntimePaths,
    *,
    project_id: str,
    method: str,
    params: dict[str, Any],
    timeout: float = 10.0,
) -> tuple[dict[str, Any] | None, PromptOutcome | None]:
    request = RequestEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id=f"cli-{uuid.uuid4().hex[:8]}",
        project_id=project_id,
        method=method,
        params=params,
    )
    try:
        response = send_request(paths.socket, request, timeout=timeout)
    except SupervisorTransportError as exc:
        return None, _error_outcome("supervisor_unreachable", str(exc))
    if not response.ok or response.result is None:
        return None, _error_outcome(
            "supervisor_error",
            response.error or "supervisor request failed",
        )
    return response.result, None


def _format_status(result: dict[str, Any]) -> str:
    counts = result.get("counts") or {}
    goal = result.get("goal")
    lines = [
        "Project status",
        f"  ready: {counts.get('ready', 0)}",
        f"  running: {counts.get('running', 0)}",
        f"  done: {counts.get('done', 0)}",
    ]
    if goal:
        lines.append(f"  goal: {goal.get('title')} ({goal.get('status')})")
    else:
        lines.append("  goal: none")
    return "\n".join(lines)


def _handle_slash(
    paths: RuntimePaths,
    project_id: str,
    text: str,
) -> PromptOutcome:
    command = text.strip().split()[0].lower()
    if command == "/status":
        result, err = _send_rpc(
            paths,
            project_id=project_id,
            method="project.status",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=_format_status(result),
            intent="status_question",
        )
    if command == "/tasks":
        result, err = _send_rpc(
            paths,
            project_id=project_id,
            method="project.tasks",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        tasks = result.get("tasks") or []
        if not tasks:
            reply = "No tasks yet."
        else:
            lines = ["Tasks:"]
            for task in tasks:
                lines.append(
                    f"  {task.get('id')} [{task.get('state')}] {task.get('title')}"
                )
            reply = "\n".join(lines)
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    return _error_outcome("unknown_slash", f"Unknown command: {command}. Use /help.")


def _chat_send(
    paths: RuntimePaths,
    *,
    project_id: str,
    text: str,
    goal_id: int | None,
) -> PromptOutcome:
    params: dict[str, Any] = {"text": text}
    if goal_id is not None:
        params["goal_id"] = goal_id
    result, err = _send_rpc(
        paths,
        project_id=project_id,
        method="chat.send",
        params=params,
        timeout=CHAT_SEND_TIMEOUT,
    )
    if err is not None:
        return err
    assert result is not None
    return PromptOutcome(
        ok=True,
        project_id=project_id,
        goal_id=result.get("goal_id"),
        user_reply=str(result.get("user_reply") or ""),
        intent=str(result.get("intent") or "conversation"),
        admitted=int(result.get("admitted") or 0),
        rejected=int(result.get("rejected") or 0),
        accepted_task_ids=list(result.get("accepted_task_ids") or []),
    )


def _emit_outcome(outcome: PromptOutcome, *, mode: str) -> None:
    if mode == "json":
        print(json.dumps(outcome.to_json_dict(), ensure_ascii=False))
        return
    if outcome.user_reply:
        print(outcome.user_reply)


def run_cli_prompt(args: argparse.Namespace) -> int:
    prompt_text = getattr(args, "prompt_text", "").strip()
    if not prompt_text:
        print("error: prompt text is required", file=sys.stderr)
        return 2

    try:
        git_root = resolve_git_root(Path(args.root).resolve())
    except NotGitRepositoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    paths = resolve_runtime_paths()
    paths.create()

    project_id, project_err = _resolve_project(paths, git_root)
    if project_err is not None:
        _emit_outcome(project_err, mode=args.mode)
        return 1

    assert project_id is not None

    try:
        ensure_supervisor(paths)
    except SupervisorIncompatibleError:
        print(f"error: {INCOMPATIBLE_SUPERVISOR_MESSAGE}", file=sys.stderr)
        return 1
    except SupervisorReadinessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    continue_goal = bool(getattr(args, "continue_goal", False))

    if prompt_text.startswith("/"):
        outcome = _handle_slash(paths, project_id, prompt_text)
    else:
        goal_id, goal_err = _resolve_goal_id(
            paths,
            project_id,
            continue_goal=continue_goal,
        )
        if goal_err is not None:
            _emit_outcome(goal_err, mode=args.mode)
            return 1
        assert goal_id is not None
        outcome = _chat_send(
            paths,
            project_id=project_id,
            text=prompt_text,
            goal_id=goal_id if continue_goal else None,
        )

    _emit_outcome(outcome, mode=args.mode)
    if not outcome.ok:
        return 1

    if args.print_mode or args.no_tui:
        return 0

    return launch_tui(start_path=git_root)