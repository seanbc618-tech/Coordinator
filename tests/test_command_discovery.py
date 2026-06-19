import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config import (
    CoordinatorConfig,
    DiscoverySourceConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.discovery import (
    discover_from_command,
    list_findings,
    load_discovery_failures,
    run_configured_discovery,
)


class CommandDiscoveryTests(unittest.TestCase):
    def test_parses_jsonl_output_into_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script_path = root / "emit_finding.py"
            script_path.write_text(textwrap.dedent("""
                import json

                print(json.dumps({
                    "id": "finding-cmd-001",
                    "repo": "demo",
                    "source": "ci_scan",
                    "title": "Failing test detected",
                    "body": "test_rules.py failed on main",
                    "severity": "high",
                    "evidence": "test=test_rules.py;exit_code=1",
                    "discovered_at": "2026-06-19T12:00:00Z",
                }))
            """).strip(), encoding="utf-8")

            result = discover_from_command(
                root=root,
                source_id="ci_scan",
                command=f"python3 {script_path}",
                repo_id="demo",
                enabled_repos={"demo": True},
                persist=True,
            )

            self.assertEqual(result.failures, [])
            self.assertEqual(len(result.findings), 1)
            self.assertEqual(result.findings[0].title, "Failing test detected")
            self.assertIn("test_rules.py", result.findings[0].evidence)

            loaded = list_findings(root)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].id, "finding-cmd-001")

    def test_nonzero_exit_records_failure_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = discover_from_command(
                root=root,
                source_id="broken",
                command="python3 -c 'import sys; sys.exit(2)'",
                repo_id="demo",
                enabled_repos={"demo": True},
            )

            self.assertEqual(result.findings, [])
            self.assertEqual(len(result.failures), 1)
            self.assertIn("exit code 2", result.failures[0])

            logged = load_discovery_failures(root)
            self.assertEqual(len(logged), 1)
            self.assertEqual(logged[0]["source"], "broken")

    def test_bad_json_records_failure_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = discover_from_command(
                root=root,
                source_id="malformed",
                command="python3 -c 'print(\"not-json\")'",
                repo_id="demo",
                enabled_repos={"demo": True},
            )

            self.assertEqual(result.findings, [])
            self.assertEqual(len(result.failures), 1)
            self.assertIn("invalid JSON", result.failures[0])

    def test_respects_repo_allowlist(self) -> None:
        script = (
            "import json; print(json.dumps({'id':'x','repo':'demo','source':'cmd',"
            "'title':'t','body':'b','severity':'info','evidence':'','discovered_at':'2026-06-19T12:00:00Z'}))"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            enabled = discover_from_command(
                root=root,
                source_id="cmd",
                command=f"python3 -c {json.dumps(script)}",
                repo_id="demo",
                enabled_repos={"demo": True},
            )
            disabled = discover_from_command(
                root=root,
                source_id="cmd",
                command=f"python3 -c {json.dumps(script)}",
                repo_id="demo",
                enabled_repos={"demo": False},
            )

            self.assertEqual(len(enabled.findings), 1)
            self.assertEqual(disabled.findings, [])
            self.assertEqual(disabled.failures, [])

    def test_run_configured_discovery_executes_command_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script_path = root / "emit_finding.py"
            script_path.write_text(textwrap.dedent("""
                import json

                print(json.dumps({
                    "id": "finding-configured-001",
                    "repo": "demo",
                    "source": "ci_scan",
                    "title": "Configured discovery",
                    "body": "from config",
                    "severity": "info",
                    "evidence": "configured=true",
                    "discovered_at": "2026-06-19T12:00:00Z",
                }))
            """).strip(), encoding="utf-8")
            config = CoordinatorConfig(
                agents={},
                repos={
                    "demo": RepoConfig(
                        id="demo",
                        path=root,
                        default_branch="main",
                        remote="origin",
                        branch_prefix="coord/",
                        allow_push=False,
                        merge_policy="no_push",
                        verify_commands=[],
                        review_policy="tests_only",
                    )
                },
                policy=PolicyConfig(
                    require_single_repo=True,
                    require_acceptance_criteria=True,
                    require_verification_commands=True,
                    require_handoff_summary=False,
                    max_files_touched=3,
                    max_expected_minutes=30,
                    max_attempts=3,
                    split_if_touches_multiple_subsystems=True,
                    split_if_research_and_code_are_mixed=True,
                ),
                discovery_sources={
                    "ci_scan": DiscoverySourceConfig(
                        id="ci_scan",
                        type="command",
                        repos={"demo": True},
                        command=f"python3 {script_path}",
                    ),
                },
            )

            result = run_configured_discovery(config, root)

            self.assertEqual(result.discovered, 1)
            self.assertEqual(result.failures, 0)
            self.assertEqual(len(list_findings(root)), 1)