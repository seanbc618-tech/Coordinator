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
from .context_files import (
    ContextFile,
    ContextFileError,
    load_context_files,
    parse_context_error_message,
    public_metadata_from_context_files,
)
from .goal_sessions import parse_goal_session_error_message
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
from .config_runtime import load_config_for_paths
from .execution_policy import resolve_effective_policy
from .supervisor_protocol import (
    PROTOCOL_VERSION,
    RequestEnvelope,
    ResponseEnvelope,
    encode_envelope,
)
from .supervisor_server import SupervisorTransportError, send_request
from .task_control import build_orchestration, flatten_task_detail_json
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
    context_files: list[dict[str, Any]] | None = None
    candidates: list[dict[str, Any]] | None = None
    orchestration: dict[str, Any] | None = None

    def to_json_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "project_id": self.project_id,
            "goal_id": self.goal_id,
            "user_reply": self.user_reply,
            "intent": self.intent,
            "admitted": self.admitted,
            "rejected": self.rejected,
            "accepted_task_ids": list(self.accepted_task_ids or []),
            "context_files": list(self.context_files or []),
            "error": (
                {"code": self.error_code, "message": self.error_message}
                if not self.ok
                else None
            ),
        }
        if self.candidates is not None:
            payload["candidates"] = list(self.candidates)
        if self.orchestration is not None:
            payload["orchestration"] = dict(self.orchestration)
        return payload


def _error_outcome(code: str, message: str) -> PromptOutcome:
    return PromptOutcome(ok=False, error_code=code, error_message=message)


def _load_cli_context(
    repo_root: Path,
    tokens: list[str],
    *,
    cwd: Path,
) -> tuple[list[ContextFile], PromptOutcome | None]:
    if not tokens:
        return [], None
    try:
        return load_context_files(repo_root, cwd, tokens), None
    except ContextFileError as exc:
        return [], _error_outcome(exc.code, str(exc))


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


def _parse_rpc_error(error: str | None) -> tuple[str, str]:
    code, message = parse_goal_session_error_message(error)
    if code != "supervisor_error":
        return code, message
    return parse_context_error_message(error)


def _local_rpc_envelope(
    *,
    ok: bool,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> ResponseEnvelope:
    return ResponseEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id=request_id or f"cli-local-{uuid.uuid4().hex[:8]}",
        ok=ok,
        result=result,
        error=error,
    )


def _emit_rpc(envelope: ResponseEnvelope) -> None:
    print(encode_envelope(envelope))


def _outcome_to_rpc(outcome: PromptOutcome) -> ResponseEnvelope:
    if outcome.ok:
        return _local_rpc_envelope(
            ok=True,
            result={
                "project_id": outcome.project_id,
                "goal_id": outcome.goal_id,
                "user_reply": outcome.user_reply,
                "intent": outcome.intent,
                "admitted": outcome.admitted,
                "rejected": outcome.rejected,
                "accepted_task_ids": list(outcome.accepted_task_ids or []),
                "context_files": list(outcome.context_files or []),
                "candidates": list(outcome.candidates or []),
                "orchestration": dict(outcome.orchestration or {}),
            },
        )
    message = outcome.error_message or "request failed"
    return _local_rpc_envelope(ok=False, error=f"{outcome.error_code}: {message}")


def _send_rpc(
    paths: RuntimePaths,
    *,
    project_id: str,
    method: str,
    params: dict[str, Any],
    timeout: float = 10.0,
    request_id: str | None = None,
) -> tuple[dict[str, Any] | None, PromptOutcome | None, ResponseEnvelope | None]:
    request = RequestEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id=request_id or f"cli-{uuid.uuid4().hex[:8]}",
        project_id=project_id,
        method=method,
        params=params,
    )
    try:
        response = send_request(paths.socket, request, timeout=timeout)
    except SupervisorTransportError as exc:
        envelope = _local_rpc_envelope(ok=False, error=str(exc), request_id=request.request_id)
        return None, _error_outcome("supervisor_unreachable", str(exc)), envelope
    envelope = ResponseEnvelope(
        protocol_version=response.protocol_version,
        request_id=response.request_id,
        ok=response.ok,
        result=response.result,
        error=response.error,
    )
    if not response.ok or response.result is None:
        code, message = _parse_rpc_error(response.error)
        return None, _error_outcome(code, message), envelope
    return response.result, None, envelope


def _resolve_cli_execution_policy(
    paths: RuntimePaths,
    git_root: Path,
    args: argparse.Namespace,
) -> dict[str, object]:
    if not (
        getattr(args, "tools", None)
        or getattr(args, "exclude_tools", None)
        or getattr(args, "no_tools", False)
    ):
        return {}
    try:
        config = load_config_for_paths(paths)
    except (FileNotFoundError, OSError, ValueError):
        if getattr(args, "no_tools", False):
            return {"allowed": [], "source": "effective"}
        return {}
    repo_config = None
    for repo in config.repos.values():
        if repo.path.resolve() == git_root.resolve():
            repo_config = repo
            break
    if repo_config is None and config.repos:
        repo_config = next(iter(config.repos.values()))
    if repo_config is None:
        if getattr(args, "no_tools", False):
            return {"allowed": [], "source": "effective"}
        return {}
    effective = resolve_effective_policy(
        repo_config,
        tools=getattr(args, "tools", None),
        exclude_tools=getattr(args, "exclude_tools", None),
        no_tools=bool(getattr(args, "no_tools", False)),
    )
    return effective.to_json_dict()


def _is_interactive_session(args: argparse.Namespace) -> bool:
    if args.print_mode or args.mode == "json":
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def _interactive_resume_selection(candidates: list[dict[str, Any]]) -> int | None:
    if not candidates:
        print("No resumable goals.")
        return None

    print(_format_goal_candidates(candidates))
    try:
        choice = input("Select goal number: ").strip()
    except EOFError:
        return None

    try:
        index = int(choice)
    except ValueError:
        print("error: enter a number from the list", file=sys.stderr)
        return None

    if index < 1 or index > len(candidates):
        print("error: selection out of range", file=sys.stderr)
        return None

    goal_id = int(candidates[index - 1]["id"])
    try:
        confirm = input(f"Resume goal {goal_id}? [y/N] ").strip().lower()
    except EOFError:
        return None

    if confirm not in {"y", "yes"}:
        return None
    return goal_id


def _format_goal_candidates(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "No resumable goals."
    lines = ["Resumable goals:"]
    for index, candidate in enumerate(candidates, start=1):
        lines.append(
            "  {index}. [{status}] {title} (id={id})".format(
                index=index,
                status=candidate.get("status"),
                title=candidate.get("title"),
                id=candidate.get("id"),
            )
        )
    return "\n".join(lines)


def _list_goal_candidates(
    paths: RuntimePaths,
    project_id: str,
) -> PromptOutcome:
    result, err, _envelope = _send_rpc(
        paths,
        project_id=project_id,
        method="project.goals",
        params={},
    )
    if err is not None:
        return err
    assert result is not None
    candidates = list(result.get("candidates") or [])
    return PromptOutcome(
        ok=True,
        project_id=project_id,
        user_reply=_format_goal_candidates(candidates),
        candidates=candidates,
    )


def _resume_goal(
    paths: RuntimePaths,
    project_id: str,
    goal_id: int,
) -> PromptOutcome | None:
    _, err, _envelope = _send_rpc(
        paths,
        project_id=project_id,
        method="project.goal.resume",
        params={"goal_id": goal_id},
    )
    return err


def _fork_goal(
    paths: RuntimePaths,
    project_id: str,
    source_goal_id: int,
    instruction: str,
) -> PromptOutcome:
    result, err, _envelope = _send_rpc(
        paths,
        project_id=project_id,
        method="project.goal.fork",
        params={"goal_id": source_goal_id, "instruction": instruction},
    )
    if err is not None:
        return err
    assert result is not None
    new_goal_id = int(result.get("goal_id") or 0)
    return PromptOutcome(
        ok=True,
        project_id=project_id,
        goal_id=new_goal_id,
        user_reply=(
            f"Forked goal {new_goal_id} (draft). "
            "Run /goal confirm to activate."
        ),
    )


def _parse_task_slash(text: str) -> tuple[str | None, str | None]:
    parts = text.strip().split()
    if len(parts) < 2:
        return None, None
    task_id = parts[1]
    action = parts[2].lower() if len(parts) > 2 else None
    return task_id, action


def _parse_task_id_slash(text: str) -> str | None:
    parts = text.strip().split()
    if len(parts) < 2:
        return None
    return parts[1]


def _orchestration_from_chat_result(result: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("orchestration")
    if isinstance(raw, dict):
        return raw
    return build_orchestration(
        admitted=int(result.get("admitted") or 0),
        rejected=int(result.get("rejected") or 0),
        rejection_reasons=list(result.get("rejection_reasons") or []),
        intent=str(result.get("intent") or "conversation"),
    )


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


def _format_loop_status(result: dict[str, Any]) -> str:
    goal = result.get("goal")
    last = result.get("last_iteration")
    backlog = result.get("backlog_counts") or {}
    run = result.get("run")
    lines = [
        f"Loop status [{result.get('project_id')}]",
        f"  autonomy: {'on' if result.get('autonomy_enabled') else 'off'}",
        f"  unevaluated terminal: {result.get('unevaluated_terminal_count', 0)}",
        f"  backlog: {backlog or 'empty'}",
        f"  next: {result.get('next_expected_action', 'wait')}",
    ]
    if goal:
        lines.append(f"  goal: {goal.get('title')} ({goal.get('status')})")
    else:
        lines.append("  goal: none")
    if run:
        lines.append(
            "  run: "
            f"{run.get('status')} {run.get('id')}, "
            f"iterations={run.get('iteration_count', 0)}, "
            f"idle={run.get('idle_iteration_count', 0)}"
        )
    else:
        lines.append("  run: none")
    if last:
        lines.append(
            f"  last: {last.get('decision')} — {last.get('reason')}"
        )
    return "\n".join(lines)


def _format_run_status(result: dict[str, Any]) -> str:
    run = result.get("run")
    if not run:
        return f"Run status [{result.get('project_id')}]\n  run: none"
    return (
        f"Run status [{result.get('project_id')}]\n"
        f"  run: {run.get('status')} {run.get('id')}, "
        f"iterations={run.get('iteration_count', 0)}, "
        f"idle={run.get('idle_iteration_count', 0)}"
    )


def _loop_admin_data(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": payload.get("project_id"),
        "run": payload.get("run"),
        "next_decision": payload.get("next_expected_action", "wait"),
    }


def _admin_error(code: str, message: str, hint: str = "") -> "AdminError":
    from .admin_json import AdminError

    return AdminError(code=code, message=message, hint=hint)


def _outcome_to_admin_error(outcome: PromptOutcome) -> "AdminError":
    code = outcome.error_code or "invalid_project"
    hints = {
        "supervisor_not_running": "Run `coordinator supervisor start`.",
        "project_not_registered": "Run `coordinator project add` or `coordinator init`.",
        "supervisor_unreachable": "Run `coordinator supervisor start`.",
    }
    return _admin_error(
        code,
        outcome.error_message or "request failed",
        hints.get(code, ""),
    )


def run_admin_loop_command(args: argparse.Namespace) -> int:
    from .admin_json import emit_envelope, envelope

    command = "loop.run" if getattr(args, "loop_command", None) == "run" else "loop"
    method = (
        "project.loop.run.status"
        if command == "loop.run"
        else "project.loop.status"
    )
    json_mode = getattr(args, "json", False)

    try:
        git_root = resolve_git_root(Path(args.root).resolve())
    except NotGitRepositoryError as exc:
        if json_mode:
            return emit_envelope(
                envelope(
                    command=command,
                    ok=False,
                    errors=[_admin_error("invalid_project", str(exc))],
                )
            )
        print(f"error: {exc}", file=sys.stderr)
        return 1

    paths = resolve_runtime_paths()
    paths.create()

    project_id, project_err = _resolve_project(paths, git_root)
    if project_err is not None:
        if json_mode:
            return emit_envelope(
                envelope(
                    command=command,
                    ok=False,
                    errors=[_outcome_to_admin_error(project_err)],
                )
            )
        print(f"error: {project_err.error_message}", file=sys.stderr)
        return 1

    assert project_id is not None

    if not paths.socket.exists():
        if json_mode:
            return emit_envelope(
                envelope(
                    command=command,
                    ok=False,
                    errors=[
                        _admin_error(
                            "supervisor_not_running",
                            "Supervisor is not running",
                            "Run `coordinator supervisor start`.",
                        )
                    ],
                )
            )
        print("error: Supervisor is not running", file=sys.stderr)
        return 1

    try:
        ensure_supervisor(paths)
    except SupervisorIncompatibleError:
        if json_mode:
            return emit_envelope(
                envelope(
                    command=command,
                    ok=False,
                    errors=[
                        _admin_error(
                            "supervisor_not_running",
                            INCOMPATIBLE_SUPERVISOR_MESSAGE,
                            "Run `coordinator supervisor restart`.",
                        )
                    ],
                )
            )
        print(f"error: {INCOMPATIBLE_SUPERVISOR_MESSAGE}", file=sys.stderr)
        return 1
    except SupervisorReadinessError as exc:
        if json_mode:
            return emit_envelope(
                envelope(
                    command=command,
                    ok=False,
                    errors=[_admin_error("supervisor_not_running", str(exc))],
                )
            )
        print(f"error: {exc}", file=sys.stderr)
        return 1

    result, err, _envelope = _send_rpc(
        paths,
        project_id=project_id,
        method=method,
        params={},
    )
    if err is not None:
        if json_mode:
            return emit_envelope(
                envelope(
                    command=command,
                    ok=False,
                    errors=[_outcome_to_admin_error(err)],
                )
            )
        print(f"error: {err.error_message}", file=sys.stderr)
        return 1

    assert result is not None
    if json_mode:
        data = (
            _loop_admin_data(result)
            if command == "loop"
            else {
                "project_id": result.get("project_id"),
                "run": result.get("run"),
            }
        )
        return emit_envelope(envelope(command=command, ok=True, data=data))

    if command == "loop.run":
        print(_format_run_status(result))
    else:
        print(_format_loop_status(result))
    return 0


def _parse_jump_slash(text: str) -> tuple[str, dict[str, Any]]:
    parts = text.strip().split(maxsplit=1)
    target = parts[1].strip() if len(parts) > 1 else ""
    params: dict[str, Any] = {"target": target}
    if parts and parts[0].lower() == "/open":
        params["alias"] = "open"
    return params


def _parse_overnight_slash(text: str) -> tuple[str, dict[str, Any]]:
    parts = text.strip().split(maxsplit=1)
    args = parts[1] if len(parts) > 1 else ""
    return "project.overnight", {"args": args}


def _parse_loop_slash(text: str) -> tuple[str, dict[str, Any]]:
    parts = text.strip().split()
    sub = parts[1].lower() if len(parts) >= 2 else ""
    if sub == "step":
        return "project.loop.step", {"force": True}
    if sub == "start":
        return "project.loop.start", {}
    if sub == "stop":
        return "project.loop.stop", {"reason": "operator stop"}
    if sub == "pause":
        return "project.loop.pause", {}
    if sub == "resume":
        return "project.loop.resume", {}
    if sub == "run":
        return "project.loop.run.status", {}
    return "project.loop.status", {}


def _parse_approve_slash(
    args_text: str,
) -> tuple[str, dict[str, Any]] | None:
    parts = args_text.strip().split()
    if parts and parts[0].lower() == "token":
        if len(parts) < 2:
            return None
        from .approval_tokens import is_approval_token

        token = parts[1]
        if not is_approval_token(token):
            return None
        return "operator.approval.approve", {"token": token, "confirmed": True}
    task_id = args_text.strip().split()[0] if args_text.strip() else ""
    if not task_id:
        return None
    return "project.task.approve", {"task_id": task_id}


def _parse_why_slash(args_text: str) -> tuple[str, dict[str, Any]] | None:
    target = args_text.strip().split()[0] if args_text.strip() else ""
    if not target:
        return None
    if target.startswith("task-"):
        return "operator.explain_failure", {"task_id": target}
    return "project.why", {"path": target}


def _parse_onboard_slash(args_text: str) -> tuple[str, dict[str, Any]]:
    parts = args_text.strip().split()
    path = str(Path.cwd())
    if parts and parts[0].lower() == "apply":
        preset = parts[1] if len(parts) > 1 else "observe"
        return "project.onboard.apply", {"path": path, "preset": preset}
    return "project.onboard.plan", {"path": path, "preset": "observe"}


def _parse_simulation_slash(args_text: str) -> tuple[str, dict[str, Any]]:
    """Route autonomy simulation vs onboarding preset simulation."""
    parts = args_text.strip().split()
    if parts and parts[0].lower() == "preset":
        preset = parts[1] if len(parts) > 1 else "overnight"
        return "project.onboard.simulate", {
            "preset": preset,
            "path": str(Path.cwd()),
        }
    if parts and parts[0].lower() == "project":
        hours = 8.0
        if len(parts) > 1:
            try:
                hours = float(parts[1])
            except ValueError:
                hours = 8.0
        return "simulation.run", {"scope": "project", "horizon_hours": hours}
    hours = 8.0
    if parts and parts[0].lower() == "overnight":
        hours = 8.0
    elif parts:
        try:
            hours = float(parts[0])
        except ValueError:
            hours = 8.0
    return "simulation.run", {"scope": "global", "horizon_hours": hours}


def _parse_slash_command(text: str) -> tuple[str, str]:
    stripped = text.strip()
    lowered = stripped.lower()
    for multi in (
        "/ci failures",
        "/pr update",
        "/notify test",
        "/pause all",
        "/resume all",
        "/rollback-onboard",
        "/benchmark agents",
    ):
        if lowered.startswith(multi):
            return multi, stripped[len(multi) :].strip()
    parts = stripped.split(maxsplit=1)
    command = parts[0].lower()
    args_text = parts[1] if len(parts) > 1 else ""
    return command, args_text


def _handle_slash(
    paths: RuntimePaths,
    project_id: str,
    text: str,
) -> PromptOutcome:
    command, args_text = _parse_slash_command(text)
    if command == "/dashboard":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="supervisor.dashboard",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        projects = result.get("projects") or []
        runs = result.get("autonomous_runs") or {}
        lines = ["Dashboard:"]
        if runs:
            lines.append(
                "  autonomous_runs: "
                f"running={runs.get('running', 0)} "
                f"paused={runs.get('paused', 0)} "
                f"stopped={runs.get('stopped', 0)}"
            )
        for entry in projects:
            counts = entry.get("task_counts") or {}
            count_text = ", ".join(f"{k}={v}" for k, v in counts.items()) or "none"
            strategic = entry.get("strategic") or {}
            strategic_text = (
                f"milestones={strategic.get('active_milestones', 0)} "
                f"recoveries={strategic.get('pending_recoveries', 0)} "
                f"overnight={strategic.get('overnight_summaries', 0)}"
            )
            lines.append(
                f"  {entry.get('project_id')} goal={entry.get('goal_status')} "
                f"workers={entry.get('active_workers', 0)} tasks[{count_text}] "
                f"{strategic_text}"
            )
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply="\n".join(lines) if projects else "Dashboard — no projects",
            intent="status_question",
        )
    if command == "/task":
        task_id, action = _parse_task_slash(text)
        if not task_id:
            return _error_outcome("invalid_args", "usage: /task <id> [approve|retry|cancel|log]")
        if action in {"approve", "retry", "cancel"}:
            method = f"project.task.{action}"
            result, err, _envelope = _send_rpc(
                paths,
                project_id=project_id,
                method=method,
                params={"task_id": task_id},
            )
            if err is not None:
                return err
            assert result is not None
            return PromptOutcome(
                ok=True,
                project_id=project_id,
                user_reply=f"Task {task_id} -> {result.get('state', action)}",
                intent="status_question",
            )
        if action == "log":
            result, err, _envelope = _send_rpc(
                paths,
                project_id=project_id,
                method="project.task.log",
                params={"task_id": task_id},
            )
            if err is not None:
                return err
            assert result is not None
            content = str(result.get("content") or "")
            if not content:
                return PromptOutcome(
                    ok=True,
                    project_id=project_id,
                    user_reply=f"Task {task_id} log: (empty)",
                    intent="status_question",
                )
            tail = content[-4000:] if len(content) > 4000 else content
            return PromptOutcome(
                ok=True,
                project_id=project_id,
                user_reply=f"Task {task_id} log:\n{tail}",
                intent="status_question",
            )
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.task",
            params={"args": task_id},
        )
        if err is not None:
            return err
        assert result is not None
        flat = flatten_task_detail_json(result)
        lines = [
            f"Task {flat.get('id', task_id)} [{flat.get('state')}] {flat.get('title', '')}",
            f"Goal: {flat.get('goal', '')}",
        ]
        if flat.get("failure_class"):
            lines.append(f"Failure: {flat.get('failure_class')} — {flat.get('failure_summary', '')}")
        if flat.get("human_review_required"):
            lines.append("Human review required")
        policy = flat.get("execution_policy") or {}
        if policy:
            lines.append(f"Policy: {policy}")
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply="\n".join(lines),
            intent="status_question",
        )
    if command == "/status":
        result, err, _envelope = _send_rpc(
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
        result, err, _envelope = _send_rpc(
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
    if command == "/loop":
        method, params = _parse_loop_slash(text)
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if err is not None:
            return err
        assert result is not None
        if method == "project.loop.step":
            reply = (
                f"Loop step: {result.get('decision')} — {result.get('reason')}"
            )
        elif method == "project.loop.run.status":
            reply = _format_run_status(result)
        elif method in {
            "project.loop.start",
            "project.loop.stop",
            "project.loop.pause",
            "project.loop.resume",
        }:
            reply = _format_run_status(result)
        else:
            reply = _format_loop_status(result)
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/backlog":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.backlog",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        items = result.get("items") or []
        if not items:
            reply = "Backlog is empty."
        else:
            lines = ["Backlog:"]
            for item in items:
                lines.append(
                    f"  {item.get('id')} [{item.get('status')}] {item.get('title')}"
                )
            reply = "\n".join(lines)
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/evals":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.evaluations",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        evaluations = result.get("evaluations") or []
        if not evaluations:
            reply = "No evaluations yet."
        else:
            lines = ["Evaluations:"]
            for entry in evaluations:
                lines.append(
                    f"  {entry.get('task_id')} -> {entry.get('verdict')} "
                    f"({entry.get('next_action')})"
                )
            reply = "\n".join(lines)
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/strategy":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.strategy",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        milestone = result.get("current_milestone")
        if milestone:
            reply = (
                f"Strategy: {milestone.get('title')} "
                f"(priority {milestone.get('priority')})"
            )
        else:
            reply = "Strategy: no active milestone"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command in {"/evidence", "/review", "/risk", "/merge-ready"}:
        task_id = _parse_task_id_slash(text)
        if not task_id:
            return _error_outcome(
                "invalid_args",
                f"usage: {command} <task-id>",
            )
        method = {
            "/evidence": "project.evidence",
            "/review": "project.review",
            "/risk": "project.risk",
            "/merge-ready": "project.merge_ready",
        }[command]
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params={"task_id": task_id},
        )
        if err is not None:
            return err
        assert result is not None
        if command == "/evidence":
            items = result.get("evidence") or []
            reply = f"Evidence for {task_id}: {len(items)} record(s)"
        elif command == "/review":
            reply = (
                f"Review {task_id}: allowed={result.get('completion_allowed')} "
                f"risk={result.get('risk_level')}"
            )
        elif command == "/risk":
            reply = (
                f"Risk {task_id}: {result.get('risk_level')} "
                f"human={result.get('requires_human_review')}"
            )
        else:
            reply = (
                f"Merge-ready {task_id}: {result.get('merge_ready')} "
                f"human={result.get('requires_human_review')}"
            )
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command in {"/deliver", "/ci", "/delivery"}:
        task_id = _parse_task_id_slash(text)
        if not task_id:
            return _error_outcome(
                "invalid_args",
                f"usage: {command} <task-id>",
            )
        method = {
            "/deliver": "project.deliver",
            "/ci": "project.ci",
            "/delivery": "project.delivery",
        }[command]
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params={"task_id": task_id},
        )
        if err is not None:
            return err
        assert result is not None
        if command == "/deliver":
            reply = (
                f"Deliver {task_id}: allowed={result.get('allowed')} "
                f"human={result.get('requires_human_review')}"
            )
        elif command == "/ci":
            reply = f"CI {task_id}: {result.get('ci_state')}"
        else:
            delivery = result.get("delivery")
            reply = (
                f"Delivery {task_id}: {delivery.get('status') if delivery else 'none'}"
            )
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/prs":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.prs",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        prs = result.get("prs") or []
        reply = f"PRs: {len(prs)} open delivery record(s)"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/heal":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.pr.heal",
            params={"dry_run": True},
        )
        if err is not None:
            return err
        assert result is not None
        attempts = result.get("attempts") or []
        reply = f"Heal: {len(attempts)} bounded attempt(s) (dry-run)"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/stale":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.pr.health",
            params={"stale_only": True},
        )
        if err is not None:
            return err
        assert result is not None
        records = result.get("records") or []
        reply = f"Stale PRs: {len(records)} record(s)"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/ci failures":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.pr.health",
            params={"ci_failed_only": True},
        )
        if err is not None:
            return err
        assert result is not None
        records = result.get("records") or []
        reply = f"CI failures: {len(records)} record(s)"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/reviews":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.pr.reviews",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        reviews = result.get("reviews") or []
        reply = f"Reviews: {len(reviews)} delivery review set(s)"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/pr update":
        if not args_text:
            return _error_outcome("invalid_args", "usage: /pr update <delivery-id>")
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.pr.update_evidence",
            params={"delivery_id": args_text.split()[0], "dry_run": True},
        )
        if err is not None:
            return err
        assert result is not None
        updated = result.get("updated")
        reply = f"PR evidence update: updated={updated} (dry-run)"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/rebase":
        if not args_text:
            return _error_outcome("invalid_args", "usage: /rebase <delivery-id> [--apply]")
        apply = "--apply" in args_text
        delivery_id = args_text.replace("--apply", "").strip().split()[0]
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.pr.rebase",
            params={
                "delivery_id": delivery_id,
                "dry_run": not apply,
                "apply": apply,
            },
        )
        if err is not None:
            return err
        assert result is not None
        reply = f"Rebase: status={result.get('status')} action={result.get('action')}"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/merge-policy":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.merge_policy",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        repos = result.get("repos") or []
        reply = f"Merge policy: {len(repos)} repo(s) configured"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command in {"/inbox", "/attention", "/summary", "/notify", "/notify test"}:
        args_text = text.strip()[len(command) :].strip()
        method = {
            "/inbox": "operator.inbox",
            "/attention": "operator.attention",
            "/summary": "operator.summary",
            "/notify": "operator.notify",
            "/notify test": "operator.notify",
        }[command]
        params: dict[str, Any] = {}
        if command == "/summary":
            params["kind"] = "morning" if "morning" in args_text.lower() else "current"
            params["args"] = args_text
        if command in {"/notify", "/notify test"}:
            params["dry_run"] = command == "/notify test" or "--dry-run" in args_text
            params["args"] = "test" if command == "/notify test" else args_text
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if err is not None:
            return err
        assert result is not None
        if command == "/inbox":
            items = result.get("items") or []
            reply = f"Inbox: {len(items)} item(s)"
        elif command == "/attention":
            items = result.get("items") or []
            reply = f"Attention: {len(items)} item(s)"
        elif command == "/summary":
            counts = result.get("counts") or {}
            reply = f"Summary ({result.get('summary_kind')}): total={counts.get('total', 0)}"
        else:
            deliveries = result.get("deliveries") or []
            dry = " dry-run" if result.get("dry_run") else ""
            reply = f"Notify{dry}: {len(deliveries)} delivery record(s)"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/approvals":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="operator.approvals",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        requests = result.get("requests") or []
        reply = f"Approvals: {len(requests)} request(s)"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/channels":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="operator.channels",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        channels = result.get("channels") or []
        reply = f"Channels: {len(channels)} configured"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/approve":
        parsed = _parse_approve_slash(args_text)
        if parsed is None:
            return _error_outcome(
                "invalid_args",
                "usage: /approve <task-id> or /approve token <approval-token>",
            )
        method, params = parsed
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if err is not None:
            return err
        assert result is not None
        if method == "operator.approval.approve":
            reply = f"Approval: {result.get('status')}"
        else:
            reply = f"Task {params['task_id']} -> {result.get('state', 'approved')}"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/reject":
        from .approval_tokens import is_approval_token

        token = args_text.strip().split()[0] if args_text.strip() else ""
        if not token or not is_approval_token(token):
            return _error_outcome(
                "invalid_args",
                "usage: /reject <approval-token>",
            )
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="operator.approval.reject",
            params={"token": token},
        )
        if err is not None:
            return err
        assert result is not None
        reply = f"Rejected: {result.get('status')}"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command in {"/decision", "/dismiss"}:
        item_id = _parse_task_id_slash(text)
        if not item_id:
            return _error_outcome("invalid_args", f"usage: {command} <item-id>")
        method = {
            "/decision": "operator.decision",
            "/dismiss": "operator.dismiss",
        }[command]
        params = {"item_id": item_id}
        if command == "/decision":
            params["dry_run"] = True
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if err is not None:
            return err
        assert result is not None
        if command == "/decision":
            reply = f"Decision: {result.get('routed_method')}"
        else:
            reply = f"Dismissed: {result.get('item_id')}"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command in {"/doctor", "/repair", "/health", "/morning"}:
        method_params = {
            "/doctor": ("operator.doctor", {"dry_run": True}),
            "/repair": (
                "operator.repair",
                {"dry_run": "--apply" not in args_text, "apply": "--apply" in args_text},
            ),
            "/health": ("operator.health", {}),
            "/morning": ("operator.morning", {}),
        }[command]
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method_params[0],
            params=method_params[1],
        )
        if err is not None:
            return err
        assert result is not None
        if command == "/health":
            agents = result.get("agents") or []
            reply = f"Agent health: {len(agents)} agent(s)"
        elif command == "/morning":
            reply = (
                f"Morning handoff: completed={len(result.get('completed_tasks') or [])} "
                f"failed={len(result.get('failed_tasks') or [])}"
            )
        else:
            reply = f"{command} status={result.get('status', 'ok')}"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/pause all":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="global.pause",
            params={"reason": args_text or "operator pause"},
        )
        if err is not None:
            return err
        assert result is not None
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=f"Global pause enabled ({len(result.get('affected_projects') or [])} projects)",
            intent="status_question",
        )
    if command == "/resume all":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="global.resume",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=f"Global pause cleared ({len(result.get('resumed_projects') or [])} resumed)",
            intent="status_question",
        )
    if command in {"/brain", "/map", "/where", "/why", "/impact", "/context"}:
        args_text = text.strip()[len(command) :].strip()
        params: dict[str, Any] = {}
        if command == "/why":
            parsed = _parse_why_slash(args_text)
            if parsed is None:
                return _error_outcome("invalid_args", "usage: /why <task-id|path>")
            method, params = parsed
        else:
            method = {
                "/brain": "project.brain",
                "/map": "project.map",
                "/where": "project.where",
                "/impact": "project.impact",
                "/context": "project.context",
            }[command]
        if command == "/where":
            if not args_text:
                return _error_outcome("invalid_args", "usage: /where <query>")
            params["query"] = args_text
        elif command == "/impact":
            if not args_text:
                return _error_outcome("invalid_args", f"usage: {command} <path>")
            params["path"] = args_text
        elif command == "/context":
            if args_text:
                params["task_id"] = args_text.split()[0]
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if err is not None:
            return err
        assert result is not None
        if command == "/brain":
            snap = result.get("snapshot") or {}
            reply = (
                f"Brain: {snap.get('file_count', 0)} files, "
                f"head={str(snap.get('git_head', ''))[:8]}"
            )
        elif command == "/map":
            cards = result.get("cards") or []
            reply = f"Map: {len(cards)} card(s)"
        elif command == "/where":
            matches = result.get("matches") or []
            reply = f"Where: {len(matches)} match(es)"
        elif command == "/why":
            if method == "operator.explain_failure":
                reply = (
                    f"Why {result.get('task_id')}: "
                    f"{result.get('classified_reason')} — {result.get('next_action')}"
                )
            else:
                related = result.get("related") or []
                reply = f"Impact: {len(related)} related path(s)"
        elif command == "/impact":
            related = result.get("related") or []
            reply = f"Impact: {len(related)} related path(s)"
        else:
            reply = f"Context packet: {result.get('packet_id')}"
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/agents":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="agent.list",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        agents = result.get("agents") or []
        lines = ["Agents:"]
        for entry in agents:
            lines.append(
                f"  {entry.get('agent_id')} [{entry.get('role')}] "
                f"enabled={entry.get('enabled')} health={entry.get('health_status')}"
            )
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply="\n".join(lines) if agents else "Agents — (none configured)",
            intent="status_question",
        )
    if command == "/agent":
        agent_id = args_text.strip().split()[0] if args_text.strip() else ""
        if not agent_id:
            return _error_outcome("invalid_args", "usage: /agent <id>")
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="agent.detail",
            params={"agent_id": agent_id},
        )
        if err is not None:
            return err
        assert result is not None
        profile = result.get("profile") or {}
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=(
                f"Agent {agent_id}: role={result.get('role')} "
                f"risk={profile.get('risk_tier')} "
                f"review={profile.get('review_strength')}"
            ),
            intent="status_question",
        )
    if command == "/route":
        task_id = args_text.strip().split()[0] if args_text.strip() else ""
        if not task_id:
            return _error_outcome("invalid_args", "usage: /route <task-id>")
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="agent.route.preview",
            params={"task_id": task_id},
        )
        if err is not None:
            return err
        assert result is not None
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=(
                f"Route {task_id}: agent={result.get('selected_agent_id') or '(none)'} "
                f"reason={result.get('reason')}"
            ),
            intent="status_question",
        )
    if command == "/benchmark agents":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="agent.benchmark",
            params={"scope": "agents"},
        )
        if err is not None:
            return err
        assert result is not None
        results = result.get("results") or []
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=f"Benchmark agents: {len(results)} run(s)",
            intent="status_question",
        )
    if command == "/recoveries":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.recoveries",
            params={"status": "pending"},
        )
        if err is not None:
            return err
        assert result is not None
        proposals = result.get("proposals") or []
        if not proposals:
            reply = "Recoveries: none pending"
        else:
            lines = ["Recoveries:"]
            for proposal in proposals:
                lines.append(
                    f"  {proposal.get('task_id')} -> {proposal.get('proposal_type')}: "
                    f"{proposal.get('title')}"
                )
            reply = "\n".join(lines)
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/overnight":
        method, params = _parse_overnight_slash(text)
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if err is not None:
            return err
        assert result is not None
        reply = (
            f"Overnight: quiet {result.get('quiet_start')}-{result.get('quiet_end')}"
        )
        latest = result.get("latest_summary")
        if latest:
            reply += (
                f"; last summary tasks_completed={latest.get('tasks_completed', 0)}"
            )
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=reply,
            intent="status_question",
        )
    if command == "/plan":
        from .project_operability import format_plan_text

        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.plan",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=format_plan_text(result),
            intent="status_question",
        )
    if command == "/scan":
        from .project_operability import format_scan_text

        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.scan",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=format_scan_text(result),
            intent="status_question",
        )
    if command in {"/jump", "/open"}:
        from .project_operability import format_jump_text

        params = _parse_jump_slash(text)
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.jump",
            params=params,
        )
        if err is not None:
            return err
        assert result is not None
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=format_jump_text(result),
            intent="status_question",
        )
    if command == "/profile":
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.profile",
            params={},
        )
        if err is not None:
            return err
        assert result is not None
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=(
                f"Profile: {result.get('detected_profile', 'unknown')} "
                f"preset={result.get('recommended_preset', 'observe')}"
            ),
            intent="status_question",
        )
    if command == "/onboard":
        method, params = _parse_onboard_slash(args_text)
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if err is not None:
            return err
        assert result is not None
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=f"Onboarding {method.split('.')[-1]} preset={params.get('preset', 'observe')}",
            intent="status_question",
        )
    if command in {"/simulate", "/what-if"}:
        method, params = _parse_simulation_slash(args_text)
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if err is not None:
            return err
        assert result is not None
        if method == "project.onboard.simulate":
            preset = str(params.get("preset") or "overnight")
            return PromptOutcome(
                ok=True,
                project_id=project_id,
                user_reply=f"Simulated preset {preset}: autonomy={result.get('autonomy_enabled')}",
                intent="status_question",
            )
        report = result.get("report") or {}
        scheduled = len(report.get("scheduled_projects") or [])
        skipped = len(report.get("skipped_projects") or [])
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=(
                f"FORECAST: {scheduled} project(s) would run, "
                f"{skipped} skipped (run {result.get('simulation_run_id')})"
            ),
            intent="status_question",
        )
    if command == "/fleet":
        root = args_text.strip() or str(Path.cwd())
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="fleet.scan",
            params={"root": root},
        )
        if err is not None:
            return err
        assert result is not None
        repos = result.get("repos") or []
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=f"Fleet scan: {len(repos)} repo(s)",
            intent="status_question",
        )
    if command == "/rollback-onboard":
        snapshot_id = args_text.strip().split()[0] if args_text.strip() else ""
        if not snapshot_id:
            return _error_outcome("invalid_args", "usage: /rollback-onboard <snapshot-id>")
        result, err, _envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.onboard.rollback",
            params={"snapshot_id": snapshot_id},
        )
        if err is not None:
            return err
        assert result is not None
        return PromptOutcome(
            ok=True,
            project_id=project_id,
            user_reply=f"Rollback restored snapshot {snapshot_id}",
            intent="status_question",
        )
    return _error_outcome("unknown_slash", f"Unknown command: {command}. Use /help.")


def _chat_send(
    paths: RuntimePaths,
    *,
    project_id: str,
    text: str,
    goal_id: int | None,
    context_files: list[ContextFile] | None = None,
    execution_policy: dict[str, object] | None = None,
) -> tuple[PromptOutcome, ResponseEnvelope | None]:
    params: dict[str, Any] = {"text": text}
    if goal_id is not None:
        params["goal_id"] = goal_id
    if context_files:
        params["context_files"] = [{"path": item.path} for item in context_files]
    if execution_policy:
        params["execution_policy"] = execution_policy
    result, err, envelope = _send_rpc(
        paths,
        project_id=project_id,
        method="chat.send",
        params=params,
        timeout=CHAT_SEND_TIMEOUT,
    )
    if err is not None:
        return err, envelope
    assert result is not None
    context_metadata = result.get("context_files")
    if not isinstance(context_metadata, list) or not context_metadata:
        context_metadata = public_metadata_from_context_files(context_files or [])
    return (
        PromptOutcome(
            ok=True,
            project_id=project_id,
            goal_id=result.get("goal_id"),
            user_reply=str(result.get("user_reply") or ""),
            intent=str(result.get("intent") or "conversation"),
            admitted=int(result.get("admitted") or 0),
            rejected=int(result.get("rejected") or 0),
            accepted_task_ids=list(result.get("accepted_task_ids") or []),
            context_files=list(context_metadata),
            orchestration=_orchestration_from_chat_result(result),
        ),
        envelope,
    )


def _emit_outcome(outcome: PromptOutcome, *, mode: str) -> None:
    if mode == "json":
        print(json.dumps(outcome.to_json_dict(), ensure_ascii=False))
        return
    if outcome.user_reply:
        print(outcome.user_reply)
    orch = outcome.orchestration or {}
    next_action = str(orch.get("next_action") or "").strip()
    if next_action and mode == "text":
        admitted = int(orch.get("admitted") or outcome.admitted or 0)
        rejected = int(orch.get("rejected") or outcome.rejected or 0)
        print(f"Next: {next_action} (admitted={admitted}, rejected={rejected})")
    if not outcome.ok and outcome.error_message:
        print(f"error: {outcome.error_message}", file=sys.stderr)


def _finish_prompt(
    args: argparse.Namespace,
    outcome: PromptOutcome,
    *,
    envelope: ResponseEnvelope | None = None,
    exit_code: int | None = None,
) -> int:
    code = exit_code if exit_code is not None else (0 if outcome.ok else 1)
    if args.mode == "rpc":
        _emit_rpc(envelope if envelope is not None else _outcome_to_rpc(outcome))
        return code
    _emit_outcome(outcome, mode=args.mode)
    return code


def _emit_task_detail_json(payload: dict[str, Any], *, ok: bool = True) -> int:
    body = dict(payload)
    body["ok"] = ok
    print(json.dumps(body, ensure_ascii=False))
    return 0


def _rpc_slash(
    paths: RuntimePaths,
    project_id: str,
    text: str,
) -> tuple[ResponseEnvelope, int]:
    command, args_text = _parse_slash_command(text)
    if command == "/dashboard":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="supervisor.dashboard",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/doctor":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="operator.doctor",
            params={"dry_run": True},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/repair":
        apply = "--apply" in args_text
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="operator.repair",
            params={"dry_run": not apply, "apply": apply, "confirmed": apply},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/health":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="operator.health",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/morning":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="operator.morning",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/pause all":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="global.pause",
            params={"reason": args_text or "operator pause"},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/resume all":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="global.resume",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/task":
        task_id, action = _parse_task_slash(text)
        if not task_id:
            outcome = _error_outcome("invalid_args", "usage: /task <id> [approve|retry|cancel|log]")
            return _outcome_to_rpc(outcome), 1
        if action in {"approve", "retry", "cancel", "log"}:
            method = "project.task.log" if action == "log" else f"project.task.{action}"
            _, err, envelope = _send_rpc(
                paths,
                project_id=project_id,
                method=method,
                params={"task_id": task_id},
            )
            if envelope is not None:
                return envelope, 0
            return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.task",
            params={"args": task_id},
        )
        if envelope is not None and envelope.ok and envelope.result is not None:
            flat = flatten_task_detail_json(envelope.result)
            flat["ok"] = True
            return _local_rpc_envelope(ok=True, result=flat), 0
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/status":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.status",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/tasks":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.tasks",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/loop":
        method, params = _parse_loop_slash(text)
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/backlog":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.backlog",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/evals":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.evaluations",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/strategy":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.strategy",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command in {"/evidence", "/review", "/risk", "/merge-ready"}:
        task_id = _parse_task_id_slash(text)
        if not task_id:
            outcome = _error_outcome("invalid_args", f"usage: {command} <task-id>")
            return _outcome_to_rpc(outcome), 1
        method = {
            "/evidence": "project.evidence",
            "/review": "project.review",
            "/risk": "project.risk",
            "/merge-ready": "project.merge_ready",
        }[command]
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params={"task_id": task_id},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command in {"/deliver", "/ci", "/delivery"}:
        task_id = _parse_task_id_slash(text)
        if not task_id:
            outcome = _error_outcome("invalid_args", f"usage: {command} <task-id>")
            return _outcome_to_rpc(outcome), 1
        method = {
            "/deliver": "project.deliver",
            "/ci": "project.ci",
            "/delivery": "project.delivery",
        }[command]
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params={"task_id": task_id},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/prs":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.prs",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/heal":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.pr.heal",
            params={"dry_run": True},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/stale":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.pr.health",
            params={"stale_only": True},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/ci failures":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.pr.health",
            params={"ci_failed_only": True},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/reviews":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.pr.reviews",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/pr update":
        if not args_text:
            outcome = _error_outcome("invalid_args", "usage: /pr update <delivery-id>")
            return _outcome_to_rpc(outcome), 1
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.pr.update_evidence",
            params={"delivery_id": args_text.split()[0], "dry_run": True},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/rebase":
        if not args_text:
            outcome = _error_outcome("invalid_args", "usage: /rebase <delivery-id> [--apply]")
            return _outcome_to_rpc(outcome), 1
        apply = "--apply" in args_text
        delivery_id = args_text.replace("--apply", "").strip().split()[0]
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.pr.rebase",
            params={
                "delivery_id": delivery_id,
                "dry_run": not apply,
                "apply": apply,
            },
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/merge-policy":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.merge_policy",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command in {"/brain", "/map", "/where", "/why", "/impact", "/context"}:
        params: dict[str, Any] = {}
        if command == "/why":
            parsed = _parse_why_slash(args_text)
            if parsed is None:
                outcome = _error_outcome("invalid_args", "usage: /why <task-id|path>")
                return _outcome_to_rpc(outcome), 1
            method, params = parsed
        else:
            method = {
                "/brain": "project.brain",
                "/map": "project.map",
                "/where": "project.where",
                "/impact": "project.impact",
                "/context": "project.context",
            }[command]
        if command == "/where":
            if not args_text:
                outcome = _error_outcome("invalid_args", "usage: /where <query>")
                return _outcome_to_rpc(outcome), 1
            params["query"] = args_text
        elif command == "/impact":
            if not args_text:
                outcome = _error_outcome("invalid_args", f"usage: {command} <path>")
                return _outcome_to_rpc(outcome), 1
            params["path"] = args_text
        elif command == "/context" and args_text:
            params["task_id"] = args_text.split()[0]
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command in {"/inbox", "/attention", "/summary", "/notify", "/notify test"}:
        args_text = text.strip()[len(command) :].strip()
        method = {
            "/inbox": "operator.inbox",
            "/attention": "operator.attention",
            "/summary": "operator.summary",
            "/notify": "operator.notify",
            "/notify test": "operator.notify",
        }[command]
        params: dict[str, Any] = {}
        if command == "/summary":
            params["kind"] = "morning" if "morning" in args_text.lower() else "current"
            params["args"] = args_text
        if command in {"/notify", "/notify test"}:
            params["dry_run"] = command == "/notify test" or "--dry-run" in args_text
            params["args"] = "test" if command == "/notify test" else args_text
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/approvals":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="operator.approvals",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/channels":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="operator.channels",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/approve":
        parsed = _parse_approve_slash(args_text)
        if parsed is None:
            outcome = _error_outcome(
                "invalid_args",
                "usage: /approve <task-id> or /approve token <approval-token>",
            )
            return _outcome_to_rpc(outcome), 1
        method, params = parsed
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/reject":
        from .approval_tokens import is_approval_token

        token = args_text.strip().split()[0] if args_text.strip() else ""
        if not token or not is_approval_token(token):
            outcome = _error_outcome("invalid_args", "usage: /reject <approval-token>")
            return _outcome_to_rpc(outcome), 1
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="operator.approval.reject",
            params={"token": token},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command in {"/decision", "/dismiss"}:
        item_id = _parse_task_id_slash(text)
        if not item_id:
            outcome = _error_outcome("invalid_args", f"usage: {command} <item-id>")
            return _outcome_to_rpc(outcome), 1
        method = {
            "/decision": "operator.decision",
            "/dismiss": "operator.dismiss",
        }[command]
        params: dict[str, Any] = {"item_id": item_id}
        if command == "/decision":
            params["dry_run"] = True
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/agents":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="agent.list",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/agent":
        agent_id = args_text.strip().split()[0] if args_text.strip() else ""
        if not agent_id:
            outcome = _error_outcome("invalid_args", "usage: /agent <id>")
            return _outcome_to_rpc(outcome), 1
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="agent.detail",
            params={"agent_id": agent_id},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/route":
        task_id = args_text.strip().split()[0] if args_text.strip() else ""
        if not task_id:
            outcome = _error_outcome("invalid_args", "usage: /route <task-id>")
            return _outcome_to_rpc(outcome), 1
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="agent.route.preview",
            params={"task_id": task_id},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/benchmark agents":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="agent.benchmark",
            params={"scope": "agents"},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/recoveries":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.recoveries",
            params={"status": "pending"},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/overnight":
        method, params = _parse_overnight_slash(text)
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/plan":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.plan",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/scan":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.scan",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command in {"/jump", "/open"}:
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.jump",
            params=_parse_jump_slash(text),
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/profile":
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.profile",
            params={},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/onboard":
        method, params = _parse_onboard_slash(args_text)
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command in {"/simulate", "/what-if"}:
        method, params = _parse_simulation_slash(args_text)
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method=method,
            params=params,
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/fleet":
        root = args_text.strip() or str(Path.cwd())
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="fleet.scan",
            params={"root": root},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    if command == "/rollback-onboard":
        snapshot_id = args_text.strip().split()[0] if args_text.strip() else ""
        if not snapshot_id:
            outcome = _error_outcome("invalid_args", "usage: /rollback-onboard <snapshot-id>")
            return _outcome_to_rpc(outcome), 1
        _, err, envelope = _send_rpc(
            paths,
            project_id=project_id,
            method="project.onboard.rollback",
            params={"snapshot_id": snapshot_id},
        )
        if envelope is not None:
            return envelope, 0 if envelope.ok else 1
        return _outcome_to_rpc(err or _error_outcome("supervisor_error", "request failed")), 1
    outcome = _error_outcome("unknown_slash", f"Unknown command: {command}. Use /help.")
    return _outcome_to_rpc(outcome), 1


def run_cli_prompt(args: argparse.Namespace) -> int:
    resume = getattr(args, "resume", None)
    fork_goal_id = getattr(args, "fork", None)
    prompt_text = getattr(args, "prompt_text", "").strip()

    if resume == "" or resume is not None:
        pass
    elif fork_goal_id is not None:
        if not prompt_text:
            _emit_outcome(
                _error_outcome("fork_conflict", "fork instruction is required"),
                mode=args.mode,
            )
            return 1
    elif not prompt_text:
        if args.mode == "rpc":
            _emit_rpc(_local_rpc_envelope(ok=False, error="prompt text is required"))
            return 2
        print("error: prompt text is required", file=sys.stderr)
        return 2

    try:
        git_root = resolve_git_root(Path(args.root).resolve())
    except NotGitRepositoryError as exc:
        if args.mode == "rpc":
            _emit_rpc(_local_rpc_envelope(ok=False, error=str(exc)))
            return 2
        print(f"error: {exc}", file=sys.stderr)
        return 2

    paths = resolve_runtime_paths()
    paths.create()

    project_id, project_err = _resolve_project(paths, git_root)
    if project_err is not None:
        return _finish_prompt(args, project_err)

    assert project_id is not None

    try:
        ensure_supervisor(paths)
    except SupervisorIncompatibleError:
        print(f"error: {INCOMPATIBLE_SUPERVISOR_MESSAGE}", file=sys.stderr)
        return 1
    except SupervisorReadinessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if resume == "":
        outcome = _list_goal_candidates(paths, project_id)
        if not outcome.ok:
            _emit_outcome(outcome, mode=args.mode)
            return 1

        if _is_interactive_session(args):
            selected_goal_id = _interactive_resume_selection(
                list(outcome.candidates or [])
            )
            if selected_goal_id is None:
                return 1
            resume_err = _resume_goal(paths, project_id, selected_goal_id)
            if resume_err is not None:
                _emit_outcome(resume_err, mode=args.mode)
                return 1
            if prompt_text:
                execution_policy = _resolve_cli_execution_policy(
                    paths, git_root, args
                )
                context_tokens = list(
                    getattr(args, "context_file_tokens", []) or []
                )
                context_files, context_err = _load_cli_context(
                    git_root,
                    context_tokens,
                    cwd=Path.cwd(),
                )
                if context_err is not None:
                    _emit_outcome(context_err, mode=args.mode)
                    return 1
                outcome, envelope = _chat_send(
                    paths,
                    project_id=project_id,
                    text=prompt_text,
                    goal_id=selected_goal_id,
                    context_files=context_files,
                    execution_policy=execution_policy,
                )
                if args.mode == "rpc":
                    return _finish_prompt(args, outcome, envelope=envelope)
                if not outcome.ok:
                    _emit_outcome(outcome, mode=args.mode)
                    return 1
                _emit_outcome(outcome, mode=args.mode)
                return launch_tui(start_path=git_root)
            outcome = PromptOutcome(
                ok=True,
                project_id=project_id,
                goal_id=selected_goal_id,
                user_reply=f"Resumed goal {selected_goal_id}.",
            )
            _emit_outcome(outcome, mode=args.mode)
            return launch_tui(start_path=git_root)

        _emit_outcome(outcome, mode=args.mode)
        return 2

    if fork_goal_id is not None:
        outcome = _fork_goal(paths, project_id, fork_goal_id, prompt_text)
        _emit_outcome(outcome, mode=args.mode)
        if not outcome.ok:
            return 1
        if args.print_mode or args.no_tui:
            return 0
        return launch_tui(start_path=git_root)

    continue_goal = bool(getattr(args, "continue_goal", False))
    execution_policy = _resolve_cli_execution_policy(paths, git_root, args)
    context_tokens = list(getattr(args, "context_file_tokens", []) or [])
    context_files, context_err = _load_cli_context(
        git_root,
        context_tokens,
        cwd=Path.cwd(),
    )
    if context_err is not None:
        _emit_outcome(context_err, mode=args.mode)
        return 1

    if resume is not None:
        goal_id = int(resume)
        resume_err = _resume_goal(paths, project_id, goal_id)
        if resume_err is not None:
            _emit_outcome(resume_err, mode=args.mode)
            return 1
        if not prompt_text:
            outcome = PromptOutcome(
                ok=True,
                project_id=project_id,
                goal_id=goal_id,
                user_reply=f"Resumed goal {goal_id}.",
            )
            _emit_outcome(outcome, mode=args.mode)
            if args.print_mode or args.no_tui:
                return 0
            return launch_tui(start_path=git_root)
        outcome, envelope = _chat_send(
            paths,
            project_id=project_id,
            text=prompt_text,
            goal_id=goal_id,
            context_files=context_files,
            execution_policy=execution_policy,
        )
        if args.mode == "rpc":
            return _finish_prompt(args, outcome, envelope=envelope)
        if not outcome.ok:
            _emit_outcome(outcome, mode=args.mode)
            return 1
        _emit_outcome(outcome, mode=args.mode)
        if args.print_mode or args.no_tui:
            return 0
        return launch_tui(start_path=git_root)

    if prompt_text.startswith("/"):
        if args.mode == "rpc":
            envelope, exit_code = _rpc_slash(paths, project_id, prompt_text)
            _emit_rpc(envelope)
            return exit_code
        if args.mode == "json":
            from .admin_json import emit_envelope, envelope

            slash_command = prompt_text.strip().split()[0].lower()
            if slash_command == "/plan":
                result, err, _envelope = _send_rpc(
                    paths,
                    project_id=project_id,
                    method="project.plan",
                    params={},
                )
                if err is not None:
                    return emit_envelope(
                        envelope(
                            command="plan",
                            ok=False,
                            errors=[_outcome_to_admin_error(err)],
                        )
                    )
                assert result is not None
                return emit_envelope(envelope(command="plan", ok=True, data=result))
            if slash_command == "/scan":
                result, err, _envelope = _send_rpc(
                    paths,
                    project_id=project_id,
                    method="project.scan",
                    params={},
                )
                if err is not None:
                    return emit_envelope(
                        envelope(
                            command="scan",
                            ok=False,
                            errors=[_outcome_to_admin_error(err)],
                        )
                    )
                assert result is not None
                return emit_envelope(envelope(command="scan", ok=True, data=result))
            if slash_command in {"/jump", "/open"}:
                result, err, _envelope = _send_rpc(
                    paths,
                    project_id=project_id,
                    method="project.jump",
                    params=_parse_jump_slash(prompt_text),
                )
                if err is not None:
                    return emit_envelope(
                        envelope(
                            command=slash_command.lstrip("/"),
                            ok=False,
                            errors=[_outcome_to_admin_error(err)],
                        )
                    )
                assert result is not None
                return emit_envelope(
                    envelope(command=slash_command.lstrip("/"), ok=True, data=result)
                )
            task_id, action = _parse_task_slash(prompt_text)
            if task_id and action is None:
                result, err, _envelope = _send_rpc(
                    paths,
                    project_id=project_id,
                    method="project.task",
                    params={"args": task_id},
                )
                if err is not None:
                    _emit_task_detail_json(
                        {
                            "error": {
                                "code": err.error_code,
                                "message": err.error_message,
                            },
                            "failure_class": "unknown",
                            "human_review_required": False,
                        },
                        ok=False,
                    )
                    return 0
                assert result is not None
                _emit_task_detail_json(flatten_task_detail_json(result))
                return 0
        outcome = _handle_slash(paths, project_id, prompt_text)
    else:
        goal_id, goal_err = _resolve_goal_id(
            paths,
            project_id,
            continue_goal=continue_goal,
        )
        if goal_err is not None:
            return _finish_prompt(args, goal_err)
        assert goal_id is not None
        outcome, envelope = _chat_send(
            paths,
            project_id=project_id,
            text=prompt_text,
            goal_id=goal_id if continue_goal else None,
            context_files=context_files,
            execution_policy=execution_policy,
        )
        if args.mode == "rpc":
            return _finish_prompt(args, outcome, envelope=envelope)

    if not outcome.ok:
        return _finish_prompt(args, outcome)

    _emit_outcome(outcome, mode=args.mode)
    if args.print_mode or args.no_tui:
        return 0

    return launch_tui(start_path=git_root)