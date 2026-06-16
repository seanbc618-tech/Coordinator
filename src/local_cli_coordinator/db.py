import sqlite3
import uuid
from pathlib import Path

from .models import TASK_STATES

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "migrations"


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


def init_db(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> None:
    conn.execute(
        "create table if not exists schema_migrations "
        "(version text primary key, applied_at text not null default current_timestamp)"
    )
    conn.commit()
    applied = {
        row["version"]
        for row in conn.execute("select version from schema_migrations").fetchall()
    }
    for migration in sorted(migrations_dir.glob("*.sql")):
        if migration.name in applied:
            continue
        script = (
            "begin;\n"
            f"{migration.read_text()}\n"
            "insert into schema_migrations(version) values "
            f"({_sql_string(migration.name)});\n"
            "commit;"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error:
            conn.rollback()
            raise
    conn.commit()


def create_task(
    conn: sqlite3.Connection,
    *,
    title: str,
    repo: str,
    source_path: str,
    priority: str,
    capabilities: list[str],
    goal: str,
    acceptance_criteria: list[str],
    verification_commands: list[str],
) -> str:
    task_id = f"task-{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        insert into tasks(
            id, title, repo, state, priority, capabilities, source_path,
            goal, acceptance_criteria, verification_commands
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            title,
            repo,
            "ready",
            priority,
            ",".join(capabilities),
            source_path,
            goal,
            "\n".join(acceptance_criteria),
            "\n".join(verification_commands),
        ),
    )
    conn.execute(
        "insert into events(task_id, old_state, new_state, note) values (?, ?, ?, ?)",
        (task_id, "inbox", "ready", "task imported"),
    )
    conn.commit()
    return task_id


def get_task(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
    row = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown task: {task_id}")
    return row


def task_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "select state, count(*) as count from tasks group by state"
    ).fetchall()
    return {row["state"]: row["count"] for row in rows}


def list_tasks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("select * from tasks order by created_at, id").fetchall()


def transition_task(conn: sqlite3.Connection, task_id: str, new_state: str, note: str) -> None:
    if new_state not in TASK_STATES:
        raise ValueError(f"invalid task state: {new_state}")
    current = get_task(conn, task_id)
    conn.execute(
        "update tasks set state = ?, updated_at = current_timestamp where id = ?",
        (new_state, task_id),
    )
    conn.execute(
        "insert into events(task_id, old_state, new_state, note) values (?, ?, ?, ?)",
        (task_id, current["state"], new_state, note),
    )
    conn.commit()


def next_ready_task(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "select * from tasks where state = ? order by created_at, id limit 1",
        ("ready",),
    ).fetchone()


def set_task_branch_and_worktree(
    conn: sqlite3.Connection,
    task_id: str,
    branch: str,
    worktree_path: Path,
) -> None:
    conn.execute(
        "update tasks set branch = ?, worktree_path = ?, updated_at = current_timestamp where id = ?",
        (branch, str(worktree_path), task_id),
    )
    conn.commit()


def add_artifact(conn: sqlite3.Connection, task_id: str, kind: str, path: Path) -> None:
    conn.execute(
        "insert into artifacts(task_id, kind, path) values (?, ?, ?)",
        (task_id, kind, str(path)),
    )
    conn.commit()
