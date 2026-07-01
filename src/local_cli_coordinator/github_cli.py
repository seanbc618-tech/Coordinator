"""Safe GitHub CLI adapter for delivery flows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .process import ProcessResult, run_command


@dataclass(frozen=True)
class PrView:
    number: int
    url: str
    title: str
    state: str
    head_ref: str
    base_ref: str


@dataclass(frozen=True)
class PrCheck:
    name: str
    state: str
    bucket: str


@dataclass(frozen=True)
class PrReviewComment:
    comment_id: int
    author: str
    body: str
    path: str
    line: int | None
    is_resolved: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.comment_id,
            "author": self.author,
            "body": self.body,
            "path": self.path,
            "line": self.line,
            "isResolved": self.is_resolved,
        }


@dataclass(frozen=True)
class GhCommandResult:
    returncode: int
    stdout: str
    stderr: str


class GitHubCli:
    """Invoke ``gh`` with argv lists only — never shell interpolation."""

    def __init__(
        self,
        *,
        executable: str = "gh",
        extra_prefix: list[str] | None = None,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._executable = executable
        self._extra_prefix = list(extra_prefix or [])
        self._cwd = cwd
        self._env = dict(env) if env is not None else None

    def _argv(self, *args: str) -> list[str]:
        return [self._executable, *self._extra_prefix, *args]

    def _run(self, *args: str) -> GhCommandResult:
        result: ProcessResult = run_command(
            self._argv(*args),
            cwd=self._cwd,
            env=self._env,
        )
        return GhCommandResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def pr_view(self, number: int) -> PrView | None:
        result = self._run(
            "pr",
            "view",
            str(number),
            "--json",
            "number,url,title,state,headRefName,baseRefName",
        )
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        return PrView(
            number=int(payload["number"]),
            url=str(payload["url"]),
            title=str(payload.get("title", "")),
            state=str(payload.get("state", "")),
            head_ref=str(payload.get("headRefName", "")),
            base_ref=str(payload.get("baseRefName", "")),
        )

    def pr_create(
        self,
        *,
        title: str,
        body: str,
        base: str,
        head: str,
    ) -> GhCommandResult:
        return self._run(
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--base",
            base,
            "--head",
            head,
        )

    def pr_edit(self, number: int, *, body: str) -> GhCommandResult:
        return self._run("pr", "edit", str(number), "--body", body)

    def pr_body(self, number: int) -> str | None:
        result = self._run("pr", "view", str(number), "--json", "body")
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        body = payload.get("body")
        return str(body) if body is not None else ""

    def pr_review_comments(self, number: int) -> list[PrReviewComment]:
        result = self._run("pr", "view", str(number), "--json", "comments")
        if result.returncode != 0:
            return []
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []
        raw_comments = payload.get("comments")
        if not isinstance(raw_comments, list):
            return []
        comments: list[PrReviewComment] = []
        for item in raw_comments:
            if not isinstance(item, dict):
                continue
            comments.append(
                PrReviewComment(
                    comment_id=int(item.get("id", 0)),
                    author=str(item.get("author", "reviewer")),
                    body=str(item.get("body", "")),
                    path=str(item.get("path", "")),
                    line=int(item["line"]) if item.get("line") is not None else None,
                    is_resolved=bool(item.get("isResolved", False)),
                )
            )
        return comments

    def pr_checks(self, number: int) -> list[PrCheck]:
        result = self._run("pr", "checks", str(number), "--json", "name,state,bucket")
        if result.returncode != 0:
            return []
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        checks: list[PrCheck] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            checks.append(
                PrCheck(
                    name=str(item.get("name", "")),
                    state=str(item.get("state", "")),
                    bucket=str(item.get("bucket", "")),
                )
            )
        return checks


def classify_check_bucket(checks: list[PrCheck]) -> str:
    """Aggregate PR check buckets into pending/pass/fail/cancelled/skipped."""
    if not checks:
        return "pending"
    buckets = {check.bucket for check in checks}
    if "fail" in buckets:
        return "fail"
    if "pending" in buckets:
        return "pending"
    if buckets <= {"cancel"}:
        return "cancelled"
    if buckets <= {"skip", "skipping"}:
        return "skipped"
    return "pass"