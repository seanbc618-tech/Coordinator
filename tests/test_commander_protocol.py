import json
import unittest

from local_cli_coordinator.commander_protocol import (
    COMMANDER_SCHEMA_VERSION,
    CommanderResponse,
    CommanderTaskProposal,
    commander_response_schema,
    parse_commander_response,
)
from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    PolicyConfig,
    select_agent_by_role,
)


def _valid_task(**overrides) -> dict:
    base = {
        "title": "Add helper",
        "repo": "demo",
        "capabilities": ["code"],
        "goal": "Ship helper module",
        "acceptance_criteria": ["Helper exists"],
        "verification_commands": [],
        "expected_files": 2,
        "expected_minutes": 20,
        "parent_task_id": None,
        "rationale": "Unblocks next slice",
    }
    base.update(overrides)
    return base


def _valid_response(**overrides) -> dict:
    base = {
        "schema_version": COMMANDER_SCHEMA_VERSION,
        "goal_status": "active",
        "progress_summary": "Ready",
        "tasks": [_valid_task()],
        "stop_reason": None,
    }
    base.update(overrides)
    return base


class CommanderProtocolTests(unittest.TestCase):
    def test_parse_valid_response(self) -> None:
        raw = json.dumps(_valid_response())
        response = parse_commander_response(raw)

        self.assertIsInstance(response, CommanderResponse)
        self.assertEqual(response.schema_version, 1)
        self.assertEqual(response.goal_status, "active")
        self.assertEqual(response.progress_summary, "Ready")
        self.assertEqual(len(response.tasks), 1)
        self.assertIsNone(response.stop_reason)

        task = response.tasks[0]
        self.assertIsInstance(task, CommanderTaskProposal)
        self.assertEqual(task.title, "Add helper")
        self.assertEqual(task.repo, "demo")
        self.assertEqual(task.capabilities, ["code"])
        self.assertEqual(task.goal, "Ship helper module")
        self.assertEqual(task.acceptance_criteria, ["Helper exists"])
        self.assertEqual(task.verification_commands, [])
        self.assertEqual(task.expected_files, 2)
        self.assertEqual(task.expected_minutes, 20)
        self.assertIsNone(task.parent_task_id)
        self.assertEqual(task.rationale, "Unblocks next slice")

    def test_unknown_response_fields_are_rejected(self) -> None:
        raw = json.dumps(
            _valid_response(
                tasks=[],
                surprise=True,
            )
        )
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            parse_commander_response(raw)

    def test_unknown_task_fields_are_rejected(self) -> None:
        raw = json.dumps(
            _valid_response(
                tasks=[_valid_task(extra=True)],
            )
        )
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            parse_commander_response(raw)

    def test_missing_top_level_keys_are_rejected(self) -> None:
        payload = _valid_response()
        del payload["progress_summary"]
        with self.assertRaisesRegex(ValueError, "missing required fields"):
            parse_commander_response(json.dumps(payload))

    def test_missing_task_keys_are_rejected(self) -> None:
        task = _valid_task()
        del task["goal"]
        raw = json.dumps(_valid_response(tasks=[task]))
        with self.assertRaisesRegex(ValueError, "missing required fields"):
            parse_commander_response(raw)

    def test_blank_required_strings_are_rejected(self) -> None:
        raw = json.dumps(_valid_response(progress_summary="   "))
        with self.assertRaisesRegex(ValueError, "blank string"):
            parse_commander_response(raw)

    def test_unsupported_schema_version_is_rejected(self) -> None:
        raw = json.dumps(_valid_response(schema_version=2))
        with self.assertRaisesRegex(ValueError, "unsupported schema_version"):
            parse_commander_response(raw)

    def test_unsupported_goal_status_is_rejected(self) -> None:
        raw = json.dumps(_valid_response(goal_status="paused"))
        with self.assertRaisesRegex(ValueError, "unsupported goal_status"):
            parse_commander_response(raw)

    def test_negative_estimates_are_rejected(self) -> None:
        raw = json.dumps(
            _valid_response(tasks=[_valid_task(expected_files=-1)])
        )
        with self.assertRaisesRegex(ValueError, "expected_files"):
            parse_commander_response(raw)

        raw = json.dumps(
            _valid_response(tasks=[_valid_task(expected_minutes=-5)])
        )
        with self.assertRaisesRegex(ValueError, "expected_minutes"):
            parse_commander_response(raw)

    def test_more_than_three_tasks_are_rejected(self) -> None:
        raw = json.dumps(
            _valid_response(
                tasks=[_valid_task(title=f"Task {index}") for index in range(4)],
            )
        )
        with self.assertRaisesRegex(ValueError, "at most 3 tasks"):
            parse_commander_response(raw)

    def test_more_than_five_criteria_are_rejected(self) -> None:
        raw = json.dumps(
            _valid_response(
                tasks=[
                    _valid_task(
                        acceptance_criteria=[f"criterion {index}" for index in range(6)]
                    )
                ],
            )
        )
        with self.assertRaisesRegex(ValueError, "at most 5 acceptance criteria"):
            parse_commander_response(raw)

    def test_completed_without_stop_reason_is_rejected(self) -> None:
        raw = json.dumps(
            _valid_response(
                goal_status="completed",
                tasks=[],
                stop_reason=None,
            )
        )
        with self.assertRaisesRegex(ValueError, "stop_reason"):
            parse_commander_response(raw)

    def test_completed_with_stop_reason_is_accepted(self) -> None:
        raw = json.dumps(
            _valid_response(
                goal_status="completed",
                tasks=[],
                stop_reason="All criteria satisfied",
            )
        )
        response = parse_commander_response(raw)
        self.assertEqual(response.goal_status, "completed")
        self.assertEqual(response.stop_reason, "All criteria satisfied")

    def test_invalid_json_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            parse_commander_response("not json")

    def test_commander_response_schema_matches_contract(self) -> None:
        schema = commander_response_schema()
        self.assertEqual(schema["type"], "object")
        self.assertIn("schema_version", schema["properties"])
        self.assertIn("tasks", schema["properties"])
        self.assertTrue(schema.get("additionalProperties") is False)


class CommanderRoleConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = CoordinatorConfig(
            agents={
                "codex": AgentConfig(
                    id="codex",
                    command="codex exec {prompt_path}",
                    capabilities=["code", "tests", "docs", "research"],
                    max_concurrency=1,
                    role="commander",
                ),
                "worker": AgentConfig(
                    id="worker",
                    command="worker {prompt_path}",
                    capabilities=["code"],
                    max_concurrency=1,
                    role="worker",
                ),
            },
            repos={},
            policy=PolicyConfig(
                require_single_repo=True,
                require_acceptance_criteria=True,
                require_verification_commands=True,
                require_handoff_summary=True,
                max_files_touched=3,
                max_expected_minutes=30,
                max_attempts=3,
                split_if_touches_multiple_subsystems=True,
                split_if_research_and_code_are_mixed=True,
            ),
        )

    def test_commander_role_is_selectable(self) -> None:
        self.assertEqual(
            select_agent_by_role(self.config, "commander").id,
            "codex",
        )