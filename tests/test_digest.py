import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from local_cli_coordinator.db import init_db, MIGRATIONS_DIR
from local_cli_coordinator.digest import generate_digest, write_daily_digest


class TestDigest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn, MIGRATIONS_DIR)
        
        # Create some tasks
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # A completed task
        self.conn.execute("""
            insert into tasks(id, title, repo, state, priority, capabilities, source_path, goal, acceptance_criteria, verification_commands, updated_at)
            values ('task-1', 'Fix bug', 'repo1', 'done', 'high', '[]', '', 'fix', '[]', '[]', current_timestamp)
        """)
        
        # A failed task
        self.conn.execute("""
            insert into tasks(id, title, repo, state, priority, capabilities, source_path, goal, acceptance_criteria, verification_commands, updated_at)
            values ('task-2', 'Bad feature', 'repo1', 'failed', 'high', '[]', '', 'fix', '[]', '[]', current_timestamp)
        """)
        
        # Create a mock diff artifact for task-1
        diff_path = self.root / "diff.patch"
        diff_path.write_text("+++ b/src/main.py\n@@ -1,1 +1,1 @@\n-a\n+b\n+++ b/src/utils.py\n")
        
        self.conn.execute("""
            insert into artifacts(task_id, kind, path)
            values ('task-1', 'diff', ?)
        """, (str(diff_path),))
        
        # Mock artifact for task-2
        diff2_path = self.root / "diff2.patch"
        diff2_path.write_text("+++ b/src/main.py\n")
        
        self.conn.execute("""
            insert into artifacts(task_id, kind, path)
            values ('task-2', 'diff', ?)
        """, (str(diff2_path),))
        
        self.conn.commit()
        self.date_str = date_str

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_generate_digest(self):
        digest = generate_digest(self.conn, self.date_str, self.root)
        
        self.assertIn(f"# Loop Comprehension Digest: {self.date_str}", digest)
        self.assertIn("## Completed Tasks", digest)
        self.assertIn("- **task-1** (repo1): Fix bug", digest)
        self.assertIn("## Failed Tasks", digest)
        self.assertIn("- **task-2** (repo1): Bad feature", digest)
        
        self.assertIn("## Top Changed Files", digest)
        self.assertIn("- `src/main.py` (touched by 2 tasks)", digest)
        self.assertIn("- `src/utils.py` (touched by 1 task)", digest)

    def test_write_daily_digest(self):
        out_path = write_daily_digest(self.conn, self.root, self.date_str)
        self.assertTrue(out_path.exists())
        self.assertEqual(out_path.parent.name, "digests")
        self.assertEqual(out_path.name, f"{self.date_str}.md")
        
        content = out_path.read_text()
        self.assertIn("- **task-1**", content)

if __name__ == "__main__":
    unittest.main()
