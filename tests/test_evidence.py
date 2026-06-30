"""Red tests for Phase 8 task evidence persistence.

Owner: Grok (Phase 8 Task 0)
Expected before implementation: ModuleNotFoundError for evidence.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


class EvidenceModuleTests(unittest.TestCase):
    def test_evidence_module_import(self) -> None:
        from local_cli_coordinator.evidence import (
            TaskEvidence,
            list_task_evidence,
            record_evidence,
        )
        self.assertTrue(callable(record_evidence))
        self.assertTrue(callable(list_task_evidence))
        self.assertIn("id", TaskEvidence.__annotations__)


class EvidencePersistenceTests(unittest.TestCase):
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
            title="Evidence task",
            repo="test-repo",
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

    def test_record_command_evidence_persists_row(self) -> None:
        from local_cli_coordinator.evidence import list_task_evidence, record_evidence

        evidence_id = record_evidence(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            evidence_type="command",
            status="passed",
            summary="verification command succeeded",
            data={"command": "true", "exit_code": 0},
        )
        self.assertIsInstance(evidence_id, int)
        rows = list_task_evidence(
            self.conn, project_id=self.project_id, task_id=self.task_id
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].evidence_type, "command")
        self.assertEqual(rows[0].status, "passed")

    def test_failed_command_evidence_is_not_hidden(self) -> None:
        from local_cli_coordinator.evidence import list_task_evidence, record_evidence

        record_evidence(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            evidence_type="command",
            status="failed",
            summary="verification command failed",
            data={"command": "false", "exit_code": 1},
        )
        rows = list_task_evidence(
            self.conn, project_id=self.project_id, task_id=self.task_id
        )
        self.assertEqual(rows[0].status, "failed")
        self.assertIn("failed", rows[0].summary.lower())

    def test_diff_evidence_records_changed_files(self) -> None:
        from local_cli_coordinator.evidence import list_task_evidence, record_evidence

        record_evidence(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            evidence_type="diff",
            status="present",
            summary="2 files changed",
            data={"changed_files": ["src/a.py", "tests/test_a.py"], "insertions": 10},
        )
        row = list_task_evidence(
            self.conn, project_id=self.project_id, task_id=self.task_id
        )[0]
        self.assertEqual(row.evidence_type, "diff")
        self.assertEqual(row.data["changed_files"], ["src/a.py", "tests/test_a.py"])

    def test_evidence_is_project_scoped(self) -> None:
        from local_cli_coordinator.evidence import list_task_evidence, record_evidence

        repo_b = self.tmp / "repo-b"
        init_git_repo(repo_b)
        draft_b = inspect_project(repo_b)
        register_project(self.conn, draft_b, confirmed=True)
        self.conn.commit()
        project_b = self.conn.execute(
            "select id from projects where id != ? limit 1",
            (self.project_id,),
        ).fetchone()["id"]
        task_b = create_task(
            self.conn,
            title="Secret task",
            repo="test-repo",
            source_path="b.md",
            priority="normal",
            capabilities=["code"],
            goal="g",
            acceptance_criteria=["a"],
            verification_commands=["true"],
            project_id=project_b,
        )
        self.conn.commit()
        record_evidence(
            self.conn,
            project_id=project_b,
            task_id=task_b,
            evidence_type="command",
            status="failed",
            summary="Project B secret failure",
            data={"command": "false"},
        )
        record_evidence(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            evidence_type="command",
            status="passed",
            summary="Project A pass",
            data={"command": "true"},
        )
        rows = list_task_evidence(
            self.conn, project_id=self.project_id, task_id=self.task_id
        )
        serialized = json.dumps([row.summary for row in rows])
        self.assertNotIn("Project B secret failure", serialized)


if __name__ == "__main__":
    unittest.main()