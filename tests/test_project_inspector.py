"""Phase 15 red tests: project shape inspection for onboarding.

Owner: Grok (Phase 15 Task 0)
Expected before implementation: project_inspector module missing.
"""

from __future__ import annotations

import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import init_git_repo


class ProjectInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "python-repo"
        init_git_repo(self.repo)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_python_repo(self) -> Path:
        (self.repo / "pyproject.toml").write_text(
            textwrap.dedent("""
                [project]
                name = "demo"
                version = "0.1.0"
            """).strip()
        )
        tests_dir = self.repo / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_demo.py").write_text("import unittest\n")
        subprocess.run(["git", "add", "."], cwd=self.repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add python project"],
            cwd=self.repo,
            capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
            },
        )
        return self.repo

    def test_detects_python_profile_and_verify_commands(self) -> None:
        from local_cli_coordinator.project_inspector import inspect_project_shape

        repo = self._make_python_repo()
        result = inspect_project_shape(repo)
        self.assertEqual(result.detected_profile, "python")
        self.assertEqual(result.recommended_preset, "observe")
        self.assertGreater(result.confidence, 0.0)
        self.assertIn("python3 -m unittest discover -s tests -q", result.verify_commands)

    def test_detects_node_profile_from_package_json(self) -> None:
        from local_cli_coordinator.project_inspector import inspect_project_shape

        node_repo = self.tmp / "node-repo"
        init_git_repo(node_repo)
        (node_repo / "package.json").write_text(
            textwrap.dedent("""
                {
                  "name": "demo",
                  "scripts": {
                    "test": "vitest",
                    "lint": "eslint .",
                    "typecheck": "tsc --noEmit"
                  }
                }
            """).strip()
        )
        subprocess.run(["git", "add", "."], cwd=node_repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add node project"],
            cwd=node_repo,
            capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
            },
        )
        result = inspect_project_shape(node_repo)
        self.assertEqual(result.detected_profile, "node")
        self.assertIn("npm test -- --run", result.verify_commands)
        self.assertIn("npm run lint", result.verify_commands)
        self.assertIn("npm run typecheck", result.verify_commands)

    def test_detects_mixed_profile_when_python_and_node_present(self) -> None:
        from local_cli_coordinator.project_inspector import inspect_project_shape

        mixed = self.tmp / "mixed-repo"
        init_git_repo(mixed)
        (mixed / "pyproject.toml").write_text("[project]\nname='demo'\n")
        (mixed / "package.json").write_text('{"name":"demo","scripts":{"test":"vitest"}}')
        subprocess.run(["git", "add", "."], cwd=mixed, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "mixed"],
            cwd=mixed,
            capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
            },
        )
        result = inspect_project_shape(mixed)
        self.assertEqual(result.detected_profile, "mixed")
        self.assertLessEqual(len(result.verify_commands), 4)

    def test_detects_docs_profile_for_markdown_only_repo(self) -> None:
        from local_cli_coordinator.project_inspector import inspect_project_shape

        docs_repo = self.tmp / "docs-repo"
        init_git_repo(docs_repo)
        (docs_repo / "README.md").write_text("# Docs\n")
        (docs_repo / "guide.md").write_text("# Guide\n")
        subprocess.run(["git", "add", "."], cwd=docs_repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "docs"],
            cwd=docs_repo,
            capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
            },
        )
        result = inspect_project_shape(docs_repo)
        self.assertEqual(result.detected_profile, "docs")
        self.assertEqual(result.verify_commands, [])

    def test_unknown_profile_when_no_signals(self) -> None:
        from local_cli_coordinator.project_inspector import inspect_project_shape

        bare = self.tmp / "bare-repo"
        init_git_repo(bare)
        result = inspect_project_shape(bare)
        self.assertEqual(result.detected_profile, "unknown")
        self.assertEqual(result.verify_commands, [])

    def test_rejects_missing_path(self) -> None:
        from local_cli_coordinator.project_inspector import inspect_project_shape

        with self.assertRaises(ValueError):
            inspect_project_shape(self.tmp / "missing")

    def test_rejects_non_git_without_allow_flag(self) -> None:
        from local_cli_coordinator.project_inspector import inspect_project_shape

        non_git = self.tmp / "not-git"
        non_git.mkdir()
        (non_git / "README.md").write_text("no git\n")
        with self.assertRaises(ValueError):
            inspect_project_shape(non_git)

    def test_inspection_never_executes_verify_commands(self) -> None:
        from local_cli_coordinator.project_inspector import inspect_project_shape

        repo = self._make_python_repo()

        def _guard_subprocess(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args")
            if isinstance(cmd, (list, tuple)) and cmd:
                joined = " ".join(str(part) for part in cmd)
                if any(
                    token in joined
                    for token in ("pytest", "unittest discover", "npm test", "npm run")
                ):
                    raise AssertionError(f"inspection executed verify command: {joined}")
            return subprocess.run(*args, **kwargs)

        with patch("subprocess.run", side_effect=_guard_subprocess):
            result = inspect_project_shape(repo)
        self.assertTrue(result.verify_commands)

    def test_redacts_home_paths_in_human_summary(self) -> None:
        from local_cli_coordinator.project_inspector import (
            format_inspection_summary,
            inspect_project_shape,
        )

        repo = self._make_python_repo()
        home = Path("/Users/tester")
        result = inspect_project_shape(repo)
        summary = format_inspection_summary(result, home=home)
        self.assertNotIn(str(repo), summary)
        self.assertNotIn("/Users/tester", summary)


if __name__ == "__main__":
    unittest.main()