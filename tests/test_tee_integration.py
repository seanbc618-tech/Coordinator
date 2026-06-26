"""Tests for Reporter and stage integration across pipeline boundaries."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_cli_coordinator.agent import run_agent
from local_cli_coordinator.config import AgentConfig
from local_cli_coordinator.connectors import run_connector
from local_cli_coordinator.discovery import discover_from_command
from local_cli_coordinator.planner import plan_finding_with_agent
from local_cli_coordinator.reporting import ExecutionContext, ExecutionEvent, Reporter
from local_cli_coordinator.review import run_quality_review, run_spec_review
from local_cli_coordinator.verify import run_verification


class RecordingReporter:
    """Captures emitted events for assertion."""

    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    def emit(self, event: ExecutionEvent) -> None:
        self.events.append(event)


def _agent(command: str, *, role: str = "worker") -> AgentConfig:
    return AgentConfig(
        id=role,
        command=command,
        capabilities=["code"],
        max_concurrency=1,
        role=role,
    )


class AgentReporterTests(unittest.TestCase):
    def test_run_agent_forwards_reporter_with_worker_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run"
            worktree.mkdir()
            prompt = root / "prompt.md"
            prompt.write_text("work")
            reporter = RecordingReporter()

            run_agent(
                _agent(f"{sys.executable} -c \"print('hello')\""),
                prompt,
                worktree,
                run_dir,
                reporter=reporter,
                task_id="task-1",
            )

            stages = {e.stage for e in reporter.events}
            self.assertIn("worker", stages)
            started = [e for e in reporter.events if e.kind == "started"]
            self.assertEqual(len(started), 1)
            self.assertEqual(started[0].task_id, "task-1")

    def test_run_agent_output_appear_in_log_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run"
            worktree.mkdir()
            prompt = root / "prompt.md"
            prompt.write_text("work")
            script = root / "worker.py"
            script.write_text("print('agent-output-line')\n")

            run_agent(
                _agent(f"{sys.executable} {script}"),
                prompt,
                worktree,
                run_dir,
            )

            log_text = (run_dir / "agent.log").read_text()
            self.assertEqual(log_text.count("agent-output-line"), 1)


class VerificationReporterTests(unittest.TestCase):
    def test_run_verification_forwards_reporter_with_verify_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            worktree.mkdir()
            reporter = RecordingReporter()

            run_verification(
                [f"{sys.executable} -c \"print('ok')\""],
                worktree,
                root / "run",
                reporter=reporter,
                task_id="task-2",
            )

            stages = {e.stage for e in reporter.events}
            self.assertIn("verify", stages)
            started = [e for e in reporter.events if e.kind == "started"]
            self.assertTrue(all(e.task_id == "task-2" for e in started))

    def test_run_verification_output_in_log_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            worktree.mkdir()
            script = root / "verify.py"
            script.write_text("print('verify-output-line')\n")

            run_verification(
                [f"{sys.executable} {script}"],
                worktree,
                root / "run",
            )

            log_text = (root / "run" / "verifier.log").read_text()
            self.assertEqual(log_text.count("verify-output-line"), 1)


class SpecReviewReporterTests(unittest.TestCase):
    def test_run_spec_review_forwards_reporter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run"
            worktree.mkdir()
            run_dir.mkdir()
            diff_path = root / "diff.patch"
            diff_path.write_text("diff")
            task = {"title": "T", "repo": "demo", "goal": "G", "acceptance_criteria": "A"}
            reporter = RecordingReporter()

            run_spec_review(
                _agent(f"{sys.executable} -c \"raise SystemExit(0)\"", role="spec_reviewer"),
                task,
                ["f.py"],
                diff_path,
                worktree,
                run_dir,
                reporter=reporter,
                task_id="task-3",
            )

            stages = {e.stage for e in reporter.events}
            self.assertIn("worker", stages)


class QualityReviewReporterTests(unittest.TestCase):
    def test_run_quality_review_forwards_reporter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run"
            worktree.mkdir()
            run_dir.mkdir()
            diff_path = root / "diff.patch"
            diff_path.write_text("diff")
            verifier_log = root / "verifier.log"
            verifier_log.write_text("passed")
            task = {"title": "T", "repo": "demo", "goal": "G", "acceptance_criteria": "A"}
            from local_cli_coordinator.config import RepoConfig
            repo = RepoConfig(
                id="demo",
                path=root,
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=False,
                merge_policy="no_push",
                verify_commands=[],
            )
            reporter = RecordingReporter()

            run_quality_review(
                _agent(f"{sys.executable} -c \"raise SystemExit(0)\"", role="quality_reviewer"),
                task,
                ["f.py"],
                diff_path,
                verifier_log,
                repo,
                worktree,
                run_dir,
                reporter=reporter,
                task_id="task-4",
            )

            stages = {e.stage for e in reporter.events}
            self.assertIn("worker", stages)


class DiscoveryReporterTests(unittest.TestCase):
    def test_discover_from_command_uses_run_command_with_discovery_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reporter = RecordingReporter()

            discover_from_command(
                root=root,
                source_id="ci_scan",
                command=f"{sys.executable} -c \"print('scan')\"",
                repo_id="demo",
                enabled_repos={"demo": True},
                reporter=reporter,
            )

            stages = {e.stage for e in reporter.events}
            self.assertIn("discovery", stages)
            started = [e for e in reporter.events if e.kind == "started"]
            self.assertTrue(len(started) >= 1)
            self.assertEqual(started[0].actor, "ci_scan")


class ConnectorReporterTests(unittest.TestCase):
    def test_run_connector_uses_run_command_when_no_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "connector.py"
            script.write_text("print('{\"status\": \"ok\"}')\n")
            reporter = RecordingReporter()

            result = run_connector(
                root=root,
                connector_id="test-connector",
                command=f"{sys.executable} {script}",
                reporter=reporter,
            )

            self.assertIsNotNone(result.output)
            self.assertEqual(result.output["status"], "ok")
            stages = {e.stage for e in reporter.events}
            self.assertIn("discovery", stages)


class PlannerReporterTests(unittest.TestCase):
    def test_plan_finding_with_agent_forwards_reporter(self) -> None:
        from local_cli_coordinator.config import CoordinatorConfig, PolicyConfig, RepoConfig
        from local_cli_coordinator.models import Finding

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "planner.py"
            script.write_text(
                "import sys\n"
                "print('{\"title\":\"t\",\"repo\":\"demo\",\"priority\":\"normal\","
                "\"capabilities\":[\"code\"],\"goal\":\"g\","
                "\"acceptance_criteria\":[\"c\"]}')\n"
            )
            config = CoordinatorConfig(
                agents={
                    "planner": _agent(f"{sys.executable} {script}", role="planner"),
                },
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
            )
            finding = Finding(
                id="f1",
                repo="demo",
                source="test",
                title="Fix bug",
                body="- fix the bug",
                severity="high",
                evidence="",
                discovered_at="2026-06-20T00:00:00Z",
            )
            reporter = RecordingReporter()

            result = plan_finding_with_agent(finding, config, reporter=reporter)

            self.assertEqual(len(result.tasks), 1)
            stages = {e.stage for e in reporter.events}
            self.assertIn("planner", stages)


if __name__ == "__main__":
    unittest.main()
