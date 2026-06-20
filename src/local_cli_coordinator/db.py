import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
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


def artifact_kinds(conn: sqlite3.Connection, task_id: str) -> set[str]:
    rows = conn.execute(
        "select kind from artifacts where task_id = ?",
        (task_id,),
    ).fetchall()
    return {row["kind"] for row in rows}


def list_task_events(conn: sqlite3.Connection, task_id: str) -> list[sqlite3.Row]:
    """Return ordered state transitions for a task."""
    return conn.execute(
        "select old_state, new_state, note, created_at from events "
        "where task_id = ? order by id",
        (task_id,),
    ).fetchall()


def list_task_artifacts(conn: sqlite3.Connection, task_id: str) -> list[sqlite3.Row]:
    """Return artifacts with kind and path for a task."""
    return conn.execute(
        "select kind, path from artifacts where task_id = ? order by id",
        (task_id,),
    ).fetchall()


def start_daemon_run(conn: sqlite3.Connection) -> int:
    cursor = conn.execute("insert into daemon_runs default values")
    conn.commit()
    return cursor.lastrowid


def finish_daemon_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    tasks_processed: int,
    failures: int,
    stop_reason: str | None = None,
) -> None:
    conn.execute(
        """
        update daemon_runs
        set ended_at = current_timestamp,
            tasks_processed = ?,
            failures = ?,
            stop_reason = ?
        where id = ?
        """,
        (tasks_processed, failures, stop_reason, run_id),
    )
    conn.commit()


def circuit_breaker_reason(conn: sqlite3.Connection, policy) -> str | None:
    daily_tasks = conn.execute(
        """
        select coalesce(sum(tasks_processed), 0) as total
        from daemon_runs
        where date(started_at) = date('now')
        """,
    ).fetchone()["total"]
    if daily_tasks >= policy.max_tasks_per_day:
        return f"daily task limit reached ({daily_tasks}/{policy.max_tasks_per_day})"

    consecutive_failures = 0
    rows = conn.execute(
        """
        select failures
        from daemon_runs
        where ended_at is not null
          and tasks_processed > 0
        order by id desc
        """
    ).fetchall()
    for row in rows:
        if row["failures"] <= 0:
            break
        consecutive_failures += 1
    if consecutive_failures >= policy.max_consecutive_failures:
        return (
            "consecutive failure limit reached "
            f"({consecutive_failures}/{policy.max_consecutive_failures})"
        )
    return None


# ---------------------------------------------------------------------------
# Task leasing
# ---------------------------------------------------------------------------

DEFAULT_LEASE_DURATION_SECONDS = 1800  # 30 minutes


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _lease_expires_at(duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS) -> str:
    return (_utcnow() + timedelta(seconds=duration_seconds)).isoformat()


def _is_lease_expired(expires_at: str) -> bool:
    try:
        return _utcnow() > datetime.fromisoformat(expires_at)
    except (ValueError, TypeError):
        return True


def _try_acquire_task_lease(
    conn: sqlite3.Connection,
    task_id: str,
    agent_id: str,
    duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
) -> bool:
    now = _utcnow().isoformat()
    expires_at = _lease_expires_at(duration_seconds)
    conn.execute(
        """
        update task_leases
        set released_at = current_timestamp
        where task_id = ?
          and released_at is null
          and expires_at <= ?
        """,
        (task_id, now),
    )
    active = conn.execute(
        """
        select id from task_leases
        where task_id = ?
          and released_at is null
          and expires_at > ?
        """,
        (task_id, now),
    ).fetchone()
    if active is not None:
        return False
    conn.execute(
        "insert into task_leases(task_id, agent_id, expires_at) values (?, ?, ?)",
        (task_id, agent_id, expires_at),
    )
    return True


def acquire_task_lease(
    conn: sqlite3.Connection,
    task_id: str,
    agent_id: str,
    duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
) -> bool:
    """Atomically claim a task lease.

    Returns True if the lease was acquired, False if the task is already
    leased by another active lease.
    """
    conn.execute("begin immediate")
    try:
        acquired = _try_acquire_task_lease(conn, task_id, agent_id, duration_seconds)
        conn.commit()
        return acquired
    except sqlite3.IntegrityError:
        conn.rollback()
        return False
    except sqlite3.Error:
        conn.rollback()
        raise


def release_task_lease(conn: sqlite3.Connection, task_id: str) -> None:
    """Release all active leases for a task."""
    conn.execute(
        """
        update task_leases set released_at = current_timestamp
        where task_id = ? and released_at is null
        """,
        (task_id,),
    )
    conn.commit()


def active_lease_count(conn: sqlite3.Connection, agent_id: str | None = None) -> int:
    """Count active (non-expired, non-released) leases."""
    if agent_id is not None:
        row = conn.execute(
            """
            select count(*) as cnt from task_leases
            where released_at is null and agent_id = ?
              and expires_at > ?
            """,
            (agent_id, _utcnow().isoformat()),
        ).fetchone()
    else:
        row = conn.execute(
            """
            select count(*) as cnt from task_leases
            where released_at is null and expires_at > ?
            """,
            (_utcnow().isoformat(),),
        ).fetchone()
    return row["cnt"]


# ---------------------------------------------------------------------------
# Attempt result tracking
# ---------------------------------------------------------------------------


def start_attempt(
    conn: sqlite3.Connection,
    task_id: str,
    agent_id: str,
    command: str,
    *,
    fallback_from_attempt_id: int | None = None,
) -> int:
    """Create a new attempt record and return its ID.

    Raises ValueError if fallback_from_attempt_id belongs to a different task.
    """
    if fallback_from_attempt_id is not None:
        parent = conn.execute(
            "select task_id from attempts where id = ?",
            (fallback_from_attempt_id,),
        ).fetchone()
        if parent is None:
            raise ValueError(f"unknown attempt: {fallback_from_attempt_id}")
        if parent["task_id"] != task_id:
            raise ValueError(
                f"fallback parent {fallback_from_attempt_id} belongs to "
                f"{parent['task_id']}, not {task_id}"
            )
    cursor = conn.execute(
        """
        insert into attempts(task_id, agent_id, command, fallback_from_attempt_id)
        values (?, ?, ?, ?)
        """,
        (task_id, agent_id, command, fallback_from_attempt_id),
    )
    conn.commit()
    return cursor.lastrowid


def finish_attempt(
    conn: sqlite3.Connection,
    attempt_id: int,
    *,
    exit_code: int,
    result_class: str = "",
    result_reason: str = "",
    log_path: str = "",
    timed_out: bool = False,
) -> None:
    """Record attempt completion with classification."""
    conn.execute(
        """
        update attempts
        set ended_at = current_timestamp,
            exit_code = ?,
            result_class = ?,
            result_reason = ?,
            log_path = ?
        where id = ?
        """,
        (exit_code, result_class, result_reason, log_path, attempt_id),
    )
    conn.commit()


def list_attempts(
    conn: sqlite3.Connection, task_id: str
) -> list[sqlite3.Row]:
    """Return attempts for a task ordered by ID."""
    return conn.execute(
        "select * from attempts where task_id = ? order by id",
        (task_id,),
    ).fetchall()


def fallback_count_for_task(conn: sqlite3.Connection, task_id: str) -> int:
    """Count how many fallback attempts have been made for a task.

    A fallback attempt is one that has a fallback_from_attempt_id set.
    """
    row = conn.execute(
        """
        select count(*) as cnt from attempts
        where task_id = ? and fallback_from_attempt_id is not null
        """,
        (task_id,),
    ).fetchone()
    return row["cnt"]


def claim_next_ready_task(
    conn: sqlite3.Connection,
    agent_id: str,
    max_agent_concurrency: int = 1,
    max_global_concurrency: int = 4,
    duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
) -> sqlite3.Row | None:
    """Claim the next ready task with an atomic lease.

    Returns the claimed task row, or None if no task is available or
    concurrency limits are reached.
    """
    conn.execute("begin immediate")
    try:
        agent_active = active_lease_count(conn, agent_id)
        if agent_active >= max_agent_concurrency:
            conn.commit()
            return None
        global_active = active_lease_count(conn)
        if global_active >= max_global_concurrency:
            conn.commit()
            return None

        candidates = conn.execute(
            "select * from tasks where state = 'ready' order by created_at, id"
        ).fetchall()

        for task in candidates:
            try:
                if _try_acquire_task_lease(conn, task["id"], agent_id, duration_seconds):
                    conn.commit()
                    return task
            except sqlite3.IntegrityError:
                continue

        conn.commit()
        return None
    except sqlite3.Error:
        conn.rollback()
        raise
