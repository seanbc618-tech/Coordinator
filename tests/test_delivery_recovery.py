"""Phase 9 delivery failure recovery tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, create_task, init_db
from tests.helpers import init_git_repo


class DeliveryRecoveryTests(unittest.TestCase):
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
            title="Recovery task",
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

    def test_ci_failure_creates_bounded_recovery_proposal(self) -> None:
        from local_cli_coordinator import github_delivery
        from local_cli_coordinator.delivery_recovery import propose_recovery_for_ci_failure

        record = github_delivery.create_delivery_record(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            repo_id="demo",
            branch_name="coord/task-1",
            base_branch="main",
            status="ci_failed",
            pr_number=12,
            pr_url="https://github.com/example/coordinator/pull/12",
            last_check_state="fail",
        )
        self.conn.commit()
        first = propose_recovery_for_ci_failure(
            self.conn,
            project_id=self.project_id,
            delivery_id=record.id,
        )
        second = propose_recovery_for_ci_failure(
            self.conn,
            project_id=self.project_id,
            delivery_id=record.id,
        )
        self.conn.commit()
        self.assertIsNotNone(first)
        assert first is not None
        self.assertIsNone(second)

    def test_passing_ci_does_not_create_recovery(self) -> None:
        from local_cli_coordinator import github_delivery
        from local_cli_coordinator.delivery_recovery import propose_recovery_for_ci_failure

        record = github_delivery.create_delivery_record(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            repo_id="demo",
            branch_name="coord/task-1",
            base_branch="main",
            status="ready",
            pr_number=12,
            last_check_state="pass",
        )
        self.conn.commit()
        proposal_id = propose_recovery_for_ci_failure(
            self.conn,
            project_id=self.project_id,
            delivery_id=record.id,
        )
        self.assertIsNone(proposal_id)


if __name__ == "__main__":
    unittest.main()