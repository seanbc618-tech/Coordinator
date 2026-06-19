from __future__ import annotations

import json
from dataclasses import dataclass

COMMANDER_SCHEMA_VERSION = 1
COMMANDER_GOAL_STATUSES = frozenset({"active", "blocked", "completed"})
MAX_COMMANDER_TASKS = 3
MAX_ACCEPTANCE_CRITERIA = 5

_RESPONSE_FIELDS = frozenset({
    "schema_version",
    "goal_status",
    "progress_summary",
    "tasks",
    "stop_reason",
})

_TASK_FIELDS = frozenset({
    "title",
    "repo",
    "capabilities",
    "goal",
    "acceptance_criteria",
    "verification_commands",
    "expected_files",
    "expected_minutes",
    "parent_task_id",
    "rationale",
})


@dataclass(frozen=True)
class CommanderTaskProposal:
    title: str
    repo: str
    capabilities: list[str]
    goal: str
    acceptance_criteria: list[str]
    verification_commands: list[str]
    expected_files: int
    expected_minutes: int
    parent_task_id: str | None
    rationale: str


@dataclass(frozen=True)
class CommanderResponse:
    schema_version: int
    goal_status: str
    progress_summary: str
    tasks: list[CommanderTaskProposal]
    stop_reason: str | None


def commander_response_schema() -> dict:
    """Return a JSON Schema for Codex CLI ``--output-schema``."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(_RESPONSE_FIELDS),
        "properties": {
            "schema_version": {"type": "integer", "const": COMMANDER_SCHEMA_VERSION},
            "goal_status": {
                "type": "string",
                "enum": sorted(COMMANDER_GOAL_STATUSES),
            },
            "progress_summary": {"type": "string", "minLength": 1},
            "stop_reason": {"type": ["string", "null"]},
            "tasks": {
                "type": "array",
                "maxItems": MAX_COMMANDER_TASKS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(_TASK_FIELDS),
                    "properties": {
                        "title": {"type": "string", "minLength": 1},
                        "repo": {"type": "string", "minLength": 1},
                        "capabilities": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                        },
                        "goal": {"type": "string", "minLength": 1},
                        "acceptance_criteria": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                            "maxItems": MAX_ACCEPTANCE_CRITERIA,
                        },
                        "verification_commands": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "expected_files": {"type": "integer", "minimum": 0},
                        "expected_minutes": {"type": "integer", "minimum": 0},
                        "parent_task_id": {"type": ["string", "null"]},
                        "rationale": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    }


def _require_mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_non_blank_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value.strip()


def _require_string_list(value: object, field: str, *, allow_blank_items: bool) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{field}[{index}] must be a string")
        if not allow_blank_items and not item.strip():
            raise ValueError(f"{field}[{index}] must be a non-blank string")
        items.append(item.strip() if not allow_blank_items else item)
    return items


def _require_non_negative_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def _parse_task(raw: object, index: int) -> CommanderTaskProposal:
    task = _require_mapping(raw, f"tasks[{index}]")
    unknown = set(task) - _TASK_FIELDS
    if unknown:
        raise ValueError(
            f"tasks[{index}] has unknown fields: {', '.join(sorted(unknown))}"
        )
    missing = _TASK_FIELDS - set(task)
    if missing:
        raise ValueError(
            f"tasks[{index}] missing required fields: {', '.join(sorted(missing))}"
        )

    parent_task_id = task["parent_task_id"]
    if parent_task_id is not None and (
        not isinstance(parent_task_id, str) or not parent_task_id.strip()
    ):
        raise ValueError(f"tasks[{index}].parent_task_id must be a string or null")

    acceptance_criteria = _require_string_list(
        task["acceptance_criteria"],
        f"tasks[{index}].acceptance_criteria",
        allow_blank_items=False,
    )
    if len(acceptance_criteria) > MAX_ACCEPTANCE_CRITERIA:
        raise ValueError(
            f"tasks[{index}] has at most {MAX_ACCEPTANCE_CRITERIA} acceptance criteria"
        )

    return CommanderTaskProposal(
        title=_require_non_blank_string(task["title"], f"tasks[{index}].title"),
        repo=_require_non_blank_string(task["repo"], f"tasks[{index}].repo"),
        capabilities=_require_string_list(
            task["capabilities"],
            f"tasks[{index}].capabilities",
            allow_blank_items=False,
        ),
        goal=_require_non_blank_string(task["goal"], f"tasks[{index}].goal"),
        acceptance_criteria=acceptance_criteria,
        verification_commands=_require_string_list(
            task["verification_commands"],
            f"tasks[{index}].verification_commands",
            allow_blank_items=True,
        ),
        expected_files=_require_non_negative_int(
            task["expected_files"],
            f"tasks[{index}].expected_files",
        ),
        expected_minutes=_require_non_negative_int(
            task["expected_minutes"],
            f"tasks[{index}].expected_minutes",
        ),
        parent_task_id=parent_task_id,
        rationale=_require_non_blank_string(
            task["rationale"],
            f"tasks[{index}].rationale",
        ),
    )


def parse_commander_response(raw: str) -> CommanderResponse:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON in commander response") from exc

    data = _require_mapping(payload, "commander response")
    unknown = set(data) - _RESPONSE_FIELDS
    if unknown:
        raise ValueError(
            f"commander response has unknown fields: {', '.join(sorted(unknown))}"
        )
    missing = _RESPONSE_FIELDS - set(data)
    if missing:
        raise ValueError(
            f"commander response missing required fields: {', '.join(sorted(missing))}"
        )

    schema_version = data["schema_version"]
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise ValueError("schema_version must be an integer")
    if schema_version != COMMANDER_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {schema_version!r}; "
            f"expected {COMMANDER_SCHEMA_VERSION}"
        )

    goal_status = data["goal_status"]
    if not isinstance(goal_status, str) or goal_status not in COMMANDER_GOAL_STATUSES:
        raise ValueError(
            f"unsupported goal_status {goal_status!r}; "
            f"expected one of {sorted(COMMANDER_GOAL_STATUSES)}"
        )

    progress_summary = _require_non_blank_string(
        data["progress_summary"],
        "progress_summary",
    )

    stop_reason = data["stop_reason"]
    if stop_reason is not None and (
        not isinstance(stop_reason, str) or not stop_reason.strip()
    ):
        raise ValueError("stop_reason must be a non-blank string or null")
    if goal_status == "completed" and stop_reason is None:
        raise ValueError("completed responses require stop_reason")

    tasks_raw = data["tasks"]
    if not isinstance(tasks_raw, list):
        raise ValueError("tasks must be a list")
    if len(tasks_raw) > MAX_COMMANDER_TASKS:
        raise ValueError(f"commander response has at most {MAX_COMMANDER_TASKS} tasks")

    tasks = [_parse_task(task, index) for index, task in enumerate(tasks_raw)]

    return CommanderResponse(
        schema_version=schema_version,
        goal_status=goal_status,
        progress_summary=progress_summary,
        tasks=tasks,
        stop_reason=stop_reason.strip() if isinstance(stop_reason, str) else None,
    )