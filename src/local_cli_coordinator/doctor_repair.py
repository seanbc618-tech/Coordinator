"""Safe doctor repair planner for Coordinator runtime paths."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping

from .config import CoordinatorConfig
from .db import init_db
from .locks import _is_stale, _read_lock
from .operator_hardening import (
    finish_diagnostic_run,
    record_diagnostic_run,
    record_repair_audit_event,
)
from .runtime_paths import RuntimePaths
from .supervisor_process import ping_supervisor

SAFE_REPAIR_KEYS = frozenset({
    "missing-state-dir",
    "missing-runs-dir",
    "missing-generated-dir",
    "stale-lock",
    "stale-socket",
    "migration-not-applied",
    "missing-config-file",
})


def _coordinator_home(paths: RuntimePaths) -> Path:
    return paths.config_dir.parent


def _path_in_coordinator_home(path: Path, paths: RuntimePaths) -> bool:
    home = _coordinator_home(paths).resolve()
    try:
        candidate = path.resolve()
    except OSError:
        return False
    if path.is_symlink():
        return False
    try:
        candidate.relative_to(home)
        return True
    except ValueError:
        return False


def _read_lock_pid(lock_path: Path) -> int | None:
    info = _read_lock(lock_path)
    return info.pid if info is not None else None


def run_readiness_findings(
    paths: RuntimePaths,
    conn: sqlite3.Connection,
    config: CoordinatorConfig | None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not paths.state_dir.is_dir():
        findings.append(
            {
                "finding_key": "missing-state-dir",
                "path": str(paths.state_dir),
                "severity": "warn",
            }
        )
    runs_dir = paths.data_dir / "runs"
    if not runs_dir.is_dir():
        findings.append(
            {
                "finding_key": "missing-runs-dir",
                "path": str(runs_dir),
                "severity": "warn",
            }
        )
    for row in conn.execute("select id, canonical_path from projects").fetchall():
        generated = Path(str(row["canonical_path"])) / "tasks" / "generated"
        if not generated.is_dir():
            findings.append(
                {
                    "finding_key": "missing-generated-dir",
                    "path": str(generated),
                    "project_id": str(row["id"]),
                    "severity": "warn",
                }
            )
    if paths.lock.exists():
        pid = _read_lock_pid(paths.lock)
        if pid is not None and _is_stale(pid):
            findings.append(
                {
                    "finding_key": "stale-lock",
                    "path": str(paths.lock),
                    "pid": pid,
                    "severity": "warn",
                }
            )
    if paths.socket.exists():
        stale = not ping_supervisor(paths, timeout=0.5)
        if stale:
            findings.append(
                {
                    "finding_key": "stale-socket",
                    "path": str(paths.socket),
                    "severity": "warn",
                }
            )
    for name in ("agents.toml", "repos.toml", "policy.toml"):
        config_path = paths.config_dir / name
        if not config_path.is_file():
            findings.append(
                {
                    "finding_key": "missing-config-file",
                    "path": str(config_path),
                    "severity": "warn",
                    "sample_command": "coordinator init --yes",
                }
            )
    pending = {
        row["version"]
        for row in conn.execute(
            "select version from schema_migrations"
        ).fetchall()
    }
    from .db import iter_migration_scripts

    for name, _sql in iter_migration_scripts():
        if name not in pending:
            findings.append(
                {
                    "finding_key": "migration-not-applied",
                    "migration": name,
                    "severity": "warn",
                }
            )
            break
    if config is None and paths.config_dir.is_dir():
        findings.append(
            {
                "finding_key": "missing-config-file",
                "path": str(paths.config_dir),
                "severity": "warn",
                "message": "configuration is not loadable",
            }
        )
    return findings


def plan_repairs(findings: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    repairs: list[dict[str, Any]] = []
    for finding in findings:
        key = str(finding.get("finding_key") or finding.get("repair_key") or "")
        if not key:
            continue
        repair = {
            "repair_key": key,
            "status": "planned",
            "path": finding.get("path"),
            "project_id": finding.get("project_id"),
            "pid": finding.get("pid"),
        }
        if key == "missing-config-file":
            repair["status"] = "skipped"
            repair["message"] = finding.get("sample_command") or "run coordinator init --yes"
        repairs.append(repair)
    return repairs


def _apply_single_repair(
    conn: sqlite3.Connection,
    paths: RuntimePaths,
    repair: Mapping[str, Any],
    *,
    diagnostic_run_id: str | None = None,
) -> dict[str, Any]:
    key = str(repair.get("repair_key") or "")
    if key not in SAFE_REPAIR_KEYS:
        raise ValueError(f"unsafe repair key: {key!r}")
    result = dict(repair)
    result.setdefault("status", "planned")
    if key == "missing-state-dir":
        paths.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        result["status"] = "applied"
        return result
    if key == "missing-runs-dir":
        (paths.data_dir / "runs").mkdir(parents=True, exist_ok=True, mode=0o700)
        result["status"] = "applied"
        return result
    if key == "missing-generated-dir":
        path = Path(str(repair.get("path") or ""))
        if path:
            path.mkdir(parents=True, exist_ok=True)
            result["status"] = "applied"
        else:
            result["status"] = "skipped"
        return result
    if key == "stale-lock":
        lock_path = Path(str(repair.get("path") or paths.lock))
        if lock_path.is_symlink() or not _path_in_coordinator_home(lock_path, paths):
            result["status"] = "skipped"
            result["reason"] = "symlink or out-of-home lock"
            return result
        pid = repair.get("pid")
        if pid is None:
            pid = _read_lock_pid(lock_path)
        if pid is None or not _is_stale(int(pid)):
            result["status"] = "skipped"
            result["reason"] = "pid still running"
            return result
        lock_path.unlink(missing_ok=True)
        result["status"] = "applied"
        return result
    if key == "stale-socket":
        socket_path = Path(str(repair.get("path") or paths.socket))
        if socket_path.is_symlink() or not _path_in_coordinator_home(socket_path, paths):
            result["status"] = "skipped"
            result["reason"] = "symlink or out-of-home socket"
            return result
        if ping_supervisor(paths, timeout=0.5):
            result["status"] = "skipped"
            result["reason"] = "supervisor still responds"
            return result
        socket_path.unlink(missing_ok=True)
        result["status"] = "applied"
        return result
    if key == "migration-not-applied":
        init_db(conn)
        result["status"] = "applied"
        return result
    if key == "missing-config-file":
        result["status"] = "skipped"
        return result
    result["status"] = "skipped"
    return result


def apply_repairs(
    conn: sqlite3.Connection,
    paths: RuntimePaths,
    repairs: list[Mapping[str, Any]],
    *,
    diagnostic_run_id: str | None = None,
    commit: bool = True,
) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    for repair in repairs:
        key = str(repair.get("repair_key") or "")
        if key not in SAFE_REPAIR_KEYS:
            raise ValueError(f"unsafe repair key: {key!r}")
        result = _apply_single_repair(
            conn,
            paths,
            repair,
            diagnostic_run_id=diagnostic_run_id,
        )
        applied.append(result)
        if diagnostic_run_id:
            record_repair_audit_event(
                conn,
                diagnostic_run_id=diagnostic_run_id,
                repair_key=key,
                mode="repair_apply",
                status=str(result.get("status") or "failed"),
                project_id=str(repair.get("project_id")) if repair.get("project_id") else None,
                before={"path": repair.get("path"), "pid": repair.get("pid")},
                after=result,
            )
    if commit:
        conn.commit()
    return applied


def run_diagnostic(
    conn: sqlite3.Connection,
    paths: RuntimePaths,
    config: CoordinatorConfig | None,
    *,
    mode: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    if mode not in {"repair_dry_run", "repair_apply"}:
        raise ValueError(f"unsupported diagnostic mode: {mode!r}")
    findings = run_readiness_findings(paths, conn, config)
    repairs = plan_repairs(findings)
    status = "warn" if findings else "pass"
    run_id = record_diagnostic_run(
        conn,
        scope="project" if project_id else "global",
        project_id=project_id,
        mode=mode,
        status=status,
        findings=findings,
        repairs=repairs,
        commit=True,
    )
    if mode == "repair_dry_run":
        dry_repairs = [{**item, "status": "planned"} for item in repairs]
        finish_diagnostic_run(
            conn,
            run_id=run_id,
            status=status,
            repairs=dry_repairs,
            commit=True,
        )
        return {
            "mode": mode,
            "run_id": run_id,
            "status": status,
            "findings": findings,
            "repairs": dry_repairs,
        }
    applied = apply_repairs(conn, paths, repairs, diagnostic_run_id=run_id, commit=True)
    final_status = "repaired" if any(item.get("status") == "applied" for item in applied) else status
    finish_diagnostic_run(
        conn,
        run_id=run_id,
        status=final_status,
        repairs=applied,
        commit=True,
    )
    return {
        "mode": mode,
        "run_id": run_id,
        "status": final_status,
        "findings": findings,
        "repairs": applied,
    }