import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config import load_config
from local_cli_coordinator.readiness import check_loop_readiness
from tests.helpers import run_cli


EXPECTED_LABELS = [
    "discovery source",
    "state file",
    "evaluator",
    "worktree isolation",
    "budget cap",
    "human review point",
]


def write_minimal_config(root: Path) -> None:
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


class ReadinessTests(unittest.TestCase):
    def test_no_config_returns_six_warning_checks_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checks = check_loop_readiness(Path(tmp), None)

        self.assertEqual([check.name for check in checks], EXPECTED_LABELS)
        self.assertEqual(len(checks), 6)
        self.assertTrue(all(check.status in {"warn", "fail"} for check in checks))

    def test_minimal_config_reports_expected_check_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tasks" / "inbox").mkdir(parents=True)
            (root / "coordinator.db").touch()
            write_minimal_config(root)
            config = load_config(root)

            checks = check_loop_readiness(root, config)

        self.assertEqual([check.name for check in checks], EXPECTED_LABELS)

    def test_doctor_output_includes_loop_readiness_section_and_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tasks" / "generated").mkdir(parents=True)
            (root / "state").mkdir()
            (root / "state" / "loop_state.md").write_text("# Loop State\n")
            write_minimal_config(root)

            result = run_cli("--root", str(root), "doctor")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Loop readiness", result.stdout)
        for label in EXPECTED_LABELS:
            self.assertIn(label, result.stdout)
