import tempfile
import unittest
from pathlib import Path

from tests.helpers import run_cli


class DiscoverCliTests(unittest.TestCase):
    def test_discover_once_no_config_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "discover", "--once")
        self.assertEqual(result.returncode, 1)
        self.assertIn("error", result.stderr.lower())

    def test_discover_once_no_sources_reports_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "agents.toml").write_text("[agents]\n")
            (config_dir / "repos.toml").write_text("[repos]\n")
            (config_dir / "policy.toml").write_text(
                "[task_policy]\n"
                "require_single_repo = true\n"
                "require_acceptance_criteria = true\n"
                "require_verification_commands = true\n"
                "require_handoff_summary = true\n"
                "max_files_touched = 5\n"
                "max_expected_minutes = 30\n"
                "max_attempts = 3\n"
                "split_if_touches_multiple_subsystems = true\n"
                "split_if_research_and_code_are_mixed = true\n"
            )
            result = run_cli("--root", str(root), "discover", "--once")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no discovery sources configured", result.stdout)

    def test_discover_reports_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "agents.toml").write_text("[agents]\n")
            (config_dir / "repos.toml").write_text("[repos]\n")
            (config_dir / "policy.toml").write_text(
                "[task_policy]\n"
                "require_single_repo = true\n"
                "require_acceptance_criteria = true\n"
                "require_verification_commands = true\n"
                "require_handoff_summary = true\n"
                "max_files_touched = 5\n"
                "max_expected_minutes = 30\n"
                "max_attempts = 3\n"
                "split_if_touches_multiple_subsystems = true\n"
                "split_if_research_and_code_are_mixed = true\n"
            )
            (config_dir / "discovery.toml").write_text(
                "[sources.inbox]\n"
                'type = "inbox"\n'
                "[sources.inbox.repos]\n"
                "demo = true\n"
            )
            result = run_cli("--root", str(root), "discover", "--once")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("discovered:", result.stdout)
        self.assertIn("skipped:", result.stdout)
        self.assertIn("failed:", result.stdout)
        self.assertIn("total findings on disk:", result.stdout)
