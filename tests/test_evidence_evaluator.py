"""Red tests for Phase 8 evidence-based completion evaluation.

Owner: Grok (Phase 8 Task 0)
Expected before implementation: ModuleNotFoundError for evidence_evaluator.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


class EvidenceEvaluatorModuleTests(unittest.TestCase):
    def test_evidence_evaluator_import(self) -> None:
        from local_cli_coordinator.evidence_evaluator import (
            CompletionGateResult,
            evaluate_completion_evidence,
            record_rules_verdict,
        )
        self.assertTrue(callable(evaluate_completion_evidence))
        self.assertTrue(callable(record_rules_verdict))
        self.assertIn("allowed", CompletionGateResult.__annotations__)


class AcceptanceCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        self.conn.commit()
        self.project_id = self.conn.execute(
            "select id from projects limit 1"
        ).fetchone()["id"]
        self.task_id = create_task(
            self.conn,
            title="Acceptance task",
            repo="test-repo",
            source_path="task.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["login works", "tests pass"],
            verification_commands=["pytest"],
            project_id=self.project_id,
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_criteria_not_covered_without_evidence(self) -> None:
        from local_cli_coordinator.evidence_evaluator import evaluate_completion_evidence

        result = evaluate_completion_evidence(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
        )
        self.assertFalse(result.allowed)
        self.assertTrue(result.missing_acceptance)

    def test_failed_commands_block_completion(self) -> None:
        from local_cli_coordinator.evidence import record_evidence
        from local_cli_coordinator.evidence_evaluator import evaluate_completion_evidence

        record_evidence(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            evidence_type="command",
            status="failed",
            summary="pytest failed",
            data={"command": "pytest", "exit_code": 1},
        )
        record_evidence(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            evidence_type="diff",
            status="present",
            summary="files changed",
            data={"changed_files": ["src/login.py"]},
        )
        result = evaluate_completion_evidence(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
        )
        self.assertFalse(result.allowed)
        self.assertIn("command", result.blockers[0].lower())

    def test_rules_verdict_is_independent_of_worker_output(self) -> None:
        from local_cli_coordinator.evidence_evaluator import record_rules_verdict

        verdict_id = record_rules_verdict(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            verdict="reject",
            rationale="independent rules evaluator rejection",
            evidence_ids=[],
            reviewer_id="rules-v2",
        )
        self.assertIsInstance(verdict_id, int)
        row = self.conn.execute(
            "select reviewer_id, verdict from task_review_verdicts where id = ?",
            (verdict_id,),
        ).fetchone()
        self.assertEqual(row["reviewer_id"], "rules-v2")
        self.assertEqual(row["verdict"], "reject")


if __name__ == "__main__":
    unittest.main()