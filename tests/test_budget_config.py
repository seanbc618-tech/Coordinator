import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config import load_config


def write_config(root: Path, policy_extra: str = "") -> None:
    (root / "config").mkdir()
    (root / "config" / "agents.toml").write_text(textwrap.dedent("""
        [agents.codex]
        command = "codex exec --json {prompt_path}"
        capabilities = ["code", "tests"]
        max_concurrency = 1
    """).strip())
    (root / "config" / "repos.toml").write_text(textwrap.dedent("""
        [repos.demo]
        path = "/tmp/demo"
        default_branch = "main"
        remote = "origin"
        branch_prefix = "coord/"
        allow_push = false
        merge_policy = "no_push"
        verify_commands = ["python -m unittest"]
    """).strip())
    (root / "config" / "policy.toml").write_text(textwrap.dedent(f"""
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
        {policy_extra}
    """).strip())


class BudgetConfigTests(unittest.TestCase):
    def test_loads_explicit_budget_caps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(
                root,
                """
                max_task_runtime_seconds = 1200
                max_daemon_runtime_seconds = 2400
                max_tasks_per_run = 4
                max_tasks_per_day = 12
                max_consecutive_failures = 2
                """,
            )

            config = load_config(root)

        self.assertEqual(config.policy.max_task_runtime_seconds, 1200)
        self.assertEqual(config.policy.max_daemon_runtime_seconds, 2400)
        self.assertEqual(config.policy.max_tasks_per_run, 4)
        self.assertEqual(config.policy.max_tasks_per_day, 12)
        self.assertEqual(config.policy.max_consecutive_failures, 2)

    def test_missing_budget_caps_use_conservative_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root)

            config = load_config(root)

        self.assertEqual(config.policy.max_task_runtime_seconds, 1800)
        self.assertEqual(config.policy.max_daemon_runtime_seconds, 3600)
        self.assertEqual(config.policy.max_tasks_per_run, 1)
        self.assertEqual(config.policy.max_tasks_per_day, 24)
        self.assertEqual(config.policy.max_consecutive_failures, 3)
