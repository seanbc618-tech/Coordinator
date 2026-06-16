import sys
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.agent import run_agent
from local_cli_coordinator.config import AgentConfig


class AgentRunnerTests(unittest.TestCase):
    def test_runs_configured_command_and_captures_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run"
            worktree.mkdir()
            run_dir.mkdir()
            prompt = run_dir / "prompt.md"
            prompt.write_text("write output")
            agent = AgentConfig(
                id="fake",
                command=f"{sys.executable} -c \"from pathlib import Path; Path('agent-output.txt').write_text('done')\"",
                capabilities=["code"],
                max_concurrency=1,
            )

            result = run_agent(agent, prompt, worktree, run_dir)

            self.assertEqual(result.exit_code, 0)
            self.assertTrue((worktree / "agent-output.txt").exists())
            self.assertTrue(result.log_path.exists())

    def test_runs_command_with_literal_braces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run"
            worktree.mkdir()
            run_dir.mkdir()
            prompt = run_dir / "prompt.md"
            prompt.write_text("write output")
            agent = AgentConfig(
                id="fake",
                command=f"{sys.executable} -c \"from pathlib import Path; Path('agent-output.txt').write_text(str({{}}))\"",
                capabilities=["code"],
                max_concurrency=1,
            )

            result = run_agent(agent, prompt, worktree, run_dir)

            self.assertEqual(result.exit_code, 0)
            self.assertEqual((worktree / "agent-output.txt").read_text(), "{}")
            self.assertTrue(result.log_path.exists())

    def test_prompt_path_placeholder_with_spaces_is_one_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run dir"
            worktree.mkdir()
            run_dir.mkdir()
            prompt = run_dir / "prompt file.md"
            prompt.write_text("write output")
            agent = AgentConfig(
                id="fake",
                command=(
                    f"{sys.executable} -c \"import sys; from pathlib import Path; "
                    "Path('argv.txt').write_text(str(len(sys.argv)) + '\\n' + sys.argv[1])\" "
                    "{prompt_path}"
                ),
                capabilities=["code"],
                max_concurrency=1,
            )

            result = run_agent(agent, prompt, worktree, run_dir)

            self.assertEqual(result.exit_code, 0)
            self.assertEqual((worktree / "argv.txt").read_text(), f"2\n{prompt}")

    def test_missing_binary_returns_failure_and_writes_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run"
            worktree.mkdir()
            run_dir.mkdir()
            prompt = run_dir / "prompt.md"
            prompt.write_text("write output")
            agent = AgentConfig(
                id="missing",
                command="local-cli-coordinator-missing-binary",
                capabilities=["code"],
                max_concurrency=1,
            )

            result = run_agent(agent, prompt, worktree, run_dir)

            self.assertEqual(result.exit_code, 127)
            self.assertTrue(result.log_path.exists())
            log = result.log_path.read_text()
            self.assertIn("local-cli-coordinator-missing-binary", log)
            self.assertIn("error:", log)

    def test_empty_command_returns_failure_and_writes_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run"
            worktree.mkdir()
            run_dir.mkdir()
            prompt = run_dir / "prompt.md"
            prompt.write_text("write output")
            agent = AgentConfig(
                id="empty",
                command="",
                capabilities=["code"],
                max_concurrency=1,
            )

            result = run_agent(agent, prompt, worktree, run_dir)

            self.assertEqual(result.exit_code, 127)
            self.assertTrue(result.log_path.exists())
            self.assertIn("empty agent command", result.log_path.read_text())
