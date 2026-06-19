"""Read-only Commander runner.

Invokes the configured commander agent (Codex CLI) with goal context
and validates its structured JSON response.
"""

from __future__ import annotations

import json
import shlex
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .config import CoordinatorConfig, select_agent_by_role
from .goals import (
    acquire_commander_run_slot,
    finish_commander_run,
    get_goal,
    list_commander_messages,
    list_commander_runs,
    list_linked_tasks,
)
from .process import run_command

COMMANDER_SCHEMA_VERSION = 1
COMMANDER_GOAL_STATUSES = frozenset({"active", "blocked", "completed"})
MAX_CONTEXT_CHARS = 20_000
MAX_LINKED_TASKS = 20
MAX_MESSAGES = 5


class CommanderRunActiveError(RuntimeError):
    """Raised when another Commander run is already active for the goal."""


@dataclass(frozen=True)
class CommanderRunResult:
    succeeded: bool
    response: "CommanderResponse | None"
    run_id: int
    prompt_path: Path
    raw_output_path: Path
    parsed_output_path: Path | None
    exit_code: int
    timed_out: bool
    error: str | None


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


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------

def _read_optional_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text or None


def build_commander_context(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    goal_id: int,
    rejected_fingerprints: list[str] | None = None,
) -> str:
    """Build the full context packet for a Commander call."""
    goal = get_goal(conn, goal_id)
    tasks = list_linked_tasks(conn, goal_id)[:MAX_LINKED_TASKS]
    messages = list_commander_messages(conn, goal_id, limit=MAX_MESSAGES)
    runs = list_commander_runs(conn, goal_id)

    sections = [
        f"# Commander Context",
        f"",
        f"## Goal",
        f"- ID: {goal['id']}",
        f"- Title: {goal['title']}",
        f"- Objective: {goal['objective']}",
        f"- Status: {goal['status']}",
        f"- Progress: {goal['progress_summary'] or '(none)'}",
        f"- Completion criteria: {goal['completion_criteria']}",
        f"- Constraints: {goal['constraints']}",
        f"- Repo IDs: {goal['repo_ids']}",
    ]

    if tasks:
        sections.append("")
        sections.append("## Linked tasks")
        for t in tasks:
            rationale = t['rationale'] if 'rationale' in t.keys() else ''
            sections.append(f"- [{t['state']}] {t['title']} ({t['id']}): {rationale}")

    if messages:
        sections.append("")
        sections.append("## Recent messages")
        for m in reversed(messages):
            sections.append(f"- {m['role']}: {m['content'][:500]}")

    if rejected_fingerprints:
        sections.append("")
        sections.append("## Rejected proposal fingerprints")
        for fp in rejected_fingerprints:
            sections.append(f"- {fp}")

    # Add repo info
    sections.append("")
    sections.append("## Available repos")
    for repo_id, repo in config.repos.items():
        sections.append(f"- {repo_id}: {repo.path} (verify: {repo.verify_commands})")

    sections.append("")
    sections.append("## Worker capability contract")
    workers = [agent for agent in config.agents.values() if agent.role == "worker"]
    for worker in workers:
        sections.append(f"- {worker.id}: {', '.join(worker.capabilities)}")
    sections.append(
        "Every proposed task's capabilities must be an exact subset of one worker's "
        "listed capabilities. Use only these exact capability names."
    )

    # Budget info
    sections.append("")
    sections.append("## Budgets")
    sections.append(f"- Max tasks per batch: 3")
    sections.append(f"- Max files per task: {config.policy.max_files_touched}")
    sections.append(f"- Max expected minutes: {config.policy.max_expected_minutes}")

    # Roadmap files
    sections.append("")
    sections.append("## Roadmap context")
    roadmap_chars = 0
    for repo_id, repo in config.repos.items():
        roadmap_path = root / "roadmap.md"
        if roadmap_path.is_file():
            try:
                content = roadmap_path.read_text()
                if roadmap_chars + len(content) > MAX_CONTEXT_CHARS:
                    content = content[: MAX_CONTEXT_CHARS - roadmap_chars]
                sections.append(content)
                roadmap_chars += len(content)
            except OSError:
                pass

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def _render_command_tokens(
    command: str,
    prompt_path: Path,
    schema_path: Path,
    repo_path: Path,
) -> list[str]:
    """Render template tokens in the agent command."""
    tokens = shlex.split(command)
    prompt_path = prompt_path.resolve()
    schema_path = schema_path.resolve()
    repo_path = repo_path.resolve()
    return [
        token
        .replace("{prompt_path}", str(prompt_path))
        .replace("{schema_path}", str(schema_path))
        .replace("{repo_path}", str(repo_path))
        for token in tokens
    ]


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_commander_response(raw: str) -> CommanderResponse:
    """Parse and validate a Commander JSON response.

    Raises ValueError on any schema violation.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("response must be a JSON object")

    # Check for unknown fields
    allowed = {"schema_version", "goal_status", "progress_summary", "tasks", "stop_reason"}
    unknown = set(data.keys()) - allowed
    if unknown:
        raise ValueError(f"unknown fields: {unknown}")

    # Check required fields
    for field in allowed:
        if field not in data:
            raise ValueError(f"missing required field: {field}")

    # Validate schema_version
    if data["schema_version"] != COMMANDER_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema version: {data['schema_version']}, "
            f"expected {COMMANDER_SCHEMA_VERSION}"
        )

    # Validate goal_status
    if data["goal_status"] not in COMMANDER_GOAL_STATUSES:
        raise ValueError(
            f"unsupported goal status: {data['goal_status']!r}, "
            f"expected one of {COMMANDER_GOAL_STATUSES}"
        )

    # Validate progress_summary
    if not isinstance(data["progress_summary"], str) or not data["progress_summary"].strip():
        raise ValueError("progress_summary must be a non-empty string")

    # Validate stop_reason for completed status
    if data["goal_status"] == "completed" and not data.get("stop_reason"):
        raise ValueError("completed status requires a stop_reason")

    # Validate tasks
    tasks_raw = data.get("tasks", [])
    if not isinstance(tasks_raw, list):
        raise ValueError("tasks must be a list")
    if len(tasks_raw) > 3:
        raise ValueError(f"too many tasks: {len(tasks_raw)}, max 3")

    tasks = []
    for i, task_raw in enumerate(tasks_raw):
        tasks.append(_parse_task_proposal(task_raw, i))

    return CommanderResponse(
        schema_version=data["schema_version"],
        goal_status=data["goal_status"],
        progress_summary=data["progress_summary"],
        tasks=tasks,
        stop_reason=data.get("stop_reason"),
    )


def _parse_task_proposal(raw: dict, index: int) -> CommanderTaskProposal:
    """Parse a single task proposal."""
    if not isinstance(raw, dict):
        raise ValueError(f"task {index} must be a JSON object")

    allowed = {
        "title", "repo", "capabilities", "goal", "acceptance_criteria",
        "verification_commands", "expected_files", "expected_minutes",
        "parent_task_id", "rationale",
    }
    unknown = set(raw.keys()) - allowed
    if unknown:
        raise ValueError(f"task {index} has unknown fields: {unknown}")

    for field in allowed:
        if field not in raw:
            raise ValueError(f"task {index} missing required field: {field}")

    # Validate strings are non-empty
    for str_field in ("title", "repo", "goal", "rationale"):
        if not isinstance(raw[str_field], str) or not raw[str_field].strip():
            raise ValueError(f"task {index} {str_field} must be a non-empty string")

    # Validate lists
    for list_field in ("capabilities", "acceptance_criteria", "verification_commands"):
        if not isinstance(raw[list_field], list):
            raise ValueError(f"task {index} {list_field} must be a list")

    if len(raw["acceptance_criteria"]) > 5:
        raise ValueError(f"task {index} has too many acceptance criteria: {len(raw['acceptance_criteria'])}")

    # Validate numeric fields
    if not isinstance(raw["expected_files"], int) or raw["expected_files"] < 0:
        raise ValueError(f"task {index} expected_files must be a non-negative integer")
    if not isinstance(raw["expected_minutes"], int) or raw["expected_minutes"] < 0:
        raise ValueError(f"task {index} expected_minutes must be a non-negative integer")

    return CommanderTaskProposal(
        title=raw["title"],
        repo=raw["repo"],
        capabilities=raw["capabilities"],
        goal=raw["goal"],
        acceptance_criteria=raw["acceptance_criteria"],
        verification_commands=raw["verification_commands"],
        expected_files=raw["expected_files"],
        expected_minutes=raw["expected_minutes"],
        parent_task_id=raw.get("parent_task_id"),
        rationale=raw["rationale"],
    )


def commander_response_schema() -> dict:
    """Return the JSON schema for Commander responses."""
    return {
        "type": "object",
        "required": ["schema_version", "goal_status", "progress_summary", "tasks", "stop_reason"],
        "properties": {
            "schema_version": {"type": "integer", "const": 1},
            "goal_status": {"type": "string", "enum": ["active", "blocked", "completed"]},
            "progress_summary": {"type": "string", "minLength": 1},
            "tasks": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "required": [
                        "title", "repo", "capabilities", "goal",
                        "acceptance_criteria", "verification_commands",
                        "expected_files", "expected_minutes",
                        "parent_task_id", "rationale",
                    ],
                    "properties": {
                        "title": {"type": "string", "minLength": 1},
                        "repo": {"type": "string", "minLength": 1},
                        "capabilities": {"type": "array", "items": {"type": "string"}},
                        "goal": {"type": "string", "minLength": 1},
                        "acceptance_criteria": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                        "verification_commands": {"type": "array", "items": {"type": "string"}},
                        "expected_files": {"type": "integer", "minimum": 0},
                        "expected_minutes": {"type": "integer", "minimum": 0},
                        "parent_task_id": {"type": ["string", "null"]},
                        "rationale": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": False,
                },
            },
            "stop_reason": {"type": ["string", "null"]},
        },
        "additionalProperties": False,
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _artifact_dir(root: Path, goal_id: int, run_id: int) -> Path:
    return root / "runs" / "commander" / str(goal_id) / str(run_id)


def _finish_commander_attempt(
    conn: sqlite3.Connection,
    *,
    run_id_db: int,
    root: Path,
    goal_id: int,
    tmp_dir: Path,
    prompt_path: Path,
    raw_output_path: Path,
    parsed_output_path: Path | None,
    response: CommanderResponse | None,
    exit_code: int,
    timed_out: bool,
    error: str | None,
    duration: float,
) -> tuple[Path, Path, Path | None]:
    import shutil

    art_dir = _artifact_dir(root, goal_id, run_id_db)
    art_dir.mkdir(parents=True, exist_ok=True)
    for item in tmp_dir.iterdir():
        shutil.move(str(item), str(art_dir / item.name))
    if tmp_dir.exists():
        tmp_dir.rmdir()

    prompt_path = art_dir / "prompt.md"
    raw_output_path = art_dir / "raw.txt"
    if parsed_output_path is not None:
        parsed_output_path = art_dir / "parsed.json"

    finish_commander_run(
        conn,
        run_id_db,
        status="succeeded" if response is not None else "failed",
        exit_code=exit_code,
        timed_out=timed_out,
        raw_output_path=str(raw_output_path),
        parsed_output_path=str(parsed_output_path) if parsed_output_path else "",
        progress_summary=response.progress_summary if response else "",
        stop_reason=response.stop_reason or "" if response else "",
        error=error or "",
        duration_seconds=duration,
    )
    return prompt_path, raw_output_path, parsed_output_path


def run_commander(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    goal_id: int,
    trigger: str,
    timeout_seconds: float = 30,
    rejected_fingerprints: list[str] | None = None,
) -> CommanderRunResult:
    """Run the Commander agent and return structured results.

    Uses only the configured 'commander' role agent.
    """
    agent = select_agent_by_role(config, "commander")
    if agent is None:
        raise ValueError("no agent configured with 'commander' role")

    tmp_dir = root / "runs" / "commander" / str(goal_id) / "_pending"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    context = build_commander_context(conn, config, root, goal_id, rejected_fingerprints)
    prompt_path = tmp_dir / "prompt.md"
    prompt_path.write_text(context)

    schema_path = tmp_dir / "schema.json"
    schema_path.write_text(json.dumps(commander_response_schema(), indent=2))

    goal = get_goal(conn, goal_id)
    repo_ids = json.loads(goal["repo_ids"])
    if repo_ids and repo_ids[0] in config.repos:
        repo_path = config.repos[repo_ids[0]].path
    else:
        repo_path = root

    run_id_db = acquire_commander_run_slot(
        conn,
        goal_id,
        trigger,
        COMMANDER_SCHEMA_VERSION,
        prompt_path,
    )
    if run_id_db is None:
        raise CommanderRunActiveError("commander run already active")

    try:
        argv = _render_command_tokens(agent.command, prompt_path, schema_path, repo_path)
    except ValueError as exc:
        raw_output_path = tmp_dir / "raw.txt"
        raw_output_path.write_text("")
        prompt_path, raw_output_path, _ = _finish_commander_attempt(
            conn,
            run_id_db=run_id_db,
            root=root,
            goal_id=goal_id,
            tmp_dir=tmp_dir,
            prompt_path=prompt_path,
            raw_output_path=raw_output_path,
            parsed_output_path=None,
            response=None,
            exit_code=1,
            timed_out=False,
            error=str(exc),
            duration=0.0,
        )
        return CommanderRunResult(
            succeeded=False,
            response=None,
            run_id=run_id_db,
            prompt_path=prompt_path,
            raw_output_path=raw_output_path,
            parsed_output_path=None,
            exit_code=1,
            timed_out=False,
            error=str(exc),
        )

    if not argv:
        raw_output_path = tmp_dir / "raw.txt"
        raw_output_path.write_text("")
        prompt_path, raw_output_path, _ = _finish_commander_attempt(
            conn,
            run_id_db=run_id_db,
            root=root,
            goal_id=goal_id,
            tmp_dir=tmp_dir,
            prompt_path=prompt_path,
            raw_output_path=raw_output_path,
            parsed_output_path=None,
            response=None,
            exit_code=1,
            timed_out=False,
            error="empty command",
            duration=0.0,
        )
        return CommanderRunResult(
            succeeded=False,
            response=None,
            run_id=run_id_db,
            prompt_path=prompt_path,
            raw_output_path=raw_output_path,
            parsed_output_path=None,
            exit_code=1,
            timed_out=False,
            error="empty command",
        )

    start_time = time.monotonic()
    result = run_command(argv, cwd=repo_path, timeout_seconds=timeout_seconds)
    duration = time.monotonic() - start_time

    raw_output_path = tmp_dir / "raw.txt"
    raw_text = result.stdout
    if result.stderr:
        separator = "\n" if raw_text and not raw_text.endswith("\n") else ""
        raw_text = f"{raw_text}{separator}[stderr]\n{result.stderr}"
    raw_output_path.write_text(raw_text)

    response: CommanderResponse | None = None
    parsed_output_path: Path | None = None
    error: str | None = None

    if result.timed_out:
        error = "timeout"
    elif result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        error = f"exit code {result.returncode}"
        if detail:
            error = f"{error}: {detail[-2000:]}"
    else:
        try:
            response = parse_commander_response(result.stdout)
            parsed_output_path = tmp_dir / "parsed.json"
            parsed_output_path.write_text(json.dumps({
                "schema_version": response.schema_version,
                "goal_status": response.goal_status,
                "progress_summary": response.progress_summary,
                "tasks": [
                    {
                        "title": t.title,
                        "repo": t.repo,
                        "capabilities": t.capabilities,
                        "goal": t.goal,
                        "acceptance_criteria": t.acceptance_criteria,
                        "verification_commands": t.verification_commands,
                        "expected_files": t.expected_files,
                        "expected_minutes": t.expected_minutes,
                        "parent_task_id": t.parent_task_id,
                        "rationale": t.rationale,
                    }
                    for t in response.tasks
                ],
                "stop_reason": response.stop_reason,
            }, indent=2))
        except ValueError as exc:
            error = f"parse error: {exc}"

    prompt_path, raw_output_path, parsed_output_path = _finish_commander_attempt(
        conn,
        run_id_db=run_id_db,
        root=root,
        goal_id=goal_id,
        tmp_dir=tmp_dir,
        prompt_path=prompt_path,
        raw_output_path=raw_output_path,
        parsed_output_path=parsed_output_path,
        response=response,
        exit_code=result.returncode,
        timed_out=result.timed_out,
        error=error,
        duration=duration,
    )

    return CommanderRunResult(
        succeeded=response is not None,
        response=response,
        run_id=run_id_db,
        prompt_path=prompt_path,
        raw_output_path=raw_output_path,
        parsed_output_path=parsed_output_path,
        exit_code=result.returncode,
        timed_out=result.timed_out,
        error=error,
    )


def classify_commander_failure(result: CommanderRunResult) -> str:
    """Classify a failed Commander run for retry and safety policy."""
    if result.timed_out:
        return "timeout"
    error = (result.error or "").lower()
    if "429" in error or "quota" in error or "rate" in error or "rate-limit" in error:
        return "quota"
    if "parse" in error or "protocol" in error or "schema" in error:
        return "protocol"
    if result.exit_code not in (0, None):
        return "process"
    return "unknown"
