from dataclasses import dataclass
from pathlib import Path

LOOP_MEMORY_RELATIVE_PATH = Path("state") / "loop_state.md"


@dataclass(frozen=True)
class LoopMemoryEntry:
    task_id: str
    repo: str
    title: str
    outcome: str
    branch: str
    verifier_result: str
    next_action: str


def loop_memory_path(root: Path) -> Path:
    return root / LOOP_MEMORY_RELATIVE_PATH


def _one_line(value: str) -> str:
    cleaned = " ".join(str(value).split())
    return cleaned or "(none)"


def append_loop_memory(root: Path, entry: LoopMemoryEntry) -> Path:
    path = loop_memory_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not path.is_file():
            return path
        needs_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8") as handle:
            if needs_header:
                handle.write("# Loop State\n\n")
            handle.write(f"## {_one_line(entry.task_id)} - {_one_line(entry.title)}\n")
            handle.write(f"- repo: {_one_line(entry.repo)}\n")
            handle.write(f"- title: {_one_line(entry.title)}\n")
            handle.write(f"- outcome: {_one_line(entry.outcome)}\n")
            handle.write(f"- branch: {_one_line(entry.branch)}\n")
            handle.write(f"- verifier: {_one_line(entry.verifier_result)}\n")
            handle.write(f"- next action: {_one_line(entry.next_action)}\n\n")
    except OSError:
        return path
    return path
