"""Read-only project operability payloads for /plan, /scan, and /jump."""

from __future__ import annotations

import shlex
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .autonomous_loop_db import backlog_status_counts
from .autonomous_runs import get_active_run_session, run_snapshot_to_payload
from .config import CoordinatorConfig
from .db import project_get_task_detail, project_task_counts, task_latest_attempt
from .goals import active_goal_for_project
from .projects import get_project
from .runtime_paths import RuntimePaths
from .supervisor_process import supervisor_log_path
from .task_control import TaskControlError, build_task_detail_payload


def _resolve_repo_config(
    project: sqlite3.Row,
    config: CoordinatorConfig | None,
) -> tuple[Path, list[str]]:
    repo_root = Path(project["canonical_path"]).resolve()
    verify_commands = [
        line for line in str(project["verify_commands"] or "").splitlines() if line
    ]
    if config is not None:
        repo = config.repos.get(project["repo_id"])
        if repo is not None:
            if repo.verify_commands:
                verify_commands = list(repo.verify_commands)
            repo_root = repo.path.resolve()
    return repo_root, verify_commands


def _agent_binary_available(command: str) -> bool:
    if "mock-provider" in command:
        return True
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if not argv:
        return False
    binary = argv[0]
    if binary in {"true", "false", "echo", "cat"}:
        return True
    candidate = Path(binary)
    if candidate.is_file():
        return True
    return shutil.which(binary) is not None


def _git_root_exists(repo_root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _working_tree_summary(repo_root: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"clean": False, "changed_files": 0, "error": str(exc)}
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return {"clean": False, "changed_files": 0, "error": detail or "git status failed"}
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return {"clean": not lines, "changed_files": len(lines)}


def _plan_next_action(
    *,
    task_counts: dict[str, int],
    backlog_counts: dict[str, int],
    run: dict[str, Any] | None,
) -> str:
    if task_counts.get("running", 0):
        return "wait for running task"
    if task_counts.get("ready", 0):
        return "daemon will schedule ready task(s)"
    if backlog_counts.get("ready", 0):
        return "review ready backlog"
    if run and run.get("status") == "running":
        decision = str(run.get("last_decision") or "wait")
        return f"autonomous run {decision}"
    if task_counts.get("failed", 0):
        return "inspect failed tasks"
    return "wait for new work"


def build_project_plan_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    config: CoordinatorConfig | None = None,
) -> dict[str, Any]:
    goal = active_goal_for_project(conn, project_id)
    goal_id = int(goal["id"]) if goal is not None else None
    task_counts = project_task_counts(conn, project_id=project_id)
    backlog_counts = backlog_status_counts(
        conn, project_id=project_id, goal_id=goal_id
    )
    active_run = get_active_run_session(conn, project_id=project_id)
    run_payload = (
        run_snapshot_to_payload(active_run) if active_run is not None else None
    )
    return {
        "goal": (
            {
                "id": goal["id"],
                "status": goal["status"],
                "title": goal["title"],
            }
            if goal is not None
            else None
        ),
        "run": (
            {
                "status": run_payload["status"],
                "last_decision": run_payload.get("last_decision"),
            }
            if run_payload is not None
            else None
        ),
        "backlog": {
            "ready": backlog_counts.get("ready", 0),
            "blocked": backlog_counts.get("blocked", 0),
        },
        "tasks": {
            "running": task_counts.get("running", 0),
            "failed": task_counts.get("failed", 0),
        },
        "next": _plan_next_action(
            task_counts=task_counts,
            backlog_counts=backlog_counts,
            run=run_payload,
        ),
    }


def build_project_scan_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    paths: RuntimePaths,
    config: CoordinatorConfig | None = None,
) -> dict[str, Any]:
    project = get_project(conn, project_id)
    if project is None:
        raise ValueError(f"project {project_id!r} not registered")

    repo_root, verify_commands = _resolve_repo_config(project, config)
    git_root_exists = _git_root_exists(repo_root)
    worktree = _working_tree_summary(repo_root) if git_root_exists else {
        "clean": False,
        "changed_files": 0,
        "error": "git root missing",
    }
    task_counts = project_task_counts(conn, project_id=project_id)
    active_run = get_active_run_session(conn, project_id=project_id)
    agents: dict[str, dict[str, Any]] = {}
    if config is not None:
        for agent_id, agent in config.agents.items():
            agents[agent_id] = {
                "role": agent.role,
                "available": _agent_binary_available(agent.command),
                "command": agent.command,
            }

    return {
        "project_id": project_id,
        "registered": True,
        "git_root": str(repo_root),
        "git_root_exists": git_root_exists,
        "working_tree": worktree,
        "verify_commands": verify_commands,
        "agents": agents,
        "failed_tasks": task_counts.get("failed", 0),
        "active_run": (
            run_snapshot_to_payload(active_run) if active_run is not None else None
        ),
    }


def _resolve_task_log_path(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> str:
    attempt = task_latest_attempt(conn, task_id)
    if attempt is not None and attempt["log_path"]:
        return str(attempt["log_path"])
    try:
        detail = build_task_detail_payload(
            conn, project_id=project_id, task_id=task_id
        )
    except TaskControlError:
        return ""
    latest_attempt = detail.get("latest_attempt")
    if isinstance(latest_attempt, dict) and latest_attempt.get("log_path"):
        return str(latest_attempt["log_path"])
    for artifact in detail.get("artifacts") or []:
        if artifact.get("kind") in {"agent_log", "attempt_log"} and artifact.get("path"):
            return str(artifact["path"])
    return ""


def build_project_jump_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    target: str,
    paths: RuntimePaths,
    alias: str | None = None,
) -> dict[str, Any]:
    cleaned = target.strip()
    if not cleaned:
        raise ValueError("jump target is required")

    parts = cleaned.split()
    head = parts[0].lower()
    qualifier = parts[1].lower() if len(parts) > 1 else ""

    if head == "goal":
        goal = active_goal_for_project(conn, project_id)
        if goal is None:
            raise ValueError("no active goal")
        return {
            "target_type": "goal",
            "path": "",
            "hint": f"/goal — goal {goal['id']} [{goal['status']}] {goal['title']}",
            "alias": alias,
        }

    if head in {"supervisor.log", "supervisor"}:
        log_path = supervisor_log_path(paths)
        return {
            "target_type": "supervisor.log",
            "path": str(log_path),
            "hint": f"Supervisor log: {log_path}",
            "alias": alias,
        }

    if head == "worktree":
        row = conn.execute(
            """
            select worktree_path
            from tasks
            where project_id = ?
              and worktree_path is not null
              and worktree_path != ''
            order by updated_at desc
            limit 1
            """,
            (project_id,),
        ).fetchone()
        if row is None or not row["worktree_path"]:
            raise ValueError("no worktree path recorded")
        path = str(row["worktree_path"])
        return {
            "target_type": "worktree",
            "path": path,
            "hint": f"Worktree: {path}",
            "alias": alias,
        }

    task_id = parts[0]
    row = project_get_task_detail(conn, project_id=project_id, task_id=task_id)
    if row is None:
        raise ValueError(f"task {task_id!r} not found")

    if qualifier == "log":
        log_path = _resolve_task_log_path(conn, project_id=project_id, task_id=task_id)
        hint = (
            f"Task log: {log_path}"
            if log_path
            else f"No log path recorded for {task_id}; try /task {task_id} log"
        )
        return {
            "target_type": "task.log",
            "task_id": task_id,
            "path": log_path,
            "hint": hint,
            "alias": alias,
        }

    if qualifier == "worktree":
        path = str(row["worktree_path"] or "")
        if not path:
            raise ValueError(f"task {task_id!r} has no worktree path")
        return {
            "target_type": "task.worktree",
            "task_id": task_id,
            "path": path,
            "hint": f"Worktree: {path}",
            "alias": alias,
        }

    path = str(row["worktree_path"] or "")
    return {
        "target_type": "task",
        "task_id": task_id,
        "path": path,
        "hint": f"/task {task_id}",
        "alias": alias,
    }


def format_plan_text(payload: dict[str, Any]) -> str:
    lines = ["Plan:"]
    goal = payload.get("goal")
    if isinstance(goal, dict):
        lines.append(f"  goal {goal['id']} [{goal['status']}] {goal['title']}")
    else:
        lines.append("  goal: none")
    run = payload.get("run")
    if isinstance(run, dict):
        lines.append(
            f"  run: {run.get('status')} "
            f"(last_decision={run.get('last_decision')})"
        )
    else:
        lines.append("  run: none")
    backlog = payload.get("backlog") or {}
    lines.append(
        "  backlog: "
        f"ready={backlog.get('ready', 0)} blocked={backlog.get('blocked', 0)}"
    )
    tasks = payload.get("tasks") or {}
    lines.append(
        "  tasks: "
        f"running={tasks.get('running', 0)} failed={tasks.get('failed', 0)}"
    )
    lines.append(f"  next: {payload.get('next', 'wait')}")
    return "\n".join(lines)


def format_scan_text(payload: dict[str, Any]) -> str:
    worktree = payload.get("working_tree") or {}
    clean_text = "clean" if worktree.get("clean") else "dirty"
    if worktree.get("error"):
        clean_text = f"unavailable ({worktree['error']})"
    verify_commands = payload.get("verify_commands") or []
    verify_text = ", ".join(verify_commands) if verify_commands else "(none)"
    active_run = payload.get("active_run")
    run_text = (
        f"{active_run.get('status')} ({active_run.get('id')})"
        if isinstance(active_run, dict)
        else "none"
    )
    lines = [
        f"Scan [{payload.get('project_id')}]",
        f"  project: registered",
        f"  git root: {'ok' if payload.get('git_root_exists') else 'missing'}",
        f"  working tree: {clean_text}",
        f"  verify commands: {verify_text}",
        f"  failed tasks: {payload.get('failed_tasks', 0)}",
        f"  active run: {run_text}",
    ]
    agents = payload.get("agents") or {}
    if agents:
        agent_bits = [
            f"{agent_id}={'ok' if info.get('available') else 'missing'}"
            for agent_id, info in sorted(agents.items())
        ]
        lines.append(f"  agents: {', '.join(agent_bits)}")
    return "\n".join(lines)


def format_jump_text(payload: dict[str, Any]) -> str:
    return str(payload.get("hint") or payload.get("path") or "(no target)")