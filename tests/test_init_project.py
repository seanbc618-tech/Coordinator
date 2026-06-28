"""Red tests for Phase 6D ``coordinator init``.

Owner: Grok (Phase 6D Task 0)
Expected before implementation: missing ``init`` subcommand or unsafe writes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import ROOT, SRC, init_git_repo

_PYTHON = sys.executable


def _run_cli_with_home(
    home: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["COORDINATOR_HOME"] = str(home)
    return subprocess.run(
        [_PYTHON, "-m", "local_cli_coordinator", *args],
        cwd=cwd or ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class InitProjectRedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "coordinator-home"
        self.home.mkdir()
        self.repo = Path(self.tmp.name) / "polymarket-crypto-threshold"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_init_creates_minimal_global_config_for_current_git_repo(self) -> None:
        result = _run_cli_with_home(self.home, "init", "--yes", cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("agents.toml", "repos.toml", "policy.toml"):
            path = self.paths.config_dir / name
            self.assertTrue(path.is_file(), f"missing {name}")
        repos_text = (self.paths.config_dir / "repos.toml").read_text()
        self.assertIn("polymarket_crypto_threshold", repos_text)
        self.assertIn(str(self.repo.resolve()), repos_text)

    def test_init_is_idempotent(self) -> None:
        first = _run_cli_with_home(self.home, "init", "--yes", cwd=self.repo)
        self.assertEqual(first.returncode, 0, first.stderr)
        agents_before = (self.paths.config_dir / "agents.toml").read_text()
        second = _run_cli_with_home(self.home, "init", "--yes", cwd=self.repo)
        self.assertEqual(second.returncode, 0, second.stderr)
        agents_after = (self.paths.config_dir / "agents.toml").read_text()
        self.assertEqual(agents_before, agents_after)

    def test_init_dry_run_json_does_not_write(self) -> None:
        result = _run_cli_with_home(
            self.home, "init", "--dry-run", "--json", cwd=self.repo
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["ok"])
        self.assertEqual(data["command"], "init")
        self.assertFalse((self.paths.config_dir / "repos.toml").exists())

    def test_init_refuses_non_git_directory(self) -> None:
        not_git = Path(self.tmp.name) / "not-git"
        not_git.mkdir()
        result = _run_cli_with_home(self.home, "init", cwd=not_git)
        self.assertEqual(result.returncode, 1, result.stderr)
        combined = f"{result.stdout}\n{result.stderr}".lower()
        self.assertRegex(combined, r"git (repo|repository|root)")
        self.assertNotIn("project not registered", combined)
        self.assertFalse((self.paths.config_dir / "repos.toml").exists())

    def test_init_does_not_enable_autonomy_by_default(self) -> None:
        result = _run_cli_with_home(self.home, "init", "--yes", cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        repos_text = (self.paths.config_dir / "repos.toml").read_text().lower()
        policy_text = (self.paths.config_dir / "policy.toml").read_text().lower()
        self.assertNotIn("autonomy_enabled = true", repos_text)
        self.assertNotIn("enabled = true", policy_text.split("[autonomy]")[-1][:200])