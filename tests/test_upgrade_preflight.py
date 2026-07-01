"""Phase 20 upgrade preflight tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.backup_manager import create_backup
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.upgrade_preflight import run_upgrade_preflight


def _write_config(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "agents.toml").write_text("[agents]\n")
    (config_dir / "repos.toml").write_text("[repos]\n")
    (config_dir / "policy.toml").write_text("[task_policy]\n")


class UpgradePreflightTests(unittest.TestCase):
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

    def test_preflight_warns_without_backup(self) -> None:
        result = run_upgrade_preflight(self.conn, self.paths)
        self.assertIn(result["status"], {"warn", "pass"})
        self.assertTrue(result["backup_recommended"])
        codes = {item["code"] for item in result["findings"]}
        self.assertIn("backup_recommended", codes)

    def test_preflight_passes_after_backup(self) -> None:
        create_backup(self.conn, self.paths)
        result = run_upgrade_preflight(self.conn, self.paths)
        self.assertIn(result["status"], {"pass", "warn"})
        backup_findings = [
            item for item in result["findings"] if item["code"] == "backup_recommended"
        ]
        self.assertEqual(backup_findings, [])

    def test_preflight_fails_when_config_missing(self) -> None:
        (self.paths.config_dir / "policy.toml").unlink()
        result = run_upgrade_preflight(self.conn, self.paths)
        self.assertEqual(result["status"], "fail")
        codes = {item["code"] for item in result["findings"]}
        self.assertIn("missing_config", codes)

    def test_preflight_records_run(self) -> None:
        result = run_upgrade_preflight(self.conn, self.paths)
        row = self.conn.execute(
            "select status, findings_json from upgrade_preflight_runs where id = ?",
            (result["run_id"],),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], result["status"])
        findings = json.loads(row["findings_json"])
        self.assertIsInstance(findings, list)


if __name__ == "__main__":
    unittest.main()