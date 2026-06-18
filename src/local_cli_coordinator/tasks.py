from pathlib import Path
import re

from .models import TaskDraft


def _parse_list(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [part.strip().strip('"').strip("'") for part in inner.split(",")]
    return [value]


def _section(content: str, name: str) -> str:
    pattern = rf"^## {re.escape(name)}\s*$"
    match = re.search(pattern, content, flags=re.MULTILINE)
    if match is None:
        return ""
    start = match.end()
    next_match = re.search(r"^## .+\s*$", content[start:], flags=re.MULTILINE)
    end = start + next_match.start() if next_match else len(content)
    return content[start:end].strip()


def _bullets(section: str) -> list[str]:
    items: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
    return items


def parse_task_markdown(content: str, source_path: str) -> TaskDraft:
    title_match = re.search(r"^# Task:\s*(.+)$", content, flags=re.MULTILINE)
    if title_match is None:
        raise ValueError(f"task file missing '# Task:' title: {source_path}")
    metadata: dict[str, str] = {}
    for line in content.splitlines():
        if line.startswith("## "):
            break
        if ":" in line and not line.startswith("#"):
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
    goal = _section(content, "Goal")
    acceptance = _bullets(_section(content, "Acceptance Criteria"))
    return TaskDraft(
        title=title_match.group(1).strip(),
        repo=metadata.get("repo", ""),
        priority=metadata.get("priority", "normal"),
        capabilities=_parse_list(metadata.get("capabilities", "[]")),
        verification_commands=_parse_list(metadata.get("verification", "[]")),
        goal=goal,
        acceptance_criteria=acceptance,
        source_path=source_path,
    )


def scan_inbox(root: Path) -> list[TaskDraft]:
    inbox = root / "tasks" / "inbox"
    if not inbox.exists():
        return []
    tasks: list[TaskDraft] = []
    for path in sorted(inbox.glob("*.md")):
        tasks.append(parse_task_markdown(path.read_text(), str(path.relative_to(root))))
    return tasks


def _filename_slug(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()
    return slug[:60] or "generated-task"


def write_generated_task(root: Path, task: TaskDraft) -> Path:
    generated = root / "tasks" / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    path = generated / f"{_filename_slug(task.title)}.md"
    acceptance = "\n".join(f"- {item}" for item in task.acceptance_criteria)
    capabilities = ", ".join(task.capabilities)
    verification = ", ".join(task.verification_commands)
    source_line = ""
    if task.source_path:
        source_line = f"source: {task.source_path}\n"
    path.write_text(
        f"# Task: {task.title}\n\n"
        f"repo: {task.repo}\n"
        f"priority: {task.priority}\n"
        f"capabilities: [{capabilities}]\n"
        f"verification: [{verification}]\n"
        f"{source_line}\n"
        f"## Goal\n\n{task.goal}\n\n"
        f"## Acceptance Criteria\n\n{acceptance}\n"
    )
    return path
