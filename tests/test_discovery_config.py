import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config import load_config


class DiscoveryConfigTests(unittest.TestCase):
    def _write_base_config(self, root: Path) -> Path:
        config_dir = root / "config"
        config_dir.mkdir()
        (config_dir / "agents.toml").write_text(
            '[agents.echo]\ncommand = "echo {prompt_path}"\n',
            encoding="utf-8",
        )
        (config_dir / "repos.toml").write_text(textwrap.dedent("""
            [repos.demo]
            path = "/tmp/demo"
            default_branch = "main"
        """).strip(), encoding="utf-8")
        (config_dir / "policy.toml").write_text(textwrap.dedent("""
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
        """).strip(), encoding="utf-8")
        return config_dir

    def test_loads_supported_sources_and_repo_switches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = self._write_base_config(root)
            (config_dir / "discovery.toml").write_text(textwrap.dedent("""
                [sources.inbox]
                type = "inbox"
                [sources.inbox.repos]
                demo = true
                legacy = false

                [sources.commits]
                type = "git_recent_commits"

                [sources.command]
                type = "command"
                command = "python discover.py"

                [sources.ci]
                type = "ci_command"

                [sources.issues]
                type = "issue_command"
            """).strip(), encoding="utf-8")

            sources = load_config(root).discovery_sources

            self.assertEqual(
                {source.type for source in sources.values()},
                {"inbox", "git_recent_commits", "command", "ci_command", "issue_command"},
            )
            self.assertEqual(sources["inbox"].repos, {"demo": True, "legacy": False})
            self.assertEqual(sources["command"].command, "python discover.py")

    def test_missing_file_defaults_to_no_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_base_config(root)

            self.assertEqual(load_config(root).discovery_sources, {})

    def test_rejects_unknown_source_type_with_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = self._write_base_config(root)
            (config_dir / "discovery.toml").write_text(
                '[sources.broken]\ntype = "webhook"\n', encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, r"broken.*webhook"):
                load_config(root)

    def test_rejects_non_string_source_type_with_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = self._write_base_config(root)
            (config_dir / "discovery.toml").write_text(
                '[sources.broken]\ntype = ["inbox"]\n', encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, r"broken.*inbox"):
                load_config(root)

    def test_rejects_non_boolean_repo_flag_with_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = self._write_base_config(root)
            (config_dir / "discovery.toml").write_text(textwrap.dedent("""
                [sources.inbox]
                type = "inbox"
                [sources.inbox.repos]
                demo = "yes"
            """).strip(), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, r"inbox.*demo.*yes"):
                load_config(root)

    def test_rejects_malformed_repo_map_with_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = self._write_base_config(root)
            (config_dir / "discovery.toml").write_text(
                '[sources.inbox]\ntype = "inbox"\nrepos = ["demo"]\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, r"inbox.*repos"):
                load_config(root)


if __name__ == "__main__":
    unittest.main()
