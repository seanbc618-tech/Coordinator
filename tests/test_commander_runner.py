"""Tests for the read-only Commander runner."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
    RepoConfig,
)
import sys as _sys

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal, get_goal
from local_cli_coordinator.commander_runner import (
    CommanderResponse,
    CommanderTaskProposal,
    build_commander_context,
    parse_commander_response,
    run_commander,
    _render_command_tokens,
)
from local_cli_coordinator.reporting import ExecutionEvent

_PYTHON = _sys.executable


class RecordingReporter:
    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    def emit(self, event: ExecutionEvent) -> None:
        self.events.append(event)


def _write_fixture_script(tmp_dir: Path) -> Path:
    """Write a Python script that outputs a valid Commander response."""
    script = tmp_dir / "fixture_commander.py"
    script.write_text('''
import json
response = {
    "schema_version": 1,
    "goal_status": "active",
    "progress_summary": "Ready",
    "tasks": [
        {
            "title": "Add feature file",
            "repo": "demo",
            "capabilities": ["code"],
            "goal": "Create feature.txt",
            "acceptance_criteria": ["feature.txt exists"],
            "verification_commands": [],
            "expected_files": 1,
            "expected_minutes": 10,
            "parent_task_id": None,
            "rationale": "First step toward goal"
        }
    ],
    "stop_reason": None
}
print(json.dumps(response))
''')
    return script


def _test_config(tmp_dir: Path, command: str | None = None, repo_path: Path | None = None) -> CoordinatorConfig:
    if command is None:
        script = _write_fixture_script(tmp_dir)
        command = f"{_PYTHON} {script}"
    if repo_path is None:
        repo_path = tmp_dir / "repo"
        repo_path.mkdir(exist_ok=True)
    return CoordinatorConfig(
        agents={
            "codex_commander": AgentConfig(
                id="codex_commander",
                command=command,
                capabilities=["code", "tests", "docs", "research"],
                max_concurrency=1,
                role="commander",
            ),
            "worker": AgentConfig(
                id="worker",
                command="echo done",
                capabilities=["code", "tests", "docs", "research"],
                max_concurrency=1,
                role="worker",
            ),
        },
        repos={
            "demo": RepoConfig(
                id="demo",
                path=repo_path,
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=False,
                merge_policy="no_push",
                verify_commands=["python -m pytest"],
            ),
        },
        policy=PolicyConfig(
            require_single_repo=True,
            require_acceptance_criteria=True,
            require_verification_commands=False,
            require_handoff_summary=False,
            max_files_touched=3,
            max_expected_minutes=30,
            max_attempts=3,
            split_if_touches_multiple_subsystems=False,
            split_if_research_and_code_are_mixed=False,
        ),
        daemon_policy=DaemonPolicyConfig(),
    )


def _worker_only_config() -> CoordinatorConfig:
    """Config with no commander agent."""
    return CoordinatorConfig(
        agents={
            "echo": AgentConfig(
                id="echo",
                command="echo done",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
        },
        repos={},
        policy=PolicyConfig(
            require_single_repo=True,
            require_acceptance_criteria=True,
            require_verification_commands=False,
            require_handoff_summary=False,
            max_files_touched=3,
            max_expected_minutes=30,
            max_attempts=3,
            split_if_touches_multiple_subsystems=False,
            split_if_research_and_code_are_mixed=False,
        ),
        daemon_policy=DaemonPolicyConfig(),
    )


class CommanderRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.conn = connect(self.root / "coordinator.db")
        init_db(self.conn)
        self.goal_id = create_goal(
            self.conn, "Roadmap", "Finish roadmap",
            completion_criteria=["all features done"],
            constraints=["dry-run"],
            repo_ids=["demo"],
        )
        self.config = _test_config(self.root, repo_path=self.repo)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_runner_renders_paths_and_persists_artifacts(self) -> None:
        result = run_commander(
            self.conn, self.config, self.root,
            self.goal_id, "initial_plan", 30,
        )
        self.assertTrue(result.succeeded)
        self.assertIsNotNone(result.response)
        self.assertEqual(result.response.progress_summary, "Ready")
        self.assertIn("Finish roadmap", result.prompt_path.read_text())
        self.assertTrue(result.raw_output_path.exists())
        self.assertIsNotNone(result.parsed_output_path)
        self.assertTrue(result.parsed_output_path.exists())

    def test_command_template_paths_are_absolute(self) -> None:
        tokens = _render_command_tokens(
            "commander {prompt_path} {schema_path} {repo_path}",
            Path("runs/prompt.md"),
            Path("runs/schema.json"),
            Path("repo"),
        )
        for token in tokens[1:]:
            self.assertTrue(Path(token).is_absolute(), token)

    def test_runner_refuses_worker_role(self) -> None:
        with self.assertRaisesRegex(ValueError, "commander"):
            run_commander(
                self.conn, _worker_only_config(), self.root,
                self.goal_id, "initial_plan", 30,
            )

    def test_runner_records_run_in_database(self) -> None:
        result = run_commander(
            self.conn, self.config, self.root,
            self.goal_id, "initial_plan", 30,
        )
        self.assertTrue(result.succeeded)
        goal = get_goal(self.conn, self.goal_id)
        # The run should be recorded
        from local_cli_coordinator.goals import get_latest_commander_run
        run = get_latest_commander_run(self.conn, self.goal_id)
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "succeeded")

    def test_runner_handles_timeout(self) -> None:
        # Use a command that sleeps forever
        config = _test_config(self.root, command=f"{_PYTHON} -c 'import time; time.sleep(300)'", repo_path=self.repo)
        result = run_commander(
            self.conn, config, self.root,
            self.goal_id, "initial_plan", 1,
        )
        self.assertFalse(result.succeeded)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.error, "timeout")

    def test_runner_handles_nonzero_exit(self) -> None:
        script = self.root / "failing_commander.py"
        script.write_text(
            "import sys\nprint('unsupported option: --old-flag', file=sys.stderr)\nsys.exit(2)\n"
        )
        config = _test_config(self.root, command=f"{_PYTHON} {script}", repo_path=self.repo)
        result = run_commander(
            self.conn, config, self.root,
            self.goal_id, "initial_plan", 30,
        )
        self.assertFalse(result.succeeded)
        self.assertEqual(result.exit_code, 2)
        self.assertIn("unsupported option: --old-flag", result.error)
        stderr_log = result.raw_output_path.parent / "stderr.log"
        self.assertTrue(stderr_log.exists())
        self.assertIn("unsupported option: --old-flag", stderr_log.read_text())
        self.assertNotIn("[stderr]", result.raw_output_path.read_text())

    def test_runner_forwards_reporter_and_streams_stdout_only_to_raw(self) -> None:
        script = self.root / "streaming_commander.py"
        script.write_text(
            "import json, sys\n"
            "print('progress', file=sys.stderr, flush=True)\n"
            "print(json.dumps({"
            "'schema_version': 1, 'goal_status': 'active', 'progress_summary': 'Ready', "
            "'tasks': [], 'stop_reason': None"
            "}))\n"
            "print('stderr-msg', file=sys.stderr, flush=True)\n"
        )
        config = _test_config(self.root, command=f"{_PYTHON} {script}", repo_path=self.repo)
        reporter = RecordingReporter()

        result = run_commander(
            self.conn,
            config,
            self.root,
            self.goal_id,
            "initial_plan",
            30,
            reporter=reporter,
        )

        self.assertTrue(result.succeeded)
        started = [event for event in reporter.events if event.kind == "started"]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0].stage, "commander")
        self.assertEqual(started[0].actor, "codex_commander")
        self.assertEqual(started[0].cwd.resolve(), self.repo.resolve())
        self.assertIn(_PYTHON, started[0].command)
        self.assertTrue(any(event.kind == "stderr" and "progress" in event.text for event in reporter.events))
        self.assertTrue(any(event.kind == "stderr" and "stderr-msg" in event.text for event in reporter.events))
        self.assertTrue(any(event.kind == "stdout" and "Ready" in event.text for event in reporter.events))
        self.assertTrue(any(event.kind == "completed" for event in reporter.events))
        self.assertTrue(result.raw_output_path.exists())
        raw_text = result.raw_output_path.read_text()
        self.assertIn("progress_summary", raw_text)
        self.assertNotIn("stderr-msg", raw_text)
        self.assertNotIn("progress\n", raw_text)
        self.assertNotIn("[codex_commander:stdout]", raw_text)
        stderr_log = result.raw_output_path.parent / "stderr.log"
        stderr_text = stderr_log.read_text()
        self.assertIn("progress", stderr_text)
        self.assertIn("stderr-msg", stderr_text)
        self.assertTrue(result.raw_output_path.parent.is_dir())

    def test_nonzero_error_keeps_stderr_tail(self) -> None:
        script = self.root / "long_failing_commander.py"
        script.write_text(
            "import sys\nprint('x' * 1500, file=sys.stderr)\n"
            "print('ROOT CAUSE AT END', file=sys.stderr)\nsys.exit(1)\n"
        )
        config = _test_config(self.root, command=f"{_PYTHON} {script}", repo_path=self.repo)
        result = run_commander(
            self.conn, config, self.root,
            self.goal_id, "initial_plan", 30,
        )
        self.assertIn("ROOT CAUSE AT END", result.error)

    def test_runner_handles_invalid_json(self) -> None:
        config = _test_config(self.root, command=f"{_PYTHON} -c \"print('not json')\"", repo_path=self.repo)
        result = run_commander(
            self.conn, config, self.root,
            self.goal_id, "initial_plan", 30,
        )
        self.assertFalse(result.succeeded)
        self.assertIn("parse error", result.error)


class CommanderResponseParsingTests(unittest.TestCase):
    def test_valid_response_parsed(self) -> None:
        raw = json.dumps({
            "schema_version": 1,
            "goal_status": "active",
            "progress_summary": "Ready",
            "tasks": [],
            "stop_reason": None,
        })
        resp = parse_commander_response(raw)
        self.assertEqual(resp.schema_version, 1)
        self.assertEqual(resp.goal_status, "active")
        self.assertEqual(resp.progress_summary, "Ready")
        self.assertEqual(resp.tasks, [])
        self.assertIsNone(resp.stop_reason)

    def test_unknown_response_fields_are_rejected(self) -> None:
        raw = json.dumps({
            "schema_version": 1,
            "goal_status": "active",
            "progress_summary": "Ready",
            "tasks": [],
            "stop_reason": None,
            "surprise": True,
        })
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            parse_commander_response(raw)

    def test_missing_required_field_rejected(self) -> None:
        raw = json.dumps({
            "schema_version": 1,
            "goal_status": "active",
            "tasks": [],
            "stop_reason": None,
        })
        with self.assertRaisesRegex(ValueError, "missing required field"):
            parse_commander_response(raw)

    def test_wrong_schema_version_rejected(self) -> None:
        raw = json.dumps({
            "schema_version": 2,
            "goal_status": "active",
            "progress_summary": "Ready",
            "tasks": [],
            "stop_reason": None,
        })
        with self.assertRaisesRegex(ValueError, "unsupported schema version"):
            parse_commander_response(raw)

    def test_invalid_goal_status_rejected(self) -> None:
        raw = json.dumps({
            "schema_version": 1,
            "goal_status": "invalid",
            "progress_summary": "Ready",
            "tasks": [],
            "stop_reason": None,
        })
        with self.assertRaisesRegex(ValueError, "unsupported goal status"):
            parse_commander_response(raw)

    def test_completed_without_stop_reason_rejected(self) -> None:
        raw = json.dumps({
            "schema_version": 1,
            "goal_status": "completed",
            "progress_summary": "Done",
            "tasks": [],
            "stop_reason": None,
        })
        with self.assertRaisesRegex(ValueError, "completed.*stop_reason"):
            parse_commander_response(raw)

    def test_too_many_tasks_rejected(self) -> None:
        tasks = [
            {
                "title": f"Task {i}",
                "repo": "demo",
                "capabilities": ["code"],
                "goal": f"Goal {i}",
                "acceptance_criteria": ["ok"],
                "verification_commands": [],
                "expected_files": 1,
                "expected_minutes": 10,
                "parent_task_id": None,
                "rationale": "because",
            }
            for i in range(4)
        ]
        raw = json.dumps({
            "schema_version": 1,
            "goal_status": "active",
            "progress_summary": "Ready",
            "tasks": tasks,
            "stop_reason": None,
        })
        with self.assertRaisesRegex(ValueError, "too many tasks"):
            parse_commander_response(raw)

    def test_unknown_task_field_rejected(self) -> None:
        raw = json.dumps({
            "schema_version": 1,
            "goal_status": "active",
            "progress_summary": "Ready",
            "tasks": [{
                "title": "T",
                "repo": "demo",
                "capabilities": ["code"],
                "goal": "G",
                "acceptance_criteria": ["A"],
                "verification_commands": [],
                "expected_files": 1,
                "expected_minutes": 10,
                "parent_task_id": None,
                "rationale": "R",
                "extra": True,
            }],
            "stop_reason": None,
        })
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            parse_commander_response(raw)

    def test_negative_expected_files_rejected(self) -> None:
        raw = json.dumps({
            "schema_version": 1,
            "goal_status": "active",
            "progress_summary": "Ready",
            "tasks": [{
                "title": "T",
                "repo": "demo",
                "capabilities": ["code"],
                "goal": "G",
                "acceptance_criteria": ["A"],
                "verification_commands": [],
                "expected_files": -1,
                "expected_minutes": 10,
                "parent_task_id": None,
                "rationale": "R",
            }],
            "stop_reason": None,
        })
        with self.assertRaisesRegex(ValueError, "non-negative integer"):
            parse_commander_response(raw)

    def test_empty_progress_summary_rejected(self) -> None:
        raw = json.dumps({
            "schema_version": 1,
            "goal_status": "active",
            "progress_summary": "",
            "tasks": [],
            "stop_reason": None,
        })
        with self.assertRaisesRegex(ValueError, "non-empty string"):
            parse_commander_response(raw)


class CommanderContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = connect(self.root / "coordinator.db")
        init_db(self.conn)
        self.goal_id = create_goal(
            self.conn, "Roadmap", "Finish roadmap",
            repo_ids=["demo"],
        )
        self.config = _test_config(self.root)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_context_includes_goal_info(self) -> None:
        context = build_commander_context(
            self.conn, self.config, self.root, self.goal_id,
        )
        self.assertIn("Finish roadmap", context)
        self.assertIn("Roadmap", context)

    def test_context_constrains_proposals_to_worker_capabilities(self) -> None:
        context = build_commander_context(
            self.conn, self.config, self.root, self.goal_id,
        )
        self.assertIn("worker: code, tests, docs, research", context)
        self.assertIn("exact subset", context)

    def test_context_includes_rejected_fingerprints(self) -> None:
        context = build_commander_context(
            self.conn, self.config, self.root, self.goal_id,
            rejected_fingerprints=["abc123", "def456"],
        )
        self.assertIn("abc123", context)
        self.assertIn("def456", context)


if __name__ == "__main__":
    unittest.main()
