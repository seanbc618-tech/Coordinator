"""Phase 20 release check command tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.release_checks import (
    _migration_mirror_check,
    run_release_checks,
)
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

    def test_migration_mirror_check_skips_without_source_mirror(self) -> None:
        with patch(
            "local_cli_coordinator.release_checks._repo_migrations_mirror",
            return_value=None,
        ):
            mirror = _migration_mirror_check()
        self.assertTrue(mirror["ok"])
        self.assertTrue(mirror["skipped"])
        self.assertGreater(mirror["package_count"], 0)
        self.assertEqual(mirror["errors"], [])


class WheelReleaseCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _build_wheel(self) -> Path:
        result = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(self.root)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        wheels = sorted(self.root.glob("*.whl"))
        self.assertEqual(len(wheels), 1, "Expected exactly one wheel")
        return wheels[0]

    def test_installed_wheel_release_check_json_ok(self) -> None:
        wheel_path = self._build_wheel()
        venv_dir = self.root / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        coordinator = venv_dir / "bin" / "coordinator"
        pip = venv_dir / "bin" / "pip"
        subprocess.run(
            [str(pip), "install", "--force-reinstall", str(wheel_path)],
            check=True,
        )

        home = self.root / "home"
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["COORDINATOR_HOME"] = str(home)

        init = subprocess.run(
            [str(coordinator), "init", "--yes", "--json"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(init.returncode, 0, init.stderr or init.stdout)

        proc = subprocess.run(
            [str(coordinator), "release", "check", "--json"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["command"], "release.check")
        checks = {item["name"]: item for item in payload["data"]["checks"]}
        self.assertTrue(checks["migration_mirror"]["ok"])
        self.assertTrue(checks["migration_mirror"]["details"]["skipped"])


if __name__ == "__main__":
    unittest.main()