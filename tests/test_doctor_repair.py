"""Phase 14 red tests: safe doctor repair planner and CLI.

Owner: Grok (Phase 14 Task 0)
Expected before implementation: doctor_repair module and --repair flags missing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config_runtime import load_config_for_paths
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import ROOT, SRC, init_git_repo

_PYTHON = sys.executable


def _write_config(config_dir: Path, repo_path: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "agents.toml").write_text(textwrap.dedent("""
        [agents.worker]
        command = "true"
        capabilities = ["code"]
        max_concurrency = 1
        role = "worker"
    """).strip())
    (config_dir / "repos.toml").write_text(textwrap.dedent(f"""
        [repos.test-repo]
        path = "{repo_path}"
        default_branch = "main"
        allow_push = false
        merge_policy = "no_push"
        review_policy = "tests_only"
    """).strip())
    (config_dir / "policy.toml").write_text(textwrap.dedent("""
        [task_policy]
        require_single_repo = false
        require_acceptance_criteria = false
        require_verification_commands = false
        require_handoff_summary = false
        max_files_touched = 20
        max_expected_minutes = 60
        max_attempts = 3
        split_if_touches_multiple_subsystems = false
        split_if_research_and_code_are_mixed = false

        [notifications]
        allow_command_sink = false
    """).strip())


def _run_cli_with_home(
    home: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["COORDINATOR_HOME"] = str(home)
    return subprocess.run(
        [_PYTHON, "-m", "local_cli_coordinator", *args],
        cwd=cwd or ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class DoctorRepairPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        register_project(self.conn, inspect_project(self.repo), confirmed=True)
        self.conn.commit()
        self.config = load_config_for_paths(self.paths)

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plan_repairs_identifies_missing_state_dir(self) -> None:
        from local_cli_coordinator.doctor_repair import plan_repairs, run_readiness_findings

        findings = run_readiness_findings(self.paths, self.conn, self.config)
        repairs = plan_repairs(findings)
        keys = {item["repair_key"] for item in repairs}
        self.assertIn("missing-state-dir", keys)

    def test_dry_run_does_not_create_directories(self) -> None:
        from local_cli_coordinator.doctor_repair import run_diagnostic

        self.assertFalse(self.paths.state_dir.exists())
        result = run_diagnostic(
            self.conn,
            self.paths,
            self.config,
            mode="repair_dry_run",
        )
        self.assertFalse(self.paths.state_dir.exists())
        self.assertIn("findings", result)
        self.assertIn("repairs", result)
        self.assertEqual(result.get("mode"), "repair_dry_run")

    def test_dry_run_records_diagnostic_run(self) -> None:
        from local_cli_coordinator.doctor_repair import run_diagnostic

        run_diagnostic(
            self.conn,
            self.paths,
            self.config,
            mode="repair_dry_run",
        )
        row = self.conn.execute(
            "select mode, status from diagnostic_runs order by started_at desc limit 1"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["mode"], "repair_dry_run")

    def test_apply_creates_missing_state_dir(self) -> None:
        from local_cli_coordinator.doctor_repair import run_diagnostic

        result = run_diagnostic(
            self.conn,
            self.paths,
            self.config,
            mode="repair_apply",
        )
        self.assertTrue(self.paths.state_dir.is_dir())
        self.assertIn("repairs", result)
        applied = [
            item
            for item in result["repairs"]
            if item.get("repair_key") == "missing-state-dir"
        ]
        self.assertTrue(applied)
        self.assertEqual(applied[0].get("status"), "applied")

    def test_apply_rejects_unsafe_repair_keys(self) -> None:
        from local_cli_coordinator.doctor_repair import apply_repairs

        with self.assertRaises(ValueError):
            apply_repairs(
                self.conn,
                self.paths,
                [{"repair_key": "rewrite-git-remote", "mode": "repair_apply"}],
            )

    def test_stale_lock_skips_symlink_outside_coordinator_home(self) -> None:
        from local_cli_coordinator.doctor_repair import apply_repairs, plan_repairs

        external = self.tmp / "external-lock"
        external.write_text('{"pid": 99999, "acquired_at": "2026-01-01T00:00:00Z"}\n')
        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        symlink = self.paths.lock
        symlink.symlink_to(external)
        repairs = plan_repairs(
            [{"finding_key": "stale-lock", "path": str(symlink), "pid": 99999}]
        )
        result = apply_repairs(self.conn, self.paths, repairs)
        self.assertTrue(symlink.is_symlink())
        self.assertTrue(external.exists())
        statuses = {item.get("status") for item in result}
        self.assertIn("skipped", statuses)

    def test_stale_socket_skips_symlink_outside_coordinator_home(self) -> None:
        from local_cli_coordinator.doctor_repair import apply_repairs, plan_repairs

        external = self.tmp / "external.sock"
        external.touch()
        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        symlink = self.paths.socket
        symlink.symlink_to(external)
        repairs = plan_repairs(
            [{"finding_key": "stale-socket", "path": str(symlink)}]
        )
        result = apply_repairs(self.conn, self.paths, repairs)
        self.assertTrue(symlink.is_symlink())
        self.assertTrue(external.exists())
        statuses = {item.get("status") for item in result}
        self.assertIn("skipped", statuses)

    def test_stale_lock_requires_absent_pid_before_removal(self) -> None:
        from local_cli_coordinator.doctor_repair import apply_repairs, plan_repairs

        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.paths.lock
        lock_path.write_text(
            f'{{"pid": {os.getpid()}, "acquired_at": "2026-01-01T00:00:00Z"}}\n',
            encoding="utf-8",
        )
        repairs = plan_repairs(
            [{"finding_key": "stale-lock", "path": str(lock_path), "pid": os.getpid()}]
        )
        result = apply_repairs(self.conn, self.paths, repairs)
        self.assertTrue(lock_path.exists())
        statuses = {item.get("status") for item in result}
        self.assertIn("skipped", statuses)

    def test_stale_lock_removes_only_when_pid_is_absent(self) -> None:
        from local_cli_coordinator.doctor_repair import apply_repairs, plan_repairs

        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.paths.lock
        stale_pid = 424242
        lock_path.write_text(
            f'{{"pid": {stale_pid}, "acquired_at": "2026-01-01T00:00:00Z"}}\n',
            encoding="utf-8",
        )
        repairs = plan_repairs(
            [{"finding_key": "stale-lock", "path": str(lock_path), "pid": stale_pid}]
        )
        result = apply_repairs(self.conn, self.paths, repairs)
        self.assertFalse(lock_path.exists())
        applied = [item for item in result if item.get("status") == "applied"]
        self.assertTrue(applied)


class DoctorRepairPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.conn = connect(Path(self.tmp) / "data" / "coordinator.db")
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_diagnostic_run_persists_row(self) -> None:
        from local_cli_coordinator.operator_hardening import record_diagnostic_run

        run_id = record_diagnostic_run(
            self.conn,
            scope="global",
            mode="repair_dry_run",
            status="warn",
            findings=[{"name": "missing-state-dir"}],
            repairs=[{"repair_key": "missing-state-dir", "status": "planned"}],
            commit=True,
        )
        row = self.conn.execute(
            "select id, scope, mode, status from diagnostic_runs where id = ?",
            (run_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["scope"], "global")
        self.assertEqual(row["mode"], "repair_dry_run")

    def test_record_global_control_event_validates_enums(self) -> None:
        from local_cli_coordinator.operator_hardening import record_global_control_event

        with self.assertRaises(ValueError):
            record_global_control_event(
                self.conn,
                action="invalid-action",
                scope="global",
                status="completed",
            )
        event_id = record_global_control_event(
            self.conn,
            action="pause",
            scope="global",
            status="completed",
            affected_projects=["proj-1"],
            reason="gate smoke",
            commit=True,
        )
        row = self.conn.execute(
            "select action, affected_json from global_control_events where id = ?",
            (event_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(json.loads(row["affected_json"]), ["proj-1"])


class DoctorRepairCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        _write_config(self.home / "config", self.repo)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_doctor_repair_dry_run_json(self) -> None:
        proc = _run_cli_with_home(
            self.home,
            "doctor",
            "--repair",
            "--dry-run",
            "--json",
            cwd=self.repo,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload.get("ok"), payload)
        data = payload.get("data") or {}
        self.assertEqual(data.get("mode"), "repair_dry_run")
        self.assertIn("findings", data)
        self.assertIn("repairs", data)

    def test_cli_doctor_without_repair_stays_read_only(self) -> None:
        proc = _run_cli_with_home(self.home, "doctor", "--json", cwd=self.repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        data = payload.get("data") or {}
        self.assertNotIn("repairs", data)
        self.assertIn("readiness", data)


if __name__ == "__main__":
    unittest.main()