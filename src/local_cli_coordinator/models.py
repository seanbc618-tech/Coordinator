from dataclasses import dataclass, field

TASK_STATES = {
    "inbox",
    "planned",
    "ready",
    "running",
    "verifying",
    "committing",
    "pushing",
    "merging",
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
