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
