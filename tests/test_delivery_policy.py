"""Phase 9 delivery policy gate tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.config import AgentConfig, CoordinatorConfig, PolicyConfig, RepoConfig
from local_cli_coordinator.db import connect, create_task, init_db
from tests.helpers import init_git_repo


def _config(repo_path: Path, *, allow_push: bool = True) -> CoordinatorConfig:
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
                allow_push=allow_push,
                merge_policy="push_branch_only" if allow_push else "no_push",
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


class DeliveryPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.conn = connect(self.tmp / "data.db")
        init_db(self.conn)
        self.project_id = "proj-1"
        self.conn.execute(
            "insert into projects(id, name, repo_root, created_at) values (?, ?, ?, datetime('now'))",
            (self.project_id, "demo", str(self.repo)),
        )
        self.task_id = create_task(
            self.conn,
            title="Policy task",
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

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_blocks_delivery_when_allow_push_false(self) -> None:
        from local_cli_coordinator.delivery_policy import evaluate_delivery_policy

        decision = evaluate_delivery_policy(
            self.conn,
            config=_config(self.repo, allow_push=False),
            project_id=self.project_id,
            task_id=self.task_id,
            branch_name="coord/task-1",
            action="deliver",
        )
        self.assertFalse(decision.allowed)
        self.assertTrue(any("allow_push" in reason for reason in decision.blockers))

    def test_blocks_delivery_without_evidence_gate(self) -> None:
        from local_cli_coordinator.delivery_policy import evaluate_delivery_policy

        decision = evaluate_delivery_policy(
            self.conn,
            config=_config(self.repo),
            project_id=self.project_id,
            task_id=self.task_id,
            branch_name="coord/task-1",
            action="deliver",
        )
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.blockers)

    def test_blocks_delivery_when_human_review_required(self) -> None:
        from local_cli_coordinator.delivery_policy import evaluate_delivery_policy
        from local_cli_coordinator.evidence import record_evidence
        from local_cli_coordinator.risk import assess_task_risk

        record_evidence(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            evidence_type="command",
            status="passed",
            summary="ok",
            data={"command": "true"},
        )
        assess_task_risk(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            changed_files=["migrations/019.sql"],
            diff_text="ALTER TABLE",
        )
        self.conn.commit()
        decision = evaluate_delivery_policy(
            self.conn,
            config=_config(self.repo),
            project_id=self.project_id,
            task_id=self.task_id,
            branch_name="coord/task-1",
            action="deliver",
        )
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_human_review)

    def test_allows_delivery_when_merge_ready_and_policy_permits(self) -> None:
        from local_cli_coordinator.delivery_policy import evaluate_delivery_policy
        from local_cli_coordinator.evidence import record_evidence

        record_evidence(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            evidence_type="command",
            status="passed",
            summary="ok",
            data={"command": "true"},
        )
        record_evidence(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            evidence_type="acceptance",
            status="covered",
            summary="done",
            data={"criterion": "done"},
        )
        self.conn.commit()
        decision = evaluate_delivery_policy(
            self.conn,
            config=_config(self.repo),
            project_id=self.project_id,
            task_id=self.task_id,
            branch_name="coord/task-1",
            action="deliver",
        )
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.requires_human_review)


if __name__ == "__main__":
    unittest.main()