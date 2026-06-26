import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config import load_config
from local_cli_coordinator.connectors import (
    load_connector_failures,
    run_connector,
)


def write_base_config(root: Path, connectors_toml: str | None = None) -> None:
    config_dir = root / "config"
    config_dir.mkdir()
    (config_dir / "agents.toml").write_text("[agents]\n")
    (config_dir / "repos.toml").write_text("[repos]\n")
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
    """).strip())
    if connectors_toml is not None:
        (config_dir / "connectors.toml").write_text(connectors_toml)


class ConnectorTests(unittest.TestCase):
    def test_loads_connector_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_base_config(
                root,
                textwrap.dedent("""
                    [connectors.echo]
                    command = "python3 -c 'print(1)'"
                    input = "json"
                    output = "json"
                """).strip(),
            )
            config = load_config(root)
            self.assertIn("echo", config.connectors)
            self.assertEqual(config.connectors["echo"].input_contract, "json")

    def test_run_connector_returns_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "echo.py"
            script.write_text(textwrap.dedent("""
                import json
                import sys

                payload = json.load(sys.stdin)
                print(json.dumps({"received": payload["name"]}))
            """).strip(), encoding="utf-8")
            result = run_connector(
                root=root,
                connector_id="echo",
                command=f"python3 {script}",
                payload={"name": "demo"},
            )
            self.assertEqual(result.failures, [])
            self.assertEqual(result.output, {"received": "demo"})

    def test_connector_failure_is_logged_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_connector(
                root=root,
                connector_id="broken",
                command="python3 -c 'import sys; sys.exit(3)'",
            )
            self.assertIsNone(result.output)
            self.assertEqual(len(result.failures), 1)
            logged = load_connector_failures(root)
            self.assertEqual(len(logged), 1)
            self.assertEqual(logged[0]["connector"], "broken")