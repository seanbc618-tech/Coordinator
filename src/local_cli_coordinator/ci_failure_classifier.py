"""Map CI check output to bounded failure classes."""

from __future__ import annotations

import re
from dataclasses import dataclass

_FLAKY_RE = re.compile(r"(?i)flaky|passed on retry|retry \d+/\d+")
_TEST_RE = re.compile(r"(?i)FAILED|AssertionError|pytest|unittest|::test_")
_LINT_RE = re.compile(r"(?i)flake8|ruff|eslint|lint|E\d{3}|W\d{3}")
_TYPECHECK_RE = re.compile(r"(?i)typecheck|mypy|pyright|TS\d{4}|tsc")
_BUILD_RE = re.compile(r"(?i)build failed|npm ERR!|cargo build|compile error")
_INFRA_RE = re.compile(r"(?i)timeout|runner|infra|503|connection refused")


@dataclass(frozen=True)
class ClassifiedFailure:
    check_name: str
    state: str
    bucket: str
    failure_class: str
    summary: str
    log_excerpt: str


def classify_check_failure(
    *,
    check_name: str,
    state: str,
    bucket: str,
    log_excerpt: str,
) -> ClassifiedFailure:
    text = log_excerpt.strip()
    failure_class = "unknown"
    if _FLAKY_RE.search(text):
        failure_class = "flaky"
    elif _TEST_RE.search(text):
        failure_class = "test_failure"
    elif _LINT_RE.search(text):
        failure_class = "lint_failure"
    elif _TYPECHECK_RE.search(text):
        failure_class = "typecheck_failure"
    elif _BUILD_RE.search(text):
        failure_class = "build_failure"
    elif _INFRA_RE.search(text):
        failure_class = "infra"

    summary = text.splitlines()[0][:240] if text else f"{check_name} failed"
    return ClassifiedFailure(
        check_name=check_name,
        state=state,
        bucket=bucket,
        failure_class=failure_class,
        summary=summary,
        log_excerpt=text,
    )


def summarize_check_failure(classified: ClassifiedFailure) -> str:
    return (
        f"{classified.check_name}: {classified.failure_class} — "
        f"{classified.summary}"
    )