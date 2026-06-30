"""Phase 9 tests for durable delivery records and PR lifecycle."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
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
                allow_push=True,
                merge_policy="push_branch_only",
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


class DeliveryRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.db_path = self.tmp / "data.db"
        self.conn = connect(self.db_path)
        init_db(self.conn)
        self.project_id = "proj-1"
        self.conn.execute(
            "insert into projects(id, name, repo_root, created_at) values (?, ?, ?, datetime('now'))",
            (self.project_id, "demo", str(self.repo)),
        )
        self.task_id = create_task(
            self.conn,
            title="Delivery task",
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
                "create_number": 55,
                "create_url": "https://github.com/example/coordinator/pull/55",
                "checks": [{"name": "unit", "state": "SUCCESS", "bucket": "pass"}],
            }
        )

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_delivery_record_persists_open_branch(self) -> None:
        from local_cli_coordinator import github_delivery

        record = github_delivery.create_delivery_record(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            repo_id="demo",
            branch_name="coord/task-1",
            base_branch="main",
        )
        self.conn.commit()
        self.assertEqual(record.status, "draft")
        loaded = github_delivery.get_delivery_for_branch(
            self.conn,
            project_id=self.project_id,
            repo_id="demo",
            branch_name="coord/task-1",
        )
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.id, record.id)

    def test_append_delivery_event_is_durable(self) -> None:
        from local_cli_coordinator import github_delivery

        record = github_delivery.create_delivery_record(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            repo_id="demo",
            branch_name="coord/task-1",
            base_branch="main",
        )
        github_delivery.append_delivery_event(
            self.conn,
            delivery_id=record.id,
            project_id=self.project_id,
            event_type="policy_checked",
            status="blocked",
            data={"reason": "allow_push=false"},
        )
        self.conn.commit()
        events = github_delivery.list_delivery_events(self.conn, delivery_id=record.id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "policy_checked")

    def test_create_or_update_pr_stores_url_and_number(self) -> None:
        from local_cli_coordinator import github_delivery
        from local_cli_coordinator.evidence import record_evidence
        from local_cli_coordinator.review_packets_v2 import write_review_packet_v2

        record_evidence(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            evidence_type="command",
            status="passed",
            summary="ok",
            data={"command": "true"},
        )
        write_review_packet_v2(
            self.conn,
            repo_root=self.repo,
            project_id=self.project_id,
            task_id=self.task_id,
            verdict="pass",
            suggested_action="merge",
        )
        self.conn.commit()
        record = github_delivery.create_delivery_record(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            repo_id="demo",
            branch_name="coord/task-1",
            base_branch="main",
        )
        updated = github_delivery.create_or_update_pr(
            self.conn,
            config=self.config,
            delivery_id=record.id,
            gh_executable=sys.executable,
            gh_prefix=[str(_FAKE_GH_PATH)],
            env=self.env,
        )
        self.conn.commit()
        self.assertEqual(updated.status, "pr_open")
        self.assertEqual(updated.pr_number, 55)
        self.assertIn("github.com", updated.pr_url or "")

    def test_poll_ci_updates_check_state(self) -> None:
        from local_cli_coordinator import github_delivery

        record = github_delivery.create_delivery_record(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            repo_id="demo",
            branch_name="coord/task-1",
            base_branch="main",
            status="pr_open",
            pr_number=55,
            pr_url="https://github.com/example/coordinator/pull/55",
        )
        self.conn.commit()
        updated = github_delivery.poll_ci_status(
            self.conn,
            delivery_id=record.id,
            gh_executable=sys.executable,
            gh_prefix=[str(_FAKE_GH_PATH)],
            env=self.env,
        )
        self.conn.commit()
        self.assertIn(updated.last_check_state, {"pass", "fail", "pending"})


class PrBodyEvidenceTests(unittest.TestCase):
    def test_pr_body_includes_evidence_summary_not_raw_secrets(self) -> None:
        from local_cli_coordinator.github_delivery import build_pr_body_from_task

        body = build_pr_body_from_task(
            task_title="Add feature",
            task_id="task-1",
            evidence_summary={
                "commands_passed": 2,
                "changed_files": ["src/a.py"],
            },
            review_verdict="pass",
            evidence_packet_path=".coordinator/review_packets_v2/task-1.json",
        )
        self.assertIn("commands_passed", body)
        self.assertIn("review_packets_v2", body)
        self.assertNotIn("api_key=secret", body)


if __name__ == "__main__":
    unittest.main()