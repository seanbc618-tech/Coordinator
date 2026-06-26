import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.models import Finding
from local_cli_coordinator.planner import (
    PlanResult,
    _parse_agent_output,
    _validate_draft,
    plan_finding_with_agent,
)
from local_cli_coordinator.models import TaskDraft


def _finding(**overrides) -> Finding:
    defaults = dict(
        id="find-001",
        repo="demo",
        source="git_recent_commits",
        title="Fix login timeout",
        body="- Increase timeout to 30s\n- Add retry with backoff",
        severity="info",
        evidence="commit abc123",
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _config_with_planner() -> CoordinatorConfig:
    return CoordinatorConfig(
        agents={
            "planner-agent": AgentConfig(
                id="planner-agent",
                command="echo",
                capabilities=["code"],
                max_concurrency=1,
                role="planner",
            ),
        },
        repos={},
        policy=PolicyConfig(
            require_single_repo=True,
            require_acceptance_criteria=True,
            require_verification_commands=True,
            require_handoff_summary=True,
            max_files_touched=5,
            max_expected_minutes=30,
            max_attempts=3,
            split_if_touches_multiple_subsystems=True,
            split_if_research_and_code_are_mixed=True,
        ),
    )


def _config_without_planner() -> CoordinatorConfig:
    return CoordinatorConfig(
        agents={},
        repos={},
        policy=PolicyConfig(
            require_single_repo=True,
            require_acceptance_criteria=True,
            require_verification_commands=True,
            require_handoff_summary=True,
            max_files_touched=5,
            max_expected_minutes=30,
            max_attempts=3,
            split_if_touches_multiple_subsystems=True,
            split_if_research_and_code_are_mixed=True,
        ),
    )


class ParseAgentOutputTests(unittest.TestCase):
    def test_parses_valid_jsonl_tasks(self) -> None:
        output = json.dumps({
            "title": "Fix timeout",
            "repo": "demo",
            "priority": "normal",
            "capabilities": ["code"],
            "goal": "Increase timeout",
            "acceptance_criteria": ["Timeout is 30s"],
        })
        result = _parse_agent_output(output)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], TaskDraft)
        self.assertEqual(result[0].title, "Fix timeout")

    def test_parses_multiple_tasks(self) -> None:
        lines = [
            json.dumps({
                "title": "Task A",
                "repo": "demo",
                "acceptance_criteria": ["A done"],
            }),
            json.dumps({
                "title": "Task B",
                "repo": "demo",
                "acceptance_criteria": ["B done"],
            }),
        ]
        result = _parse_agent_output("\n".join(lines))

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].title, "Task A")
        self.assertEqual(result[1].title, "Task B")

    def test_returns_split_reasons(self) -> None:
        output = json.dumps({"needs_split": "Too broad, break it down."})
        result = _parse_agent_output(output)

        self.assertIsInstance(result, list)
        self.assertEqual(result, ["Too broad, break it down."])

    def test_skips_non_json_lines(self) -> None:
        output = "some preamble\n" + json.dumps({
            "title": "T",
            "repo": "r",
            "acceptance_criteria": ["C"],
        }) + "\ntrailing text"
        result = _parse_agent_output(output)

        self.assertEqual(len(result), 1)

    def test_empty_output_returns_empty_list(self) -> None:
        self.assertEqual(_parse_agent_output(""), [])


class ValidateDraftTests(unittest.TestCase):
    def test_valid_draft_passes(self) -> None:
        draft = TaskDraft(
            title="Fix timeout",
            repo="demo",
            priority="normal",
            capabilities=["code"],
            goal="Increase timeout",
            acceptance_criteria=["Timeout is 30s"],
        )
        reasons = _validate_draft(draft, "find-001")
        self.assertEqual(reasons, [])

    def test_broad_title_rejected(self) -> None:
        draft = TaskDraft(
            title="Refactor everything",
            repo="demo",
            priority="normal",
            capabilities=["code"],
            goal="Fix stuff",
            acceptance_criteria=["Done"],
        )
        reasons = _validate_draft(draft, "find-001")
        self.assertTrue(len(reasons) > 0)
        self.assertIn("broad", reasons[0].lower())

    def test_too_many_criteria_rejected(self) -> None:
        draft = TaskDraft(
            title="Fix timeout value",
            repo="demo",
            priority="normal",
            capabilities=["code"],
            goal="Do things",
            acceptance_criteria=[f"Criterion {i}" for i in range(10)],
        )
        reasons = _validate_draft(draft, "find-001")
        self.assertTrue(len(reasons) > 0)
        self.assertIn("criteria", reasons[0].lower())

    def test_no_criteria_rejected(self) -> None:
        draft = TaskDraft(
            title="Fix timeout value",
            repo="demo",
            priority="normal",
            capabilities=["code"],
            goal="Do things",
            acceptance_criteria=[],
        )
        reasons = _validate_draft(draft, "find-001")
        self.assertTrue(len(reasons) > 0)
        self.assertIn("no acceptance", reasons[0].lower())


class PlanFindingWithAgentTests(unittest.TestCase):
    def test_falls_back_to_rule_based_when_no_planner(self) -> None:
        config = _config_without_planner()
        finding = _finding()
        result = plan_finding_with_agent(finding, config)

        self.assertEqual(len(result.needs_split), 0)
        self.assertEqual(len(result.tasks), 1)
        self.assertEqual(result.tasks[0].title, "Fix login timeout")

    @patch("local_cli_coordinator.planner.run_command")
    def test_uses_planner_agent_output(self, mock_run) -> None:
        agent_output = json.dumps({
            "title": "Increase login timeout",
            "repo": "demo",
            "priority": "high",
            "capabilities": ["code"],
            "goal": "Increase the timeout to 30 seconds",
            "acceptance_criteria": [
                "Timeout is set to 30s",
                "Retry with backoff is implemented",
            ],
        })
        mock_run.return_value = type("R", (), {
            "returncode": 0,
            "stdout": agent_output,
            "stderr": "",
            "timed_out": False,
        })()

        config = _config_with_planner()
        finding = _finding()
        result = plan_finding_with_agent(finding, config, timeout_seconds=10)

        self.assertEqual(len(result.tasks), 1)
        self.assertEqual(result.tasks[0].title, "Increase login timeout")
        self.assertEqual(result.tasks[0].priority, "high")
        self.assertEqual(len(result.tasks[0].acceptance_criteria), 2)
        self.assertTrue(mock_run.called)

    @patch("local_cli_coordinator.planner.run_command")
    def test_agent_broad_output_is_rejected_by_guard(self, mock_run) -> None:
        agent_output = json.dumps({
            "title": "Refactor entire auth system",
            "repo": "demo",
            "acceptance_criteria": ["Done"],
        })
        mock_run.return_value = type("R", (), {
            "returncode": 0,
            "stdout": agent_output,
            "stderr": "",
            "timed_out": False,
        })()

        config = _config_with_planner()
        finding = _finding()
        result = plan_finding_with_agent(finding, config)

        self.assertEqual(len(result.tasks), 0)
        self.assertTrue(len(result.needs_split) > 0)

    @patch("local_cli_coordinator.planner.run_command")
    def test_agent_failure_falls_back_to_rule_based(self, mock_run) -> None:
        mock_run.return_value = type("R", (), {
            "returncode": 1,
            "stdout": "",
            "stderr": "error",
            "timed_out": False,
        })()

        config = _config_with_planner()
        finding = _finding()
        result = plan_finding_with_agent(finding, config)

        self.assertEqual(len(result.tasks), 1)
        self.assertEqual(result.tasks[0].title, "Fix login timeout")

    @patch("local_cli_coordinator.planner.run_command")
    def test_agent_split_reasons_are_passed_through(self, mock_run) -> None:
        agent_output = json.dumps({
            "needs_split": "This finding covers too many subsystems.",
        })
        mock_run.return_value = type("R", (), {
            "returncode": 0,
            "stdout": agent_output,
            "stderr": "",
            "timed_out": False,
        })()

        config = _config_with_planner()
        finding = _finding()
        result = plan_finding_with_agent(finding, config)

        self.assertEqual(len(result.tasks), 0)
        self.assertIn("subsystems", result.needs_split[0])

    @patch("local_cli_coordinator.planner.run_command")
    def test_agent_output_includes_source_path(self, mock_run) -> None:
        agent_output = json.dumps({
            "title": "Fix it",
            "repo": "demo",
            "acceptance_criteria": ["Fixed"],
        })
        mock_run.return_value = type("R", (), {
            "returncode": 0,
            "stdout": agent_output,
            "stderr": "",
            "timed_out": False,
        })()

        config = _config_with_planner()
        finding = _finding()
        result = plan_finding_with_agent(finding, config)

        self.assertEqual(
            result.tasks[0].source_path, "state/findings/find-001.jsonl"
        )
