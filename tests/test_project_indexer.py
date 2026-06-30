"""Phase 11 repository indexer safety contract tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.helpers import init_git_repo, run


class ProjectIndexerSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        (self.repo / "src").mkdir()
        (self.repo / "src" / "app.py").write_text("def main():\n    return 1\n")
        (self.repo / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
        (self.repo / ".gitignore").write_text(".env\nnode_modules/\n")
        (self.repo / ".env").write_text("API_KEY=super-secret-value\n")
        (self.repo / "node_modules").mkdir()
        (self.repo / "node_modules" / "pkg.js").write_text("module.exports = {}")
        run("git", "add", "src", "pyproject.toml", ".gitignore", cwd=self.repo)
        run("git", "commit", "-m", "add sources", cwd=self.repo)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_index_honors_gitignore(self) -> None:
        from local_cli_coordinator.project_indexer import index_repository

        (self.repo / ".gitignore").write_text("ignored/\n")
        (self.repo / "ignored").mkdir()
        (self.repo / "ignored" / "secret.py").write_text("TOKEN=abc\n")
        run("git", "add", ".gitignore", cwd=self.repo)
        run("git", "commit", "-m", "gitignore", cwd=self.repo)
        result = index_repository(self.repo)
        paths = {entry.path for entry in result.entries}
        self.assertNotIn("ignored/secret.py", paths)

    def test_index_ignores_env_and_vendor_dirs(self) -> None:
        from local_cli_coordinator.project_indexer import index_repository

        result = index_repository(self.repo)
        paths = {entry.path for entry in result.entries}
        self.assertIn("src/app.py", paths)
        self.assertIn("pyproject.toml", paths)
        self.assertNotIn(".env", paths)
        self.assertFalse(any(p.startswith("node_modules/") for p in paths))

    def test_index_rejects_path_outside_repo_root(self) -> None:
        from local_cli_coordinator.project_indexer import index_repository

        outside = self.tmp / "outside.txt"
        outside.write_text("nope")
        with self.assertRaises(ValueError):
            index_repository(outside)

    def test_index_redacts_secret_like_content_at_ingest(self) -> None:
        from local_cli_coordinator.project_indexer import index_repository

        (self.repo / "config.toml").write_text("token = leaked-value\n")
        run("git", "add", "config.toml", cwd=self.repo)
        run("git", "commit", "-m", "config", cwd=self.repo)
        result = index_repository(self.repo)
        blob = result.model_dump_json() if hasattr(result, "model_dump_json") else str(result)
        self.assertNotIn("leaked-value", blob)
        self.assertNotIn("super-secret-value", blob)

    def test_brain_card_persists_redacted_summary_not_raw_secret(self) -> None:
        import tempfile as tf

        from local_cli_coordinator.db import connect, init_db
        from local_cli_coordinator.project_brain import (
            create_brain_snapshot,
            upsert_brain_card,
        )

        tmp = Path(tf.mkdtemp())
        conn = connect(tmp / "d.db")
        init_db(conn)
        conn.execute(
            "insert into projects(id, canonical_path, repo_id) values ('proj-x', ?, 'd')",
            (str(self.repo.resolve()),),
        )
        snap = create_brain_snapshot(conn, project_id="proj-x", repo_path=self.repo)
        card = upsert_brain_card(
            conn,
            project_id="proj-x",
            snapshot_id=snap.id,
            card_type="config",
            title="auth",
            summary="api_key=raw-secret-value",
            citations=[{"path": "config.toml"}],
        )
        conn.commit()
        row = conn.execute(
            "select summary from project_brain_cards where id = ?",
            (card.id,),
        ).fetchone()
        conn.close()
        self.assertNotIn("raw-secret-value", row["summary"])
        self.assertIn("[REDACTED]", row["summary"])

    def test_index_captures_git_head_and_dirty_flag(self) -> None:
        from local_cli_coordinator.project_indexer import index_repository

        clean = index_repository(self.repo)
        self.assertTrue(clean.git_head)
        self.assertFalse(clean.git_dirty)
        (self.repo / "dirty.txt").write_text("wip\n")
        dirty = index_repository(self.repo)
        self.assertTrue(dirty.git_dirty)

    def test_index_is_idempotent_for_unchanged_repo(self) -> None:
        from local_cli_coordinator.project_indexer import index_repository

        first = index_repository(self.repo)
        second = index_repository(self.repo)
        self.assertEqual(first.git_head, second.git_head)
        self.assertEqual(first.file_count, second.file_count)


if __name__ == "__main__":
    unittest.main()