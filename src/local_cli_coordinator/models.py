from dataclasses import dataclass, field
from datetime import datetime, timezone

GOAL_STATES = frozenset({
    "draft", "active", "paused", "blocked",
    "completed", "failed", "abandoned",
})
NONTERMINAL_GOAL_STATES = frozenset({
    "draft", "active", "paused", "blocked",
})

TASK_STATES = {
    "inbox",
    "planned",
    "ready",
    "running",
    "verifying",
    "committing",
    "pushing",
    "merging",
    "reviewing_spec",
    "reviewing_quality",
    "awaiting_human",
    "rejected",
    "done",
    "failed",
    "retrying",
    "reassigned",
    "needs_split",
    "blocked",
}


@dataclass(frozen=True)
class TaskDraft:
    title: str
    repo: str
    priority: str
    capabilities: list[str]
    goal: str
    acceptance_criteria: list[str]
    verification_commands: list[str] = field(default_factory=list)
    source_path: str = ""


@dataclass
class Finding:
    id: str
    repo: str
    source: str
    title: str
    body: str
    severity: str
    evidence: str
    discovered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "repo": self.repo,
            "source": self.source,
            "title": self.title,
            "body": self.body,
            "severity": self.severity,
            "evidence": self.evidence,
            "discovered_at": self.discovered_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Finding":
        return cls(
            id=str(data["id"]),
            repo=str(data["repo"]),
            source=str(data["source"]),
            title=str(data["title"]),
            body=str(data.get("body", "")),
            severity=str(data.get("severity", "info")),
            evidence=str(data.get("evidence", "")),
            discovered_at=str(data.get("discovered_at", "")),
        )
