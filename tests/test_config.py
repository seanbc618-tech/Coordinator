import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config import load_config


class ConfigTests(unittest.TestCase):
    def test_loads_agents_repos_and_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "agents.toml").write_text(textwrap.dedent("""
                [agents.codex]
                command = "codex exec --json {prompt_path}"
                capabilities = ["code", "tests"]
                max_concurrency = 2
            """).strip())
            (root / "config" / "repos.toml").write_text(textwrap.dedent("""
                [repos.demo]
                path = "/tmp/demo"
                default_branch = "main"
                remote = "origin"
                branch_prefix = "coord/"
                allow_push = true
                merge_policy = "push_branch_only"
                verify_commands = ["python -m unittest"]
            """).strip())
            (root / "config" / "policy.toml").write_text(textwrap.dedent("""
                [task_policy]
                require_single_repo = true
                require_acceptance_criteria = true
                require_verification_commands = true
                require_handoff_summary = true
                max_files_touched = 3
                max_expected_minutes = 30
                max_attempts = 3
                split_if_touches_multiple_subsystems = true
                split_if_research_and_code_are_mixed = true
            """).strip())

            config = load_config(root)

            self.assertEqual(config.agents["codex"].max_concurrency, 2)
            self.assertEqual(config.repos["demo"].merge_policy, "push_branch_only")
            self.assertEqual(config.policy.max_files_touched, 3)
