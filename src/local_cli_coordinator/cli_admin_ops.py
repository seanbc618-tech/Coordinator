"""Dry-run-first admin operations for worktree cleanup, rollback, and drain."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .config import CoordinatorConfig
from .db import connect, get_task, init_db, transition_task
from .gitops import list_worktrees, remove_worktree, worktree_has_uncommitted_changes
from .ops_safety import OpsPlan, confirm_token_for_plan, verify_confirm_token
from .runtime_paths import RuntimePaths, resolve_runtime_paths


_CLEANUP_ELIGIBLE_TASK_STATES = frozenset({"done"})


def _extract_task_id_from_path(wt_path: Path) -> str | None:
    return wt_path.name if wt_path.name.startswith("task-") else None


def _use_global_home(root: Path) -> bool:
    """Use COORDINATOR_HOME only for implicit ``--root .`` invocations."""
    if not os.environ.get("COORDINATOR_HOME"):
        return False
    return str(root) in {".", "./"}


def _resolve_admin_context(
    root: Path,
    db_name: str,
) -> tuple[Path, CoordinatorConfig, sqlite3.Connection, RuntimePaths | None]:
    if _use_global_home(root):
        paths = resolve_runtime_paths()
        paths.create()
        from .config_runtime import load_config_for_paths

        config = load_config_for_paths(paths)
        conn = connect(paths.database)
        init_db(conn)
        return paths.data_dir, config, conn, paths

    from .config import try_load_config

    config, config_error = try_load_config(root)
    if config_error is not None:
        raise RuntimeError(config_error)
    db_path = Path(db_name)
    if not db_path.is_absolute():
        db_path = root / db_path
    conn = connect(db_path)
    init_db(conn)
    return root, config, conn, None


def plan_cleanup_worktrees(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    *,
    force: bool = False,
    project_id: str | None = None,
) -> OpsPlan:
    items: list[str] = []
    for repo_id, repo_config in config.repos.items():
        repo_path = repo_config.path
        if not repo_path.exists():
            continue
        worktrees_root = (root / "worktrees" / repo_id).resolve()
        for wt in list_worktrees(repo_path):
            if wt.get("branch", "") == "":
                continue
            wt_path = Path(wt.get("worktree", "")).resolve()
            if worktrees_root not in wt_path.parents:
                continue
            task_id = _extract_task_id_from_path(wt_path)
            if task_id is None:
                continue
            try:
                task = get_task(conn, task_id)
            except KeyError:
                continue
            if project_id is not None and task.get("project_id") != project_id:
                continue
            if task["state"] not in _CLEANUP_ELIGIBLE_TASK_STATES:
                continue
            if worktree_has_uncommitted_changes(wt_path) and not force:
                continue
            items.append(str(wt_path))
    return OpsPlan(
        action="cleanup-worktrees",
        summary=f"remove {len(items)} worktree(s)",
        items=sorted(items),
        extra={"force": force, "project_id": project_id},
    )


def apply_cleanup_worktrees(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    plan: OpsPlan,
    *,
    force: bool = False,
) -> tuple[int, int, int]:
    removed = 0
    skipped = 0
    errors = 0
    for wt_path_str in plan.items:
        wt_path = Path(wt_path_str).resolve()
        repo_path = None
        for candidate_repo, _repo_id in ((repo.path.resolve(), repo_id) for repo_id, repo in config.repos.items()):
            if candidate_repo.exists():
                for wt in list_worktrees(candidate_repo):
                    if Path(wt.get("worktree", "")).resolve() == wt_path:
                        repo_path = candidate_repo
                        break
            if repo_path is not None:
                break
        if repo_path is None:
            skipped += 1
            continue
        task_id = _extract_task_id_from_path(wt_path)
        try:
            remove_worktree(repo_path, wt_path, force=force)
            if task_id:
                transition_task(
                    conn,
                    task_id,
                    get_task(conn, task_id)["state"],
                    f"cleanup: removed worktree {wt_path}",
                )
            removed += 1
        except RuntimeError:
            errors += 1
    return removed, skipped, errors


def plan_task_rollback(conn: sqlite3.Connection, task_id: str) -> OpsPlan:
    try:
        task = get_task(conn, task_id)
    except KeyError as exc:
        raise RuntimeError(f"task {task_id!r} not found") from exc
    worktree = task.get("worktree_path") or ""
    items = [worktree] if worktree else []
    status = ""
    if worktree:
        wt_path = Path(worktree)
        if wt_path.is_dir():
            result = subprocess.run(
                ["git", "status", "--short"],
                cwd=wt_path,
                capture_output=True,
                text=True,
                check=False,
            )
            status = result.stdout.strip()
    return OpsPlan(
        action="task-rollback",
        summary=f"reset worktree for task {task_id}",
        items=items,
        extra={"task_id": task_id, "git_status": status},
    )


def apply_task_rollback(conn: sqlite3.Connection, task_id: str, plan: OpsPlan) -> None:
    if not plan.items:
        raise RuntimeError(f"task {task_id!r} has no worktree to rollback")
    wt_path = Path(plan.items[0])
    if not wt_path.is_dir():
        raise RuntimeError(f"worktree not found: {wt_path}")
    for argv in (["git", "reset", "--hard"], ["git", "clean", "-fd"]):
        result = subprocess.run(argv, cwd=wt_path, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git failed")
    old_state = get_task(conn, task_id)["state"]
    transition_task(conn, task_id, old_state, f"rollback applied for {wt_path}")


def plan_supervisor_drain(conn: sqlite3.Connection) -> OpsPlan:
    rows = conn.execute(
        """
        select tl.task_id, tl.agent_id, tl.project_id, t.state
        from task_leases tl
        join tasks t on t.id = tl.task_id
        where tl.released_at is null
          and tl.expires_at > datetime('now')
        order by tl.id
        """
    ).fetchall()
    items = [
        f"{row['project_id']}:{row['task_id']} agent={row['agent_id']} state={row['state']}"
        for row in rows
    ]
    running = conn.execute(
        "select id, project_id from tasks where state = 'running' order by id"
    ).fetchall()
    for row in running:
        entry = f"{row['project_id']}:{row['id']} state=running"
        if entry not in items:
            items.append(entry)
    return OpsPlan(
        action="supervisor-drain",
        summary=f"{len(items)} active lease(s)/running task(s)",
        items=items,
        extra={},
    )


def format_plan_output(plan: OpsPlan, *, dry_run: bool) -> str:
    lines = [f"action: {plan.action}", f"summary: {plan.summary}"]
    if plan.extra.get("git_status"):
        lines.append(f"git status:\n{plan.extra['git_status']}")
    if plan.items:
        lines.append("items:")
        for item in plan.items:
            lines.append(f"  {item}")
    else:
        lines.append("items: (none)")
    token = confirm_token_for_plan(plan)
    lines.append(f"confirm_token: {token}")
    if dry_run:
        lines.append("mode: dry-run (pass --apply --confirm <token> to execute)")
    return "\n".join(lines)


def run_cleanup_worktrees(
    root: Path,
    db_name: str,
    *,
    dry_run: bool,
    apply: bool,
    confirm: str | None,
    force: bool,
    project_id: str | None,
) -> tuple[int, str]:
    try:
        data_root, config, conn, _paths = _resolve_admin_context(root, db_name)
    except RuntimeError as exc:
        return 1, f"error: {exc}"

    try:
        plan = plan_cleanup_worktrees(
            conn, config, data_root, force=force, project_id=project_id
        )
        if dry_run or not apply:
            return 0, format_plan_output(plan, dry_run=True)
        err = verify_confirm_token(plan, confirm)
        if err:
            return 1, f"error: {err}"
        removed, skipped, errors = apply_cleanup_worktrees(
            conn, config, data_root, plan, force=force
        )
        return (
            1 if errors else 0,
            f"removed: {removed}, skipped: {skipped}, errors: {errors}",
        )
    finally:
        conn.close()


def run_task_rollback(
    root: Path,
    db_name: str,
    task_id: str,
    *,
    dry_run: bool,
    apply: bool,
    confirm: str | None,
) -> tuple[int, str]:
    try:
        _data_root, _config, conn, _paths = _resolve_admin_context(root, db_name)
    except RuntimeError as exc:
        return 1, f"error: {exc}"

    try:
        plan = plan_task_rollback(conn, task_id)
        if dry_run or not apply:
            return 0, format_plan_output(plan, dry_run=True)
        err = verify_confirm_token(plan, confirm)
        if err:
            return 1, f"error: {err}"
        apply_task_rollback(conn, task_id, plan)
        return 0, f"rollback applied for task {task_id}"
    except RuntimeError as exc:
        return 1, f"error: {exc}"
    finally:
        conn.close()


def run_supervisor_drain(
    root: Path,
    db_name: str,
    *,
    dry_run: bool,
) -> tuple[int, str]:
    try:
        _data_root, _config, conn, _paths = _resolve_admin_context(root, db_name)
    except RuntimeError as exc:
        return 1, f"error: {exc}"

    try:
        plan = plan_supervisor_drain(conn)
        return 0, format_plan_output(plan, dry_run=dry_run or True)
    finally:
        conn.close()