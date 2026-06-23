import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from local_cli_coordinator.db import MIGRATIONS_DIR, connect, init_db
from local_cli_coordinator.digest import generate_digest, write_daily_digest
from local_cli_coordinator.goals import create_goal, transition_goal, update_goal_progress
from tests.helpers import run_cli


class DigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn, MIGRATIONS_DIR)
        self.date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        self.conn.execute(
            """
            insert into tasks(
                id, title, repo, state, priority, capabilities, source_path,
                goal, acceptance_criteria, verification_commands, updated_at
            ) values ('task-1', 'Fix bug', 'repo1', 'done', 'high', '[]', '',
                      'fix', '[]', '[]', current_timestamp)
            """
        )
        self.conn.execute(
            """
            insert into tasks(
                id, title, repo, state, priority, capabilities, source_path,
                goal, acceptance_criteria, verification_commands, updated_at
            ) values ('task-2', 'Bad feature', 'repo1', 'failed', 'high', '[]', '',
                      'fix', '[]', '[]', current_timestamp)
            """
        )

        diff_path = self.root / "diff.patch"
        diff_path.write_text("+++ b/src/main.py\n@@ -1,1 +1,1 @@\n-a\n+b\n+++ b/src/utils.py\n")
        self.conn.execute(
            "insert into artifacts(task_id, kind, path) values ('task-1', 'diff', ?)",
            (str(diff_path),),
        )

        diff2_path = self.root / "diff2.patch"
        diff2_path.write_text("+++ b/src/main.py\n")
        self.conn.execute(
            "insert into artifacts(task_id, kind, path) values ('task-2', 'diff', ?)",
            (str(diff2_path),),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self.temp_dir.cleanup()

    def test_generate_digest(self) -> None:
        digest = generate_digest(self.conn, self.date_str, self.root)

        self.assertIn(f"# Loop Comprehension Digest: {self.date_str}", digest)
        self.assertIn("## Completed Tasks", digest)
        self.assertIn("- **task-1** (repo1): Fix bug", digest)
        self.assertIn("## Failed Tasks", digest)
        self.assertIn("- **task-2** (repo1): Bad feature", digest)
        self.assertIn("## Top Changed Files", digest)
        self.assertIn("- `src/main.py` (touched by 2 tasks)", digest)
        self.assertIn("- `src/utils.py` (touched by 1 task)", digest)

    def test_generate_digest_includes_active_goal_progress(self) -> None:
        goal_id = create_goal(
            self.conn,
            "Roadmap",
            "Finish roadmap",
            completion_criteria=[],
            constraints=[],
            repo_ids=["demo"],
        )
        transition_goal(self.conn, goal_id, "active")
        update_goal_progress(self.conn, goal_id, "First slice complete")

        digest = generate_digest(self.conn, self.date_str, self.root)

        self.assertIn("## Active Goal Progress", digest)
        self.assertIn("Goal: active", digest)
        self.assertIn("First slice complete", digest)

    def test_write_daily_digest(self) -> None:
        out_path = write_daily_digest(self.conn, self.root, self.date_str)
        self.assertTrue(out_path.exists())
        self.assertEqual(out_path.parent.name, "digests")
        self.assertEqual(out_path.name, f"{self.date_str}.md")
        self.assertIn("- **task-1**", out_path.read_text())

    def test_digest_cli_creates_digest_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = connect(root / "coordinator.db")
            init_db(conn, MIGRATIONS_DIR)
            conn.execute(
                """
                insert into tasks(
                    id, title, repo, state, priority, capabilities, source_path,
                    goal, acceptance_criteria, verification_commands
                ) values ('task-x', 'Test task', 'demo', 'done', 'normal', '[]',
                          '', 'goal', '[]', '[]')
                """
            )
            conn.commit()
            conn.close()

            result = run_cli("--root", str(root), "digest")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("wrote daily digest to", result.stdout)
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            digest_file = root / "state" / "digests" / f"{date_str}.md"
            self.assertTrue(digest_file.exists(), f"digest file not found: {digest_file}")
            self.assertIn("task-x", digest_file.read_text())

    def test_digest_cli_fails_without_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "digest")
            self.assertEqual(result.returncode, 1)
            self.assertIn("does not exist", result.stderr)