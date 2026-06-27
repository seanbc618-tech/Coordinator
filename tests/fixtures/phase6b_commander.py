"""Deterministic Commander fixtures for Phase 6B red/green tests."""

from __future__ import annotations

import sys
from pathlib import Path

from local_cli_coordinator.commander_protocol import (
    CommanderResponse,
    CommanderTaskProposal,
)
from local_cli_coordinator.commander_runner import CommanderRunResult
from local_cli_coordinator.config import (
    AgentConfig,
    AutonomyConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
    RepoConfig,
)

_PYTHON = sys.executable
_FAKE_COMMANDER = Path(__file__).resolve().parent / "fake_commander.py"


def make_commander_proposal(
    *,
    title: str = "Add backlog helper",
    repo: str = "test-repo",
    goal: str = "Expose backlog status in loop output",
    rationale: str = "Improves operator visibility",
    acceptance_criteria: list[str] | None = None,
    verification_commands: list[str] | None = None,
) -> CommanderTaskProposal:
    return CommanderTaskProposal(
        title=title,
        repo=repo,
        capabilities=["code"],
        goal=goal,
        acceptance_criteria=acceptance_criteria or ["Loop status shows backlog section"],
        verification_commands=verification_commands or ["true"],
        expected_files=1,
        expected_minutes=5,
        parent_task_id=None,
        rationale=rationale,
    )


def make_commander_response(
    *tasks: CommanderTaskProposal,
    intent: str | None = None,
) -> CommanderResponse:
    resolved_intent = intent or ("task_request" if tasks else "conversation")
    return CommanderResponse(
        schema_version=2,
        intent=resolved_intent,
        user_reply="Commander fixture: plan generated",
        goal_status="active",
        progress_summary="Commander fixture: plan generated",
        tasks=list(tasks),
        stop_reason=None,
    )


def make_commander_run_result(
    response: CommanderResponse,
    *,
    succeeded: bool = True,
    run_id: int = 1,
    tmp_dir: Path | None = None,
) -> CommanderRunResult:
    root = tmp_dir or Path("/tmp")
    prompt_path = root / "commander-prompt.md"
    raw_output_path = root / "commander-raw.json"
    parsed_output_path = root / "commander-parsed.json"
    return CommanderRunResult(
        succeeded=succeeded,
        response=response if succeeded else None,
        run_id=run_id,
        prompt_path=prompt_path,
        raw_output_path=raw_output_path,
        parsed_output_path=parsed_output_path if succeeded else None,
        exit_code=0 if succeeded else 1,
        timed_out=False,
        error=None if succeeded else "commander failed",
    )


def autonomy_loop_config(
    tmp_dir: Path,
    repo_path: Path,
    *,
    repo_id: str = "test-repo",
    max_generated: int = 3,
    commander_command: str | None = None,
) -> CoordinatorConfig:
    if commander_command is None:
        commander_command = f"{_PYTHON} {_FAKE_COMMANDER}"
    return CoordinatorConfig(
        agents={
            "commander": AgentConfig(
                id="commander",
                command=commander_command,
                capabilities=["code", "tests", "docs", "research"],
                max_concurrency=1,
                role="commander",
            ),
            "worker": AgentConfig(
                id="worker",
                command="true",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
        },
        repos={
            repo_id: RepoConfig(
                id=repo_id,
                path=repo_path,
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=False,
                merge_policy="no_push",
                verify_commands=[],
                autonomy_enabled=True,
            ),
        },
        policy=PolicyConfig(
            require_single_repo=False,
            require_acceptance_criteria=False,
            require_verification_commands=False,
            require_handoff_summary=False,
            max_files_touched=20,
            max_expected_minutes=60,
            max_attempts=3,
            split_if_touches_multiple_subsystems=False,
            split_if_research_and_code_are_mixed=False,
        ),
        daemon_policy=DaemonPolicyConfig(),
        autonomy=AutonomyConfig(
            enabled=True,
            max_iterations_per_tick=1,
            max_evaluations_per_iteration=3,
            max_admissions_per_iteration=1,
            max_generated_backlog_per_iteration=max_generated,
            wait_when_running=True,
            require_evaluation_before_followup=True,
            pause_after_consecutive_failures=3,
        ),
    )