"""Classify agent execution results to detect interactive approval blocks.

When a worker agent asks the operator for permission instead of implementing,
the classifier detects this so the engine can hand the task to a fallback agent.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass


class AgentResultClass(enum.Enum):
    """High-level outcome of a worker agent invocation."""

    COMPLETED = "completed"
    INTERACTIVE_BLOCKED = "interactive_blocked"
    COMMAND_FAILED = "command_failed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class ClassifiedResult:
    """Immutable classification of an agent run."""

    classification: AgentResultClass
    reason: str


# Patterns that indicate the agent is asking the operator for approval rather
# than implementing.  Each tuple is (compiled_pattern, reason_code).
# Patterns are matched case-insensitively against the full output text.
_BLOCK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Explicit "should I proceed" / "shall I" / "may I" questions
    (re.compile(r"\bshould\s+i\s+(proceed|continue|go\s+ahead|implement|start)\b", re.I), "approval_request"),
    (re.compile(r"\bshall\s+i\s+(proceed|continue|go\s+ahead|implement|start)\b", re.I), "approval_request"),
    (re.compile(r"\bmay\s+i\s+(proceed|continue|go\s+ahead|implement|start|begin)\b", re.I), "approval_request"),
    # "Would you like me to ..."
    (re.compile(r"\bwould\s+you\s+like\s+me\s+to\b", re.I), "approval_request"),
    # "Do you want me to ..."
    (re.compile(r"\bdo\s+you\s+want\s+me\s+to\b", re.I), "approval_request"),
    # Plan mode approval requests
    (re.compile(r"\bexit\s+plan\s+mode\b.*\b(approval|confirm|proceed|begin)\b", re.I), "plan_exit_approval"),
    (re.compile(r"\benter\s*plan\s*mode\b.*\b(approval|confirm|proceed)\b", re.I), "plan_enter_approval"),
    (re.compile(r"\bplan\s+mode\b.*\bmay\s+i\b", re.I), "plan_mode_approval"),
    # "Tell me to proceed"
    (re.compile(r"\btell\s+me\s+to\s+(proceed|continue|start|begin)\b", re.I), "tell_me_to_proceed"),
    # "Before I" + action-seeking
    (re.compile(r"\bbefore\s+i\s+\w+.*\b(approve|confirm|proceed|go\s+ahead)\b", re.I), "implementation_approval"),
    # Explicit "please confirm" / "please approve"
    (re.compile(r"\bplease\s+(confirm|approve)\b", re.I), "explicit_approval"),
]


def classify_agent_output(
    text: str,
    *,
    exit_code: int = 0,
    timed_out: bool = False,
) -> ClassifiedResult:
    """Classify agent output text into a high-level result category.

    Non-zero exit codes and timeouts take precedence over text analysis.
    """
    # Timeouts take highest precedence over exit codes
    if timed_out:
        return ClassifiedResult(
            classification=AgentResultClass.TIMED_OUT,
            reason="timeout",
        )

    # Non-zero exit code
    if exit_code != 0:
        return ClassifiedResult(
            classification=AgentResultClass.COMMAND_FAILED,
            reason=f"exit_code={exit_code}",
        )

    # Check for interactive approval patterns
    for pattern, reason_code in _BLOCK_PATTERNS:
        if pattern.search(text):
            return ClassifiedResult(
                classification=AgentResultClass.INTERACTIVE_BLOCKED,
                reason=reason_code,
            )

    return ClassifiedResult(
        classification=AgentResultClass.COMPLETED,
        reason="ok",
    )
