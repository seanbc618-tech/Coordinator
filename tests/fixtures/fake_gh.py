#!/usr/bin/env python3
"""Deterministic fake ``gh`` for Phase 9+ tests. Never calls GitHub."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _log_path() -> Path | None:
    raw = os.environ.get("GH_FAKE_LOG")
    if not raw:
        return None
    return Path(raw)


def _append_log(line: str) -> None:
    path = _log_path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _scenario() -> dict:
    raw = os.environ.get("GH_FAKE_SCENARIO", "{}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _emit(payload: object, *, exit_code: int = 0) -> int:
    if isinstance(payload, str):
        sys.stdout.write(payload)
        if not payload.endswith("\n"):
            sys.stdout.write("\n")
    else:
        json.dump(payload, sys.stdout)
        sys.stdout.write("\n")
    return exit_code


def _emit_error(message: str, *, exit_code: int = 1) -> int:
    sys.stderr.write(message)
    if not message.endswith("\n"):
        sys.stderr.write("\n")
    return exit_code


def _pr_number_from_args(pr_args: list[str], scenario: dict) -> int:
    for token in pr_args[1:]:
        if token.isdigit():
            return int(token)
    return int(scenario.get("pr_number", 42))


def _json_fields(pr_args: list[str]) -> list[str]:
    if "--json" not in pr_args:
        return []
    index = pr_args.index("--json")
    if index + 1 >= len(pr_args):
        return []
    return [field.strip() for field in pr_args[index + 1].split(",") if field.strip()]


def _filter_payload(payload: dict, fields: list[str]) -> dict:
    if not fields:
        return payload
    return {key: payload[key] for key in fields if key in payload}


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    _append_log(" ".join(args))

    if not args:
        return _emit_error("fake gh: missing subcommand", exit_code=2)

    scenario = _scenario()
    forced_exit = scenario.get("exit_code")
    if forced_exit is not None:
        return _emit_error(str(scenario.get("stderr", "forced failure")), exit_code=int(forced_exit))

    if args[0] != "pr":
        return _emit_error(f"fake gh: unsupported command {args[0]!r}", exit_code=2)

    pr_args = args[1:]
    if not pr_args:
        return _emit_error("fake gh: missing pr subcommand", exit_code=2)

    sub = pr_args[0]
    if sub == "view":
        number = _pr_number_from_args(pr_args, scenario)
        base_payload = scenario.get(
            "pr_view",
            {
                "number": number,
                "title": scenario.get("pr_title", "Coordinator delivery"),
                "url": scenario.get("pr_url", f"https://github.com/example/repo/pull/{number}"),
                "state": scenario.get("pr_state", "OPEN"),
                "headRefName": scenario.get("head_ref", "coord/task-1"),
                "baseRefName": scenario.get("base_ref", "main"),
            },
        )
        payload = dict(base_payload)
        payload.setdefault("number", number)
        payload["body"] = scenario.get("pr_body", payload.get("body", ""))
        payload["comments"] = scenario.get("review_comments", payload.get("comments", []))
        fields = _json_fields(pr_args)
        return _emit(_filter_payload(payload, fields) if fields else payload)

    if sub == "create":
        number = int(scenario.get("create_number", scenario.get("pr_number", 99)))
        url = scenario.get(
            "create_url",
            scenario.get("pr_url", f"https://github.com/example/repo/pull/{number}"),
        )
        if scenario.get("create_fail"):
            return _emit_error(scenario.get("create_stderr", "pr create failed"), exit_code=1)
        return _emit(url)

    if sub == "edit":
        if scenario.get("edit_fail"):
            return _emit_error(scenario.get("edit_stderr", "pr edit failed"), exit_code=1)
        return _emit("")

    if sub == "checks":
        payload = scenario.get(
            "checks",
            [
                {"name": "unit", "state": "SUCCESS", "bucket": "pass"},
                {"name": "lint", "state": "SUCCESS", "bucket": "pass"},
            ],
        )
        return _emit(payload)

    return _emit_error(f"fake gh: unsupported pr subcommand {sub!r}", exit_code=2)


if __name__ == "__main__":
    raise SystemExit(main())