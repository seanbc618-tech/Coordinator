"""Tests for project onboarding Supervisor methods."""

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.config import load_config
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.supervisor_methods import SupervisorMethods
from local_cli_coordinator.supervisor_protocol import RequestEnvelope


def _request(method: str, project_id: str | None = "__onboarding__", **params) -> RequestEnvelope:
    return RequestEnvelope(
        protocol_version=1,
        request_id="req-1",
        project_id=project_id,
        method=method,
        params=params,
    )


class ProjectOnboardingMethodsTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(self.db_path)
        init_db(self.conn)
        self.methods = SupervisorMethods()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, capture_output=True)
        (self.repo / "README.md").write_text("test")
        subprocess.run(["git", "add", "."], cwd=self.repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo,
            capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
            },
        )
        self.draft = inspect_project(self.repo)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_inspect_returns_draft_without_writes(self) -> None:
        before = self.conn.execute("select count(*) as n from projects").fetchone()["n"]
        resp = self.methods.handle(
            self.conn,
            _request("project.inspect", path=str(self.repo)),
        )
        after = self.conn.execute("select count(*) as n from projects").fetchone()["n"]
        self.assertTrue(resp.ok)
        self.assertEqual(before, after)
        self.assertEqual(resp.result["canonical_path"], str(self.repo.resolve()))
        self.assertIn("repo_id", resp.result)
        self.assertEqual(resp.result["default_branch"], "main")
        self.assertEqual(resp.result["branch_prefix"], "coord/")
        self.assertEqual(resp.result["verify_commands"], [])

    def test_inspect_includes_policy_defaults(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("project.inspect", path=str(self.repo)),
        )
        self.assertTrue(resp.ok)
        self.assertFalse(resp.result["allow_push"])
        self.assertEqual(resp.result["merge_policy"], "no_push")
        self.assertEqual(resp.result["review_policy"], "full_review")
        self.assertEqual(resp.result["max_tasks_per_day"], 24)
        self.assertEqual(resp.result["max_task_runtime_seconds"], 1800)

    def test_inspect_uses_repo_and_global_policy_when_configured(self) -> None:
        config_root = Path(self.tmp.name) / "config-root"
        config_dir = config_root / "config"
        config_dir.mkdir(parents=True)
        config_dir.joinpath("agents.toml").write_text(
            '[agents.test]\n'
            f'command = "{Path(sys.executable)}"\n'
            'capabilities = ["code"]\n'
            'max_concurrency = 1\n',
            encoding="utf-8",
        )
        config_dir.joinpath("repos.toml").write_text(
            '[repos.demo]\n'
            f'path = "{self.repo.resolve()}"\n'
            'default_branch = "main"\n'
            'allow_push = true\n'
            'merge_policy = "push_branch_only"\n'
            'review_policy = "always_human"\n'
            'verify_commands = ["make test"]\n',
            encoding="utf-8",
        )
        config_dir.joinpath("policy.toml").write_text(
            '[task_policy]\n'
            'require_single_repo = true\n'
            'require_acceptance_criteria = false\n'
            'require_verification_commands = false\n'
            'require_handoff_summary = false\n'
            'max_files_touched = 10\n'
            'max_expected_minutes = 30\n'
            'max_attempts = 3\n'
            'split_if_touches_multiple_subsystems = false\n'
            'split_if_research_and_code_are_mixed = false\n'
            'max_tasks_per_run = 1\n'
            'max_tasks_per_day = 42\n'
            'max_task_runtime_seconds = 900\n'
            'max_consecutive_failures = 3\n',
            encoding="utf-8",
        )
        config = load_config(config_root)
        methods = SupervisorMethods(config=config)
        resp = methods.handle(
            self.conn,
            _request("project.inspect", path=str(self.repo)),
        )
        self.assertTrue(resp.ok)
        self.assertTrue(resp.result["allow_push"])
        self.assertEqual(resp.result["merge_policy"], "push_branch_only")
        self.assertEqual(resp.result["review_policy"], "always_human")
        self.assertEqual(resp.result["verify_commands"], ["make test"])
        self.assertEqual(resp.result["max_tasks_per_day"], 42)
        self.assertEqual(resp.result["max_task_runtime_seconds"], 900)

    def test_inspect_detects_registered_project(self) -> None:
        project_id = register_project(self.conn, self.draft, confirmed=True)
        resp = self.methods.handle(
            self.conn,
            _request("project.inspect", path=str(self.repo)),
        )
        self.assertTrue(resp.ok)
        self.assertTrue(resp.result["registered"])
        self.assertEqual(resp.result["project_id"], project_id)
        self.assertFalse(resp.result["path_changed"])

    def test_inspect_detects_path_changed(self) -> None:
        project_id = register_project(self.conn, self.draft, confirmed=True)
        moved = Path(self.tmp.name) / "moved"
        subprocess.run(["git", "clone", str(self.repo), str(moved)], capture_output=True)
        resp = self.methods.handle(
            self.conn,
            _request("project.inspect", path=str(moved)),
        )
        self.assertTrue(resp.ok)
        self.assertFalse(resp.result["registered"])
        self.assertTrue(resp.result["path_changed"])
        self.assertEqual(resp.result["project_id"], project_id)
        self.assertEqual(resp.result["stored_canonical_path"], str(self.repo.resolve()))

    def test_register_requires_confirmed(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request(
                "project.register",
                path=str(self.repo),
                canonical_path=str(self.repo.resolve()),
                repo_id=self.draft.repo_id,
                default_branch=self.draft.default_branch,
                branch_prefix=self.draft.branch_prefix,
                verify_commands=[],
            ),
        )
        self.assertFalse(resp.ok)
        self.assertIn("confirmation", resp.error.lower())

    def test_register_rejects_stale_draft(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request(
                "project.register",
                confirmed=True,
                path=str(self.repo),
                canonical_path=str(self.repo.resolve()),
                repo_id="stale-repo-id",
                default_branch=self.draft.default_branch,
                branch_prefix=self.draft.branch_prefix,
                verify_commands=[],
            ),
        )
        self.assertFalse(resp.ok)
        self.assertIn("mismatch", resp.error.lower())

    def test_register_accepts_policy_resolved_verify_commands(self) -> None:
        config_root = Path(self.tmp.name) / "config-root"
        config_dir = config_root / "config"
        config_dir.mkdir(parents=True)
        config_dir.joinpath("agents.toml").write_text(
            '[agents.test]\n'
            f'command = "{Path(sys.executable)}"\n'
            'capabilities = ["code"]\n'
            'max_concurrency = 1\n',
            encoding="utf-8",
        )
        config_dir.joinpath("repos.toml").write_text(
            '[repos.demo]\n'
            f'path = "{self.repo.resolve()}"\n'
            'default_branch = "main"\n'
            'verify_commands = ["make test"]\n',
            encoding="utf-8",
        )
        config_dir.joinpath("policy.toml").write_text(
            '[task_policy]\n'
            'require_single_repo = true\n'
            'require_acceptance_criteria = false\n'
            'require_verification_commands = false\n'
            'require_handoff_summary = false\n'
            'max_files_touched = 10\n'
            'max_expected_minutes = 30\n'
            'max_attempts = 3\n'
            'split_if_touches_multiple_subsystems = false\n'
            'split_if_research_and_code_are_mixed = false\n'
            'max_tasks_per_run = 1\n'
            'max_tasks_per_day = 24\n'
            'max_consecutive_failures = 3\n',
            encoding="utf-8",
        )
        config = load_config(config_root)
        methods = SupervisorMethods(config=config)
        inspect_resp = methods.handle(
            self.conn,
            _request("project.inspect", path=str(self.repo)),
        )
        self.assertTrue(inspect_resp.ok)
        resp = methods.handle(
            self.conn,
            _request(
                "project.register",
                confirmed=True,
                path=str(self.repo),
                canonical_path=inspect_resp.result["canonical_path"],
                repo_id=inspect_resp.result["repo_id"],
                default_branch=inspect_resp.result["default_branch"],
                branch_prefix=inspect_resp.result["branch_prefix"],
                verify_commands=inspect_resp.result["verify_commands"],
            ),
        )
        self.assertTrue(resp.ok, resp.error)
        row = self.conn.execute(
            "select verify_commands from projects where id = ?",
            (resp.result["project_id"],),
        ).fetchone()
        self.assertEqual(row["verify_commands"], "make test")

    def test_register_confirmed_creates_project(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request(
                "project.register",
                confirmed=True,
                path=str(self.repo),
                canonical_path=str(self.repo.resolve()),
                repo_id=self.draft.repo_id,
                default_branch=self.draft.default_branch,
                branch_prefix=self.draft.branch_prefix,
                verify_commands=[],
            ),
        )
        self.assertTrue(resp.ok)
        self.assertIn("project_id", resp.result)
        row = self.conn.execute(
            "select id from projects where canonical_path = ?",
            (str(self.repo.resolve()),),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], resp.result["project_id"])

    def test_register_updates_path_after_move(self) -> None:
        project_id = register_project(self.conn, self.draft, confirmed=True)
        moved = Path(self.tmp.name) / "moved"
        subprocess.run(["git", "clone", str(self.repo), str(moved)], capture_output=True)
        moved_draft = inspect_project(moved)
        resp = self.methods.handle(
            self.conn,
            _request(
                "project.register",
                confirmed=True,
                path=str(moved),
                canonical_path=str(moved_draft.canonical_path),
                repo_id=moved_draft.repo_id,
                default_branch=moved_draft.default_branch,
                branch_prefix=moved_draft.branch_prefix,
                verify_commands=[],
            ),
        )
        self.assertTrue(resp.ok)
        self.assertEqual(resp.result["project_id"], project_id)
        row = self.conn.execute(
            "select canonical_path from projects where id = ?",
            (project_id,),
        ).fetchone()
        self.assertEqual(row["canonical_path"], str(moved.resolve()))