"""Phase 9 red/contract tests for the safe GitHub CLI adapter."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT

_FAKE_GH = ROOT / "tests" / "fixtures" / "fake_gh.py"


class GitHubCliAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.log_path = self.tmp / "gh.log"
        self.env = os.environ.copy()
        self.env["GH_FAKE_LOG"] = str(self.log_path)
        self.env["GH_FAKE_SCENARIO"] = json.dumps(
            {
                "pr_number": 7,
                "pr_url": "https://github.com/example/coordinator/pull/7",
                "checks": [
                    {"name": "unit", "state": "SUCCESS", "bucket": "pass"},
                    {"name": "lint", "state": "FAILURE", "bucket": "fail"},
                ],
            }
        )

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_fake(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(_FAKE_GH), *args],
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_fake_gh_records_invocations(self) -> None:
        result = self._run_fake("pr", "view", "7", "--json", "number,url")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.log_path.exists())
        logged = self.log_path.read_text(encoding="utf-8")
        self.assertIn("pr view 7", logged)

    def test_github_cli_uses_argv_not_shell(self) -> None:
        from local_cli_coordinator import github_cli

        injection = "main; touch /tmp/pwned"
        client = github_cli.GitHubCli(
            executable=sys.executable,
            extra_prefix=[str(_FAKE_GH)],
            cwd=self.tmp,
            env=self.env,
        )
        result = client.pr_create(
            title="safe",
            body="evidence",
            base="main",
            head=injection,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        logged = self.log_path.read_text(encoding="utf-8")
        self.assertIn(injection, logged)
        self.assertNotIn("touch", logged.split("\n")[0])

    def test_github_cli_pr_view_parses_json(self) -> None:
        from local_cli_coordinator import github_cli

        client = github_cli.GitHubCli(
            executable=sys.executable,
            extra_prefix=[str(_FAKE_GH)],
            cwd=self.tmp,
            env=self.env,
        )
        view = client.pr_view(7)
        self.assertEqual(view.number, 7)
        self.assertIn("github.com", view.url)

    def test_github_cli_pr_checks_classifies_states(self) -> None:
        from local_cli_coordinator import github_cli

        client = github_cli.GitHubCli(
            executable=sys.executable,
            extra_prefix=[str(_FAKE_GH)],
            cwd=self.tmp,
            env=self.env,
        )
        checks = client.pr_checks(7)
        buckets = {item.name: item.bucket for item in checks}
        self.assertEqual(buckets["unit"], "pass")
        self.assertEqual(buckets["lint"], "fail")

    def test_github_cli_nonzero_exit_is_failure(self) -> None:
        from local_cli_coordinator import github_cli

        env = self.env.copy()
        env["GH_FAKE_SCENARIO"] = json.dumps({"exit_code": 1, "stderr": "auth failed"})
        client = github_cli.GitHubCli(
            executable=sys.executable,
            extra_prefix=[str(_FAKE_GH)],
            cwd=self.tmp,
            env=env,
        )
        result = client.pr_view(1)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()