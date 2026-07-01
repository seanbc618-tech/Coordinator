"""Phase 14 red tests: global pause and resume controls.

Owner: Grok (Phase 14 Task 0)
Expected before implementation: global_controls module and pause/resume --all CLI missing.
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


class GlobalControlsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        register_project(self.conn, inspect_project(self.repo), confirmed=True)
        self.conn.commit()
        self.project_id = self.conn.execute(
            "select id from projects limit 1"
        ).fetchone()["id"]
        self.config = load_config_for_paths(self.paths)

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pause_all_sets_global_flag_and_pauses_projects(self) -> None:
        from local_cli_coordinator.global_controls import pause_all

        result = pause_all(
            self.conn,
            paths=self.paths,
            reason="overnight maintenance",
        )
        self.assertTrue(result.get("global_pause"))
        self.assertIn("affected_projects", result)
        self.assertGreaterEqual(len(result["affected_projects"]), 1)
        row = self.conn.execute(
            "select action, scope, status from global_control_events "
            "order by created_at desc limit 1"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["action"], "pause")
        self.assertEqual(row["scope"], "global")
        self.assertEqual(row["status"], "completed")

    def test_pause_all_does_not_kill_workers_by_default(self) -> None:
        from local_cli_coordinator.global_controls import pause_all

        result = pause_all(
            self.conn,
            paths=self.paths,
            reason="gate smoke",
        )
        self.assertFalse(result.get("workers_killed"))
        self.assertIn("running_workers", result)

    def test_resume_all_skips_manually_paused_projects(self) -> None:
        from local_cli_coordinator.global_controls import pause_all, resume_all

        pause_all(self.conn, paths=self.paths, reason="global pause")
        self.conn.execute(
            "update projects set status = 'paused', pause_reason = 'manual' where id = ?",
            (self.project_id,),
        )
        self.conn.commit()
        result = resume_all(self.conn, paths=self.paths)
        row = self.conn.execute(
            "select status, pause_reason from projects where id = ?",
            (self.project_id,),
        ).fetchone()
        self.assertEqual(row["status"], "paused")
        self.assertEqual(row["pause_reason"], "manual")
        resumed = result.get("resumed_projects") or []
        self.assertNotIn(self.project_id, resumed)

    def test_resume_all_with_include_manual(self) -> None:
        from local_cli_coordinator.global_controls import pause_all, resume_all

        pause_all(self.conn, paths=self.paths, reason="global pause")
        self.conn.execute(
            "update projects set status = 'paused', pause_reason = 'manual' where id = ?",
            (self.project_id,),
        )
        self.conn.commit()
        resume_all(self.conn, paths=self.paths, include_manual=True)
        row = self.conn.execute(
            "select status from projects where id = ?",
            (self.project_id,),
        ).fetchone()
        self.assertEqual(row["status"], "active")

    def test_resume_all_only_restores_whitelisted_projects(self) -> None:
        from local_cli_coordinator.global_controls import pause_all, resume_all

        second_repo = self.tmp / "repo2"
        init_git_repo(second_repo)
        second = inspect_project(second_repo)
        register_project(self.conn, second, confirmed=True)
        self.conn.commit()
        second_id = self.conn.execute(
            "select id from projects where id != ? limit 1",
            (self.project_id,),
        ).fetchone()["id"]
        self.conn.execute(
            "update projects set status = 'paused', pause_reason = 'manual' where id = ?",
            (second_id,),
        )
        self.conn.commit()

        pause_result = pause_all(self.conn, paths=self.paths, reason="global pause")
        whitelist = set(pause_result.get("affected_projects") or [])
        self.assertIn(self.project_id, whitelist)
        self.assertNotIn(second_id, whitelist)

        resume_result = resume_all(self.conn, paths=self.paths)
        resumed = set(resume_result.get("resumed_projects") or [])
        self.assertEqual(resumed, whitelist)

        active_row = self.conn.execute(
            "select status from projects where id = ?",
            (self.project_id,),
        ).fetchone()
        manual_row = self.conn.execute(
            "select status, pause_reason from projects where id = ?",
            (second_id,),
        ).fetchone()
        self.assertEqual(active_row["status"], "active")
        self.assertEqual(manual_row["status"], "paused")
        self.assertEqual(manual_row["pause_reason"], "manual")


class GlobalControlsCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        register_project(self.conn, inspect_project(self.repo), confirmed=True)
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_pause_all(self) -> None:
        proc = _run_cli_with_home(
            self.home,
            "pause",
            "--all",
            "--reason",
            "gate smoke",
            "--json",
            cwd=self.repo,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload.get("ok"), payload)
        data = payload.get("data") or {}
        self.assertTrue(data.get("global_pause"))

    def test_cli_resume_all(self) -> None:
        _run_cli_with_home(
            self.home,
            "pause",
            "--all",
            "--reason",
            "gate smoke",
            cwd=self.repo,
        )
        proc = _run_cli_with_home(self.home, "resume", "--all", "--json", cwd=self.repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload.get("ok"), payload)
        data = payload.get("data") or {}
        self.assertFalse(data.get("global_pause"))


if __name__ == "__main__":
    unittest.main()