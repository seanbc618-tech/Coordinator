"""Deterministic mock Commander/worker provider harness for CI parity."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .commander_protocol import parse_commander_response
from .process import ProcessResult
from .reporting import ExecutionContext, ExecutionEvent, NULL_REPORTER, Reporter

_WORKER_REQUIRED_FIELDS = frozenset(
    {"exit_code", "stdout", "stderr", "changed_files"}
)


class MockProviderError(ValueError):
    """Raised when a mock provider fixture or invocation is invalid."""


def validate_commander_fixture(path: Path) -> dict[str, Any]:
    """Load and validate fixture against Commander schema v2."""
    fixture_path = path.resolve()
    if not fixture_path.is_file():
        raise MockProviderError(f"fixture not found: {fixture_path}")
    raw = fixture_path.read_text(encoding="utf-8")
    parse_commander_response(raw)
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise MockProviderError("commander fixture must be a JSON object")
    return payload


def validate_worker_fixture(path: Path) -> dict[str, Any]:
    """Load worker fixture and require exit_code, stdout, stderr, changed_files."""
    fixture_path = path.resolve()
    if not fixture_path.is_file():
        raise MockProviderError(f"fixture not found: {fixture_path}")
    try:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MockProviderError("worker fixture must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise MockProviderError("worker fixture must be a JSON object")
    missing = _WORKER_REQUIRED_FIELDS - set(payload)
    if missing:
        raise MockProviderError(
            "worker fixture missing required fields: "
            + ", ".join(sorted(missing))
        )
    exit_code = payload["exit_code"]
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise MockProviderError("exit_code must be an integer")
    for field in ("stdout", "stderr"):
        if not isinstance(payload[field], str):
            raise MockProviderError(f"{field} must be a string")
    changed_files = payload["changed_files"]
    if not isinstance(changed_files, list) or not all(
        isinstance(item, str) for item in changed_files
    ):
        raise MockProviderError("changed_files must be a list of strings")
    return payload


def render_worker_fixture(path: Path) -> dict[str, Any]:
    """Return deterministic worker output derived from a fixture."""
    payload = validate_worker_fixture(path)
    return {
        "exit_code": payload["exit_code"],
        "stdout": payload["stdout"],
        "stderr": payload["stderr"],
        "changed_files": payload["changed_files"],
        "log_text": payload.get("log_text", payload["stdout"]),
    }


def _resolve_prompt_path(
    prompt: Path | None,
    *,
    env: dict[str, str] | None = None,
) -> Path | None:
    if prompt is not None:
        return prompt.resolve()
    env = env or os.environ
    prompt_value = env.get("COORDINATOR_PROMPT_PATH", "").strip()
    if not prompt_value:
        return None
    return Path(prompt_value).resolve()


def _ensure_prompt_exists(prompt: Path | None, *, env: dict[str, str] | None = None) -> None:
    resolved = _resolve_prompt_path(prompt, env=env)
    if resolved is None:
        return
    if not resolved.is_file():
        raise MockProviderError(f"prompt file not found: {resolved}")


def _normalize_argv(argv: list[str]) -> list[str]:
    tokens = list(argv)
    if len(tokens) >= 4 and tokens[1] == "-m" and tokens[2].endswith("local_cli_coordinator"):
        return tokens[3:]
    if tokens and Path(tokens[0]).name in {"python", "python3"}:
        if len(tokens) >= 4 and tokens[1] == "-m":
            return tokens[3:]
        return tokens[1:]
    return tokens


def parse_mock_provider_argv(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
) -> tuple[str, Path, Path | None] | None:
    """Return ``(role, fixture, prompt)`` when *argv* invokes mock-provider."""
    tokens = _normalize_argv(argv)
    if len(tokens) < 5:
        return None
    if tokens[0] != "mock-provider" or tokens[1] != "run":
        return None
    role = tokens[2]
    if role not in {"commander", "worker"}:
        return None

    fixture: Path | None = None
    prompt: Path | None = None
    index = 3
    while index < len(tokens):
        token = tokens[index]
        if token == "--fixture" and index + 1 < len(tokens):
            fixture = Path(tokens[index + 1])
            index += 2
            continue
        if token == "--prompt" and index + 1 < len(tokens):
            prompt = Path(tokens[index + 1])
            index += 2
            continue
        if not token.startswith("-") and prompt is None and fixture is not None:
            prompt = Path(token)
        index += 1

    if fixture is None:
        return None
    if prompt is None:
        prompt = _resolve_prompt_path(None, env=env)
    return role, fixture.resolve(), prompt


def is_mock_provider_command(command: str) -> bool:
    return "mock-provider" in command


def ensure_mock_provider_prompt(command_argv: list[str], *, prompt_path: Path) -> None:
    """Validate prompt existence for mock-provider commander invocations."""
    parsed = parse_mock_provider_argv(command_argv)
    if parsed is None:
        return
    role, _, _ = parsed
    if role == "commander":
        _ensure_prompt_exists(prompt_path.resolve())


def run_commander_fixture(
    fixture_path: Path,
    *,
    prompt_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> ProcessResult:
    payload = validate_commander_fixture(fixture_path)
    _ensure_prompt_exists(prompt_path, env=env)
    stdout = json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
    return ProcessResult(returncode=0, stdout=stdout, stderr="", timed_out=False)


def run_worker_fixture(
    fixture_path: Path,
    *,
    prompt_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> ProcessResult:
    rendered = render_worker_fixture(fixture_path)
    _ensure_prompt_exists(prompt_path, env=env)
    return ProcessResult(
        returncode=rendered["exit_code"],
        stdout=rendered["stdout"],
        stderr=rendered["stderr"],
        timed_out=False,
    )


def try_run_mock_provider(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    reporter: Reporter = NULL_REPORTER,
    context: ExecutionContext | None = None,
    stdout_sink: Any | None = None,
    stderr_sink: Any | None = None,
) -> ProcessResult | None:
    """Execute mock-provider inline when *argv* targets the harness."""
    parsed = parse_mock_provider_argv(argv, env=env)
    if parsed is None:
        return None

    role, fixture_path, prompt_path = parsed
    stage = context.stage if context is not None else ""
    actor = context.actor if context is not None else ""
    task_id = context.task_id if context is not None else ""
    log_path = context.log_path if context is not None else None

    if role == "commander":
        result = run_commander_fixture(
            fixture_path, prompt_path=prompt_path, env=env
        )
    else:
        result = run_worker_fixture(
            fixture_path, prompt_path=prompt_path, env=env
        )

    if result.stdout:
        reporter.emit(
            ExecutionEvent(
                kind="stdout",
                stage=stage,
                actor=actor,
                task_id=task_id,
                text=result.stdout,
                log_path=log_path,
            )
        )
        if stdout_sink is not None:
            stdout_sink(result.stdout)
    if result.stderr:
        reporter.emit(
            ExecutionEvent(
                kind="stderr",
                stage=stage,
                actor=actor,
                task_id=task_id,
                text=result.stderr,
                log_path=log_path,
            )
        )
        if stderr_sink is not None:
            stderr_sink(result.stderr)
    reporter.emit(
        ExecutionEvent(
            kind="completed",
            stage=stage,
            actor=actor,
            task_id=task_id,
            exit_code=result.returncode,
            timed_out=result.timed_out,
            log_path=log_path,
        )
    )
    return result


def run_mock_provider_cli(
    *,
    role: str,
    fixture: str,
    prompt: str | None = None,
) -> int:
    fixture_path = Path(fixture).resolve()
    prompt_path = Path(prompt).resolve() if prompt else None
    if role == "commander":
        result = run_commander_fixture(fixture_path, prompt_path=prompt_path)
        sys.stdout.write(result.stdout)
        return result.returncode
    if role == "worker":
        result = run_worker_fixture(fixture_path, prompt_path=prompt_path)
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        return result.returncode
    raise MockProviderError(f"unsupported mock provider role: {role!r}")