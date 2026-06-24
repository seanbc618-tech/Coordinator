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

from .commander_protocol import (
    COMMANDER_SCHEMA_VERSION,
    CommanderResponse,
    CommanderTaskProposal,
    commander_response_schema,
    commander_trigger_instructions,
    parse_commander_response,
)
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
from .reporting import NULL_REPORTER, ExecutionContext, Reporter

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
# Runner
# ---------------------------------------------------------------------------

def _artifact_dir(root: Path, goal_id: int, run_id: int) -> Path:
    return root / "runs" / "commander" / str(goal_id) / str(run_id)


def _finish_commander_attempt(
    conn: sqlite3.Connection,
    *,
    run_id_db: int,
    prompt_path: Path,
    raw_output_path: Path,
    parsed_output_path: Path | None,
    response: CommanderResponse | None,
    exit_code: int,
    timed_out: bool,
    error: str | None,
    duration: float,
    status: str | None = None,
) -> tuple[Path, Path, Path | None]:
    finish_commander_run(
        conn,
        run_id_db,
        status=status or ("succeeded" if response is not None else "failed"),
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


def _prepare_commander_artifacts(
    conn: sqlite3.Connection,
    *,
    root: Path,
    goal_id: int,
    run_id_db: int,
    context: str,
) -> tuple[Path, Path, Path, Path, Path]:
    art_dir = _artifact_dir(root, goal_id, run_id_db)
    art_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = art_dir / "prompt.md"
    schema_path = art_dir / "schema.json"
    raw_output_path = art_dir / "raw.txt"
    stderr_log_path = art_dir / "stderr.log"
    prompt_path.write_text(context)
    schema_path.write_text(json.dumps(commander_response_schema(), indent=2))
    raw_output_path.write_text("")
    stderr_log_path.write_text("")
    conn.execute(
        "update commander_runs set prompt_path = ? where id = ?",
        (str(prompt_path), run_id_db),
    )
    conn.commit()
    return art_dir, prompt_path, schema_path, raw_output_path, stderr_log_path


def run_commander(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    goal_id: int,
    trigger: str,
    timeout_seconds: float = 30,
    rejected_fingerprints: list[str] | None = None,
    reporter: Reporter = NULL_REPORTER,
) -> CommanderRunResult:
    """Run the Commander agent and return structured results.

    Uses only the configured 'commander' role agent.
    """
    agent = select_agent_by_role(config, "commander")
    if agent is None:
        raise ValueError("no agent configured with 'commander' role")

    context = build_commander_context(conn, config, root, goal_id, rejected_fingerprints)
    trigger_instructions = commander_trigger_instructions(trigger)
    if trigger_instructions:
        context = f"{context}\n\n{trigger_instructions}"

    goal = get_goal(conn, goal_id)
    repo_ids = json.loads(goal["repo_ids"])
    if repo_ids and repo_ids[0] in config.repos:
        repo_path = config.repos[repo_ids[0]].path
    else:
        repo_path = root

    pending_prompt = root / "runs" / "commander" / str(goal_id) / "pending" / "prompt.md"
    run_id_db = acquire_commander_run_slot(
        conn,
        goal_id,
        trigger,
        COMMANDER_SCHEMA_VERSION,
        pending_prompt,
    )
    if run_id_db is None:
        raise CommanderRunActiveError("commander run already active")

    _, prompt_path, schema_path, raw_output_path, stderr_log_path = _prepare_commander_artifacts(
        conn,
        root=root,
        goal_id=goal_id,
        run_id_db=run_id_db,
        context=context,
    )

    try:
        argv = _render_command_tokens(agent.command, prompt_path, schema_path, repo_path)
    except ValueError as exc:
        prompt_path, raw_output_path, _ = _finish_commander_attempt(
            conn,
            run_id_db=run_id_db,
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
        prompt_path, raw_output_path, _ = _finish_commander_attempt(
            conn,
            run_id_db=run_id_db,
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

    execution_context = ExecutionContext(
        stage="commander",
        actor=agent.id,
        task_id=str(goal_id),
        log_path=raw_output_path,
    )
    start_time = time.monotonic()
    with (
        raw_output_path.open("a", encoding="utf-8") as raw_file,
        stderr_log_path.open("a", encoding="utf-8") as stderr_file,
    ):
        try:
            result = run_command(
                argv,
                cwd=repo_path,
                timeout_seconds=timeout_seconds,
                reporter=reporter,
                context=execution_context,
                stdout_sink=raw_file.write,
                stderr_sink=stderr_file.write,
            )
        except KeyboardInterrupt:
            duration = time.monotonic() - start_time
            finish_commander_run(
                conn,
                run_id_db,
                status="interrupted",
                error="interrupted by operator",
                duration_seconds=duration,
                raw_output_path=str(raw_output_path),
            )
            raise
    duration = time.monotonic() - start_time

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
            parsed_output_path = raw_output_path.parent / "parsed.json"
            parsed_output_path.write_text(json.dumps({
                "schema_version": response.schema_version,
                "intent": response.intent,
                "user_reply": response.user_reply,
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
