"""Runtime config loading from flat XDG layout (no temp shim)."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config import load_config_from_dir
from local_cli_coordinator.config_runtime import load_config_for_paths
from local_cli_coordinator.runtime_paths import RuntimePaths


def _write_minimal_policy(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """
            [task_policy]
            require_single_repo = false
            require_acceptance_criteria = false
            require_verification_commands = false
            require_handoff_summary = false
            max_files_touched = 10
            max_expected_minutes = 60
            max_attempts = 3
            split_if_touches_multiple_subsystems = false
            split_if_research_and_code_are_mixed = false
            """
        ).strip()
    )


class ConfigRuntimeTests(unittest.TestCase):
    def test_load_config_from_dir_reads_flat_xdg_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "coordinator"
            config_dir.mkdir()
            (config_dir / "agents.toml").write_text(
                '[agents.w]\ncommand = "true"\nrole = "worker"\n'
            )
            (config_dir / "repos.toml").write_text(
                textwrap.dedent(
                    """
                    [repos.demo]
                    path = "/tmp/demo"
                    default_branch = "main"
                    """
                ).strip()
            )
            _write_minimal_policy(config_dir / "policy.toml")

            cfg = load_config_from_dir(config_dir)

            self.assertIn("w", cfg.agents)
            self.assertEqual(cfg.agents["w"].command, "true")
            self.assertIn("demo", cfg.repos)
            self.assertEqual(cfg.repos["demo"].path, Path("/tmp/demo"))

    def test_load_config_for_paths_uses_runtime_paths_config_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            paths = RuntimePaths(
                config_dir=base / "config",
                data_dir=base / "data",
                state_dir=base / "state",
            )
            paths.config_dir.mkdir(parents=True)
            (paths.config_dir / "agents.toml").write_text(
                '[agents.worker]\ncommand = "true"\nrole = "worker"\n'
            )
            (paths.config_dir / "repos.toml").write_text(
                textwrap.dedent(
                    """
                    [repos.demo]
                    path = "/tmp/demo"
                    default_branch = "main"
                    """
                ).strip()
            )
            _write_minimal_policy(paths.config_dir / "policy.toml")

            cfg = load_config_for_paths(paths)

            self.assertIn("worker", cfg.agents)


if __name__ == "__main__":
    unittest.main()