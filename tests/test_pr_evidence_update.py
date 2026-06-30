"""Red tests for Phase 12 PR evidence refresh.

Owner: Grok (Phase 12 Task 0)
Expected before implementation: missing pr_evidence_update module.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.config import AgentConfig, CoordinatorConfig, PolicyConfig, RepoConfig
from local_cli_coordinator.db import connect, create_task, init_db
from tests.helpers import ROOT, init_git_repo

_FAKE_GH_PATH = ROOT / "tests" / "fixtures" / "fake_gh.py"


def _base_config(repo_path: Path) -> CoordinatorConfig:
    return CoordinatorConfig(
        agents={
            "worker": AgentConfig(
                id="worker",
                command="true",
                capabilities=["code"],
                max_concurrency=1,
            )
        },
        repos={
            "demo": RepoConfig(
                id="demo",
                path=repo_path,
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=False,
                merge_policy="no_push",
                verify_commands=["true"],
                review_policy="tests_only",
            )
        },
        policy=PolicyConfig(
            require_single_repo=False,
            require_acceptance_criteria=False,
            require_verification_commands=False,
            require_handoff_summary=False,
            max_files_touched=20,
            max_expected_minutes=60,
            max_attempts=3,
            split_if_touches_multiple_subsystems=False,
            split_if_research_and_code_are_mixed=False,
        ),
    )


class PrEvidenceUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.conn = connect(self.tmp / "data.db")
        init_db(self.conn)
        self.project_id = "proj-1"
        self.conn.execute(
            "insert into projects(id, canonical_path, repo_id) values (?, ?, ?)",
            (self.project_id, str(self.repo.resolve()), "demo"),
        )
        self.task_id = create_task(
            self.conn,
            title="Evidence task",
            repo="demo",
            source_path="task.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["true"],
            project_id=self.project_id,
        )
        self.conn.commit()
        self.config = _base_config(self.repo)
        self.env = os.environ.copy()
        self.env["GH_FAKE_SCENARIO"] = json.dumps(
            {
                "pr_number": 21,
                "pr_body": (
                    "## Coordinator Evidence\n"
                    "### CI (failed)\n"
                    "- unit: FAILED\n"
                    "\n"
                    "## Coordinator Evidence (latest)\n"
                    "### CI (failed)\n"
                    "- unit: FAILED\n"
                ),
            }
        )

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _delivery(self):
        from local_cli_coordinator import github_delivery

        return github_delivery.create_delivery_record(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            repo_id="demo",
            branch_name="coord/task-1",
            base_branch="main",
            status="ci_failed",
            pr_number=21,
            pr_url="https://github.com/example/coordinator/pull/21",
            last_check_state="fail",
        )

    def test_update_appends_latest_evidence_section(self) -> None:
        from local_cli_coordinator.pr_evidence_update import update_pr_evidence

        record = self._delivery()
        self.conn.commit()
        result = update_pr_evidence(
            self.conn,
            config=self.config,
            project_id=self.project_id,
            delivery_id=record.id,
            gh_executable=str(_FAKE_GH_PATH),
            env=self.env,
            sections={"ci": "pass", "checks": ["unit: SUCCESS"]},
        )
        self.assertTrue(result.updated)
        self.assertIn("Coordinator Evidence (latest)", result.body)
        self.assertIn("SUCCESS", result.body)

    def test_update_preserves_prior_failure_history(self) -> None:
        from local_cli_coordinator.pr_evidence_update import update_pr_evidence

        record = self._delivery()
        self.conn.commit()
        result = update_pr_evidence(
            self.conn,
            config=self.config,
            project_id=self.project_id,
            delivery_id=record.id,
            gh_executable=str(_FAKE_GH_PATH),
            env=self.env,
            sections={"ci": "pass", "checks": ["unit: SUCCESS"]},
        )
        self.assertIn("CI (failed)", result.body)
        self.assertIn("FAILED", result.body)

    def test_update_records_healing_attempt(self) -> None:
        from local_cli_coordinator.pr_evidence_update import update_pr_evidence

        record = self._delivery()
        self.conn.commit()
        update_pr_evidence(
            self.conn,
            config=self.config,
            project_id=self.project_id,
            delivery_id=record.id,
            gh_executable=str(_FAKE_GH_PATH),
            env=self.env,
            sections={"ci": "pass"},
        )
        row = self.conn.execute(
            """
            select action, status from pr_healing_attempts
            where delivery_id = ? and action = 'evidence_update'
            """,
            (record.id,),
        ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["status"], "succeeded")

    def test_update_rejects_cross_project_delivery(self) -> None:
        from local_cli_coordinator.pr_evidence_update import update_pr_evidence

        record = self._delivery()
        self.conn.commit()
        with self.assertRaises(ValueError):
            update_pr_evidence(
                self.conn,
                config=self.config,
                project_id="other-project",
                delivery_id=record.id,
                gh_executable=str(_FAKE_GH_PATH),
                env=self.env,
                sections={"ci": "pass"},
            )


if __name__ == "__main__":
    unittest.main()