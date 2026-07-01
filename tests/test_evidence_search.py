"""Phase 18 evidence search tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.artifact_registry import register_artifact
from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.evidence_search import search_evidence
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


class EvidenceSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo_a = self.tmp / "repo-a"
        self.repo_b = self.tmp / "repo-b"
        init_git_repo(self.repo_a)
        init_git_repo(self.repo_b)
        self.paths = RuntimePaths(
            self.home / "config",
            self.home / "data",
            self.home / "state",
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        self.project_a = register_project(
            self.conn, inspect_project(self.repo_a), confirmed=True
        )
        self.project_b = register_project(
            self.conn, inspect_project(self.repo_b), confirmed=True
        )
        for project_id, task_id in (
            (self.project_a, "task-a"),
            (self.project_b, "task-b"),
        ):
            create_task(
                self.conn,
                title=f"title-{task_id}",
                repo="repo",
                source_path="task.md",
                priority="normal",
                capabilities=["code"],
                goal="goal",
                acceptance_criteria=["done"],
                verification_commands=[],
                project_id=project_id,
            )
        self.conn.commit()
        for project_id, task_id, secret in (
            (self.project_a, "task-a", "token=alpha-secret"),
            (self.project_b, "task-b", "token=beta-secret"),
        ):
            path = self.paths.data_dir / "runs" / project_id / f"{task_id}.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(secret + "\n", encoding="utf-8")
            register_artifact(
                self.conn,
                paths=self.paths,
                project_id=project_id,
                artifact_type="log",
                path=path,
                task_id=task_id,
                provenance={"note": secret},
            )

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_project_search_is_scoped(self) -> None:
        result = search_evidence(
            self.conn,
            paths=self.paths,
            scope="project",
            project_id=self.project_a,
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["artifacts"][0]["project_id"], self.project_a)

    def test_global_search_hides_paths_by_default(self) -> None:
        result = search_evidence(
            self.conn,
            paths=self.paths,
            scope="global",
        )
        self.assertEqual(result["count"], 2)
        self.assertTrue(result["paths_hidden"])
        self.assertNotIn("artifacts", result)
        self.assertEqual(len(result["projects"]), 2)

    def test_search_redacts_secret_values(self) -> None:
        result = search_evidence(
            self.conn,
            paths=self.paths,
            scope="project",
            project_id=self.project_a,
        )
        provenance = result["artifacts"][0]["provenance"]
        self.assertNotIn("alpha-secret", str(provenance))

    def test_filter_by_artifact_type(self) -> None:
        patch_path = self.paths.data_dir / "runs" / self.project_a / "change.patch"
        patch_path.write_text("patch\n", encoding="utf-8")
        register_artifact(
            self.conn,
            paths=self.paths,
            project_id=self.project_a,
            artifact_type="patch",
            path=patch_path,
            task_id="task-a",
        )
        result = search_evidence(
            self.conn,
            paths=self.paths,
            scope="project",
            project_id=self.project_a,
            artifact_type="patch",
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["artifacts"][0]["artifact_type"], "patch")


if __name__ == "__main__":
    unittest.main()