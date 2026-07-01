"""Phase 20 release check command tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.release_checks import run_release_checks
from local_cli_coordinator.runtime_paths import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
_PYTHON = sys.executable


def _write_config(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "agents.toml").write_text("[agents]\n")
    (config_dir / "repos.toml").write_text("[repos]\n")
    (config_dir / "policy.toml").write_text("[task_policy]\n")


class ReleaseChecksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.paths = RuntimePaths(
            self.tmp / "config",
            self.tmp / "data",
            self.tmp / "state",
        )
        self.paths.create()
        _write_config(self.paths.config_dir)
        self.conn = connect(self.paths.database)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_run_release_checks_returns_structured_report(self) -> None:
        result = run_release_checks(self.conn, self.paths)
        self.assertIn("checks", result)
        names = {item["name"] for item in result["checks"]}
        self.assertTrue(
            {"version", "config", "schema", "migration_mirror", "extensions"} <= names
        )

    def test_cli_release_check_json(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC)
        env["COORDINATOR_HOME"] = str(self.tmp)
        proc = subprocess.run(
            [_PYTHON, "-m", "local_cli_coordinator", "release", "check", "--json"],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "release.check")
        self.assertIn("checks", payload["data"])


if __name__ == "__main__":
    unittest.main()