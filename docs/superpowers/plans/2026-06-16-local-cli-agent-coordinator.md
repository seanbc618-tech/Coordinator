# Local CLI Agent Coordinator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first working local CLI coordinator that imports Markdown tasks, stores durable state in SQLite, runs configured CLI agents inside per-task git worktrees, verifies results, commits, pushes, and advances tasks.

**Architecture:** Implement a single Python package with focused modules for config, database state, Markdown task parsing, policy checks, git operations, agent execution, verification, and orchestration. The daemon and CLI commands share the same service functions so every action is testable without a long-running process.

**Tech Stack:** Python 3.11+, standard library only for the first version (`argparse`, `sqlite3`, `tomllib`, `subprocess`, `pathlib`, `dataclasses`, `unittest`), git CLI, SQLite, TOML config.

---

## Scope Check

The approved spec describes one coherent subsystem: a local CLI-only coordinator. This plan implements the MVP as one Python application, not multiple services. Browser automation, web dashboard, and cloud queue features are outside this plan.

## File Structure

- Create: `pyproject.toml` - package metadata and `coordinator` console script.
- Create: `src/local_cli_coordinator/__init__.py` - package version.
- Create: `src/local_cli_coordinator/__main__.py` - `python -m local_cli_coordinator` entrypoint.
- Create: `src/local_cli_coordinator/cli.py` - argparse commands and terminal output.
- Create: `src/local_cli_coordinator/models.py` - dataclasses and state constants shared across modules.
- Create: `src/local_cli_coordinator/config.py` - TOML config loading and validation.
- Create: `src/local_cli_coordinator/db.py` - SQLite connection, migrations, task persistence, events, artifacts.
- Create: `src/local_cli_coordinator/tasks.py` - Markdown task parsing and inbox scanning.
- Create: `src/local_cli_coordinator/policy.py` - small-task and changed-file rule gate.
- Create: `src/local_cli_coordinator/gitops.py` - git validation, worktree, branch, diff, commit, push, merge.
- Create: `src/local_cli_coordinator/agent.py` - generic CLI agent adapter.
- Create: `src/local_cli_coordinator/verify.py` - verification command runner.
- Create: `src/local_cli_coordinator/engine.py` - one-task orchestration and daemon `--once` loop.
- Create: `migrations/001_initial.sql` - initial SQLite schema.
- Create: `config/agents.toml` - example agent configuration.
- Create: `config/repos.toml` - example repo allowlist configuration.
- Create: `config/policy.toml` - default policy configuration.
- Create: `tasks/inbox/.gitkeep`, `tasks/accepted/.gitkeep`, `tasks/generated/.gitkeep`, `tasks/blocked/.gitkeep`.
- Create: `tests/test_cli.py`, `tests/test_config.py`, `tests/test_db.py`, `tests/test_tasks.py`, `tests/test_policy.py`, `tests/test_gitops.py`, `tests/test_agent.py`, `tests/test_verify.py`, `tests/test_engine.py`.
- Create: `tests/helpers.py` - temporary project, git repo, and CLI helpers.

## State Names

Use these exact task states:

```python
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
```

## Task 1: Project Scaffold And CLI Smoke Test

**Files:**
- Create: `pyproject.toml`
- Create: `src/local_cli_coordinator/__init__.py`
- Create: `src/local_cli_coordinator/__main__.py`
- Create: `src/local_cli_coordinator/cli.py`
- Create: `tests/helpers.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write the CLI smoke tests**

Add this to `tests/helpers.py`:

```python
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    return subprocess.run(
        [sys.executable, "-m", "local_cli_coordinator", *args],
        cwd=cwd or ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
```

Add this to `tests/test_cli.py`:

```python
import unittest

from helpers import run_cli


class CliSmokeTests(unittest.TestCase):
    def test_help_lists_core_commands(self) -> None:
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("daemon", result.stdout)
        self.assertIn("inbox", result.stdout)
        self.assertIn("status", result.stdout)
        self.assertIn("doctor", result.stdout)

    def test_doctor_runs_without_configuration(self) -> None:
        result = run_cli("doctor")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Coordinator doctor", result.stdout)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
python -m unittest tests/test_cli.py -v
```

Expected: FAIL with `No module named local_cli_coordinator`.

- [ ] **Step 3: Create the minimal package and CLI**

Add this to `pyproject.toml`:

```toml
[project]
name = "local-cli-coordinator"
version = "0.1.0"
requires-python = ">=3.11"
description = "Local CLI agent coordinator"

[project.scripts]
coordinator = "local_cli_coordinator.cli:main"
```

Add this to `src/local_cli_coordinator/__init__.py`:

```python
__version__ = "0.1.0"
```

Add this to `src/local_cli_coordinator/__main__.py`:

```python
from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
```

Add this to `src/local_cli_coordinator/cli.py`:

```python
import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coordinator")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("daemon")
    subparsers.add_parser("status")
    subparsers.add_parser("doctor")

    inbox = subparsers.add_parser("inbox")
    inbox_subparsers = inbox.add_subparsers(dest="inbox_command")
    inbox_subparsers.add_parser("scan")

    task = subparsers.add_parser("task")
    task_subparsers = task.add_subparsers(dest="task_command")
    task_subparsers.add_parser("list")
    task_subparsers.add_parser("show").add_argument("task_id")
    task_subparsers.add_parser("retry").add_argument("task_id")
    task_subparsers.add_parser("block").add_argument("task_id")

    agent = subparsers.add_parser("agent")
    agent_subparsers = agent.add_subparsers(dest="agent_command")
    agent_subparsers.add_parser("list")

    repo = subparsers.add_parser("repo")
    repo_subparsers = repo.add_subparsers(dest="repo_command")
    repo_subparsers.add_parser("list")

    subparsers.add_parser("logs").add_argument("task_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        print("Coordinator doctor")
        print("status: ok")
        return 0
    if args.command is None:
        parser.print_help()
        return 0
    print(f"{args.command}: command is registered")
    return 0
```

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```bash
python -m unittest tests/test_cli.py -v
```

Expected: PASS with two tests.

- [ ] **Step 5: Commit**

Run:

```bash
git add pyproject.toml src/local_cli_coordinator tests/helpers.py tests/test_cli.py
git commit -m "feat: scaffold coordinator CLI"
```

Expected: commit succeeds.

## Task 2: Models, SQLite Schema, And Migrations

**Files:**
- Create: `src/local_cli_coordinator/models.py`
- Create: `src/local_cli_coordinator/db.py`
- Create: `migrations/001_initial.sql`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write failing database tests**

Add this to `tests/test_db.py`:

```python
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, create_task, get_task, init_db, transition_task


class DatabaseTests(unittest.TestCase):
    def test_create_and_transition_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "coordinator.db"
            conn = connect(db_path)
            init_db(conn)

            task_id = create_task(
                conn,
                title="Add parser regression",
                repo="polymarket-weather-arb",
                source_path="tasks/inbox/parser.md",
                priority="normal",
                capabilities=["tests", "code"],
                goal="Add focused regression coverage.",
                acceptance_criteria=["pytest passes"],
                verification_commands=["uv run pytest tests/test_rules.py -q"],
            )

            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "ready")
            self.assertEqual(task["repo"], "polymarket-weather-arb")
            self.assertEqual(task["capabilities"], "tests,code")

            transition_task(conn, task_id, "running", "agent started")
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "running")

            events = conn.execute(
                "select new_state, note from events where task_id = ? order by id",
                (task_id,),
            ).fetchall()
            self.assertEqual(events[-1]["new_state"], "running")
            self.assertEqual(events[-1]["note"], "agent started")
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_db.py -v
```

Expected: FAIL because `local_cli_coordinator.db` does not exist.

- [ ] **Step 3: Add models**

Add this to `src/local_cli_coordinator/models.py`:

```python
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
```

- [ ] **Step 4: Add the initial migration**

Add this to `migrations/001_initial.sql`:

```sql
create table if not exists schema_migrations (
    version text primary key,
    applied_at text not null default current_timestamp
);

create table if not exists tasks (
    id text primary key,
    title text not null,
    repo text not null,
    state text not null,
    priority text not null,
    capabilities text not null,
    source_path text not null,
    goal text not null,
    acceptance_criteria text not null,
    verification_commands text not null,
    branch text not null default '',
    worktree_path text not null default '',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);

create table if not exists attempts (
    id integer primary key autoincrement,
    task_id text not null,
    agent_id text not null,
    command text not null,
    started_at text not null default current_timestamp,
    ended_at text,
    exit_code integer,
    log_path text not null default '',
    foreign key(task_id) references tasks(id)
);

create table if not exists events (
    id integer primary key autoincrement,
    task_id text not null,
    old_state text not null,
    new_state text not null,
    note text not null default '',
    created_at text not null default current_timestamp,
    foreign key(task_id) references tasks(id)
);

create table if not exists artifacts (
    id integer primary key autoincrement,
    task_id text not null,
    kind text not null,
    path text not null,
    created_at text not null default current_timestamp,
    foreign key(task_id) references tasks(id)
);

create table if not exists agents (
    id text primary key,
    capabilities text not null,
    max_concurrency integer not null,
    observed_successes integer not null default 0,
    observed_failures integer not null default 0
);

create table if not exists repos (
    id text primary key,
    path text not null,
    default_branch text not null,
    remote text not null,
    branch_prefix text not null,
    allow_push integer not null,
    merge_policy text not null
);
```

- [ ] **Step 5: Add database functions**

Add this to `src/local_cli_coordinator/db.py`:

```python
import sqlite3
import uuid
from pathlib import Path

from .models import TASK_STATES

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "migrations"


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
    applied = {
        row["version"]
        for row in conn.execute("select version from schema_migrations").fetchall()
    }
    for migration in sorted(migrations_dir.glob("*.sql")):
        if migration.name in applied:
            continue
        conn.executescript(migration.read_text())
        conn.execute("insert into schema_migrations(version) values (?)", (migration.name,))
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
```

- [ ] **Step 6: Run the database tests**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_db.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add migrations/001_initial.sql src/local_cli_coordinator/models.py src/local_cli_coordinator/db.py tests/test_db.py
git commit -m "feat: add coordinator ledger"
```

Expected: commit succeeds.

## Task 3: Configuration Loading

**Files:**
- Create: `src/local_cli_coordinator/config.py`
- Create: `config/agents.toml`
- Create: `config/repos.toml`
- Create: `config/policy.toml`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write configuration tests**

Add this to `tests/test_config.py`:

```python
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config import load_config


class ConfigTests(unittest.TestCase):
    def test_loads_agents_repos_and_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "agents.toml").write_text(textwrap.dedent("""
                [agents.codex]
                command = "codex exec --json {prompt_path}"
                capabilities = ["code", "tests"]
                max_concurrency = 2
            """).strip())
            (root / "config" / "repos.toml").write_text(textwrap.dedent("""
                [repos.demo]
                path = "/tmp/demo"
                default_branch = "main"
                remote = "origin"
                branch_prefix = "coord/"
                allow_push = true
                merge_policy = "push_branch_only"
                verify_commands = ["python -m unittest"]
            """).strip())
            (root / "config" / "policy.toml").write_text(textwrap.dedent("""
                [task_policy]
                require_single_repo = true
                require_acceptance_criteria = true
                require_verification_commands = true
                require_handoff_summary = true
                max_files_touched = 3
                max_expected_minutes = 30
                max_attempts = 3
                split_if_touches_multiple_subsystems = true
                split_if_research_and_code_are_mixed = true
            """).strip())

            config = load_config(root)

            self.assertEqual(config.agents["codex"].max_concurrency, 2)
            self.assertEqual(config.repos["demo"].merge_policy, "push_branch_only")
            self.assertEqual(config.policy.max_files_touched, 3)
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_config.py -v
```

Expected: FAIL because `local_cli_coordinator.config` does not exist.

- [ ] **Step 3: Add configuration models and loader**

Add this to `src/local_cli_coordinator/config.py`:

```python
from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class AgentConfig:
    id: str
    command: str
    capabilities: list[str]
    max_concurrency: int


@dataclass(frozen=True)
class RepoConfig:
    id: str
    path: Path
    default_branch: str
    remote: str
    branch_prefix: str
    allow_push: bool
    merge_policy: str
    verify_commands: list[str]


@dataclass(frozen=True)
class PolicyConfig:
    require_single_repo: bool
    require_acceptance_criteria: bool
    require_verification_commands: bool
    require_handoff_summary: bool
    max_files_touched: int
    max_expected_minutes: int
    max_attempts: int
    split_if_touches_multiple_subsystems: bool
    split_if_research_and_code_are_mixed: bool


@dataclass(frozen=True)
class CoordinatorConfig:
    agents: dict[str, AgentConfig]
    repos: dict[str, RepoConfig]
    policy: PolicyConfig


def _read_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_config(root: Path) -> CoordinatorConfig:
    config_dir = root / "config"
    agents_raw = _read_toml(config_dir / "agents.toml").get("agents", {})
    repos_raw = _read_toml(config_dir / "repos.toml").get("repos", {})
    policy_raw = _read_toml(config_dir / "policy.toml")["task_policy"]

    agents = {
        agent_id: AgentConfig(
            id=agent_id,
            command=str(raw["command"]),
            capabilities=list(raw.get("capabilities", [])),
            max_concurrency=int(raw.get("max_concurrency", 1)),
        )
        for agent_id, raw in agents_raw.items()
    }

    repos = {
        repo_id: RepoConfig(
            id=repo_id,
            path=Path(raw["path"]),
            default_branch=str(raw["default_branch"]),
            remote=str(raw.get("remote", "origin")),
            branch_prefix=str(raw.get("branch_prefix", "coord/")),
            allow_push=bool(raw.get("allow_push", False)),
            merge_policy=str(raw.get("merge_policy", "no_push")),
            verify_commands=list(raw.get("verify_commands", [])),
        )
        for repo_id, raw in repos_raw.items()
    }

    policy = PolicyConfig(
        require_single_repo=bool(policy_raw["require_single_repo"]),
        require_acceptance_criteria=bool(policy_raw["require_acceptance_criteria"]),
        require_verification_commands=bool(policy_raw["require_verification_commands"]),
        require_handoff_summary=bool(policy_raw["require_handoff_summary"]),
        max_files_touched=int(policy_raw["max_files_touched"]),
        max_expected_minutes=int(policy_raw["max_expected_minutes"]),
        max_attempts=int(policy_raw["max_attempts"]),
        split_if_touches_multiple_subsystems=bool(policy_raw["split_if_touches_multiple_subsystems"]),
        split_if_research_and_code_are_mixed=bool(policy_raw["split_if_research_and_code_are_mixed"]),
    )

    return CoordinatorConfig(agents=agents, repos=repos, policy=policy)
```

- [ ] **Step 4: Add example config files**

Add this to `config/agents.toml`:

```toml
[agents.echo]
command = "python -c \"from pathlib import Path; Path('agent-output.txt').write_text('done')\""
capabilities = ["code", "tests", "docs", "planner"]
max_concurrency = 1
```

Add this to `config/repos.toml`:

```toml
[repos.example]
path = "/tmp/coordinator-example-repo"
default_branch = "main"
remote = "origin"
branch_prefix = "coord/"
allow_push = false
merge_policy = "no_push"
verify_commands = ["python -m unittest"]
```

Add this to `config/policy.toml`:

```toml
[task_policy]
require_single_repo = true
require_acceptance_criteria = true
require_verification_commands = true
require_handoff_summary = true
max_files_touched = 3
max_expected_minutes = 30
max_attempts = 3
split_if_touches_multiple_subsystems = true
split_if_research_and_code_are_mixed = true
```

- [ ] **Step 5: Run configuration tests**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_config.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add config src/local_cli_coordinator/config.py tests/test_config.py
git commit -m "feat: load coordinator config"
```

Expected: commit succeeds.

## Task 4: Markdown Inbox Parser

**Files:**
- Create: `src/local_cli_coordinator/tasks.py`
- Create: `tests/test_tasks.py`
- Create: `tasks/inbox/.gitkeep`
- Create: `tasks/accepted/.gitkeep`
- Create: `tasks/generated/.gitkeep`
- Create: `tasks/blocked/.gitkeep`

- [ ] **Step 1: Write parser tests**

Add this to `tests/test_tasks.py`:

```python
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.tasks import parse_task_markdown, scan_inbox


class TaskParserTests(unittest.TestCase):
    def test_parses_markdown_task(self) -> None:
        content = textwrap.dedent("""
            # Task: Add regression coverage

            repo: polymarket-weather-arb
            priority: normal
            capabilities: [tests, code]
            verification: [uv run pytest tests/test_rules.py -q]

            ## Goal

            Add focused regression coverage.

            ## Acceptance Criteria

            - Adds tests for low temperature titles.
            - Keeps the change small.
        """).strip()

        task = parse_task_markdown(content, "tasks/inbox/parser.md")

        self.assertEqual(task.title, "Add regression coverage")
        self.assertEqual(task.repo, "polymarket-weather-arb")
        self.assertEqual(task.capabilities, ["tests", "code"])
        self.assertEqual(task.verification_commands, ["uv run pytest tests/test_rules.py -q"])
        self.assertEqual(len(task.acceptance_criteria), 2)

    def test_scan_inbox_returns_markdown_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inbox = root / "tasks" / "inbox"
            inbox.mkdir(parents=True)
            (inbox / "one.md").write_text("# Task: One\n\nrepo: demo\npriority: normal\ncapabilities: [code]\nverification: [python -m unittest]\n\n## Goal\n\nShip one.\n\n## Acceptance Criteria\n\n- Works.")

            tasks = scan_inbox(root)

            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].title, "One")
```

- [ ] **Step 2: Run parser tests and verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_tasks.py -v
```

Expected: FAIL because `local_cli_coordinator.tasks` does not exist.

- [ ] **Step 3: Implement Markdown parsing**

Add this to `src/local_cli_coordinator/tasks.py`:

```python
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
```

- [ ] **Step 4: Add task directories**

Run:

```bash
mkdir -p tasks/inbox tasks/accepted tasks/generated tasks/blocked
touch tasks/inbox/.gitkeep tasks/accepted/.gitkeep tasks/generated/.gitkeep tasks/blocked/.gitkeep
```

Expected: directories exist.

- [ ] **Step 5: Run parser tests**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_tasks.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/local_cli_coordinator/tasks.py tests/test_tasks.py tasks
git commit -m "feat: parse markdown task inbox"
```

Expected: commit succeeds.

## Task 5: Policy Rule Gate

**Files:**
- Create: `src/local_cli_coordinator/policy.py`
- Create: `tests/test_policy.py`

- [ ] **Step 1: Write policy tests**

Add this to `tests/test_policy.py`:

```python
import unittest

from local_cli_coordinator.config import PolicyConfig
from local_cli_coordinator.models import TaskDraft
from local_cli_coordinator.policy import check_changed_files, check_task_draft


def policy() -> PolicyConfig:
    return PolicyConfig(
        require_single_repo=True,
        require_acceptance_criteria=True,
        require_verification_commands=True,
        require_handoff_summary=True,
        max_files_touched=3,
        max_expected_minutes=30,
        max_attempts=3,
        split_if_touches_multiple_subsystems=True,
        split_if_research_and_code_are_mixed=True,
    )


class PolicyTests(unittest.TestCase):
    def test_accepts_small_task(self) -> None:
        task = TaskDraft(
            title="Small",
            repo="demo",
            priority="normal",
            capabilities=["code"],
            goal="Change one thing.",
            acceptance_criteria=["Test passes."],
            verification_commands=["python -m unittest"],
        )
        result = check_task_draft(task, policy())
        self.assertTrue(result.accepted)
        self.assertEqual(result.reasons, [])

    def test_rejects_task_without_acceptance_criteria(self) -> None:
        task = TaskDraft(
            title="Vague",
            repo="demo",
            priority="normal",
            capabilities=["code"],
            goal="Improve the project.",
            acceptance_criteria=[],
            verification_commands=["python -m unittest"],
        )
        result = check_task_draft(task, policy())
        self.assertFalse(result.accepted)
        self.assertIn("missing acceptance criteria", result.reasons)

    def test_rejects_too_many_changed_files(self) -> None:
        result = check_changed_files(
            ["a.py", "b.py", "c.py", "d.py"],
            policy(),
        )
        self.assertFalse(result.accepted)
        self.assertIn("changed file count 4 exceeds limit 3", result.reasons)
```

- [ ] **Step 2: Run policy tests and verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_policy.py -v
```

Expected: FAIL because `local_cli_coordinator.policy` does not exist.

- [ ] **Step 3: Implement policy checks**

Add this to `src/local_cli_coordinator/policy.py`:

```python
from dataclasses import dataclass

from .config import PolicyConfig
from .models import TaskDraft


@dataclass(frozen=True)
class PolicyResult:
    accepted: bool
    reasons: list[str]


def check_task_draft(task: TaskDraft, policy: PolicyConfig) -> PolicyResult:
    reasons: list[str] = []
    if policy.require_single_repo and not task.repo:
        reasons.append("missing repo")
    if policy.require_acceptance_criteria and not task.acceptance_criteria:
        reasons.append("missing acceptance criteria")
    if policy.require_verification_commands and not task.verification_commands:
        reasons.append("missing verification commands")
    if not task.goal.strip():
        reasons.append("missing goal")
    return PolicyResult(accepted=not reasons, reasons=reasons)


def check_changed_files(changed_files: list[str], policy: PolicyConfig) -> PolicyResult:
    reasons: list[str] = []
    if len(changed_files) > policy.max_files_touched:
        reasons.append(
            f"changed file count {len(changed_files)} exceeds limit {policy.max_files_touched}"
        )
    return PolicyResult(accepted=not reasons, reasons=reasons)
```

- [ ] **Step 4: Run policy tests**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_policy.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/local_cli_coordinator/policy.py tests/test_policy.py
git commit -m "feat: add task policy gate"
```

Expected: commit succeeds.

## Task 6: Git Worktree Operations

**Files:**
- Create: `src/local_cli_coordinator/gitops.py`
- Create: `tests/test_gitops.py`

- [ ] **Step 1: Write git operation tests**

Add this helper to `tests/helpers.py`:

```python
def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    run("git", "init", "-b", "main", cwd=path)
    run("git", "config", "user.email", "coordinator@example.local", cwd=path)
    run("git", "config", "user.name", "Coordinator Test", cwd=path)
    (path / "README.md").write_text("demo\n")
    run("git", "add", "README.md", cwd=path)
    result = run("git", "commit", "-m", "initial", cwd=path)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
```

Add this to `tests/test_gitops.py`:

```python
import tempfile
import unittest
from pathlib import Path

from helpers import init_git_repo
from local_cli_coordinator.gitops import collect_changed_files, create_worktree, diff_patch, is_git_repo


class GitOpsTests(unittest.TestCase):
    def test_create_worktree_and_collect_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            worktrees = root / "worktrees"
            init_git_repo(repo)

            worktree = create_worktree(
                repo_path=repo,
                worktrees_root=worktrees,
                task_id="task-abc",
                branch_name="coord/task-abc-demo",
            )

            self.assertTrue(is_git_repo(worktree))
            (worktree / "feature.txt").write_text("hello\n")
            self.assertEqual(collect_changed_files(worktree), ["feature.txt"])
            self.assertIn("feature.txt", diff_patch(worktree))
```

- [ ] **Step 2: Run git operation tests and verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_gitops.py -v
```

Expected: FAIL because `local_cli_coordinator.gitops` does not exist.

- [ ] **Step 3: Implement git operations**

Add this to `src/local_cli_coordinator/gitops.py`:

```python
from pathlib import Path
import subprocess


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def require_success(result: subprocess.CompletedProcess[str], action: str) -> None:
    if result.returncode != 0:
        raise RuntimeError(f"{action} failed: {result.stderr.strip()}")


def is_git_repo(path: Path) -> bool:
    result = git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return result.returncode == 0 and result.stdout.strip() == "true"


def create_worktree(
    *,
    repo_path: Path,
    worktrees_root: Path,
    task_id: str,
    branch_name: str,
) -> Path:
    worktree_path = worktrees_root / task_id
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    result = git(["worktree", "add", "-b", branch_name, str(worktree_path)], cwd=repo_path)
    require_success(result, "create worktree")
    return worktree_path


def collect_changed_files(worktree_path: Path) -> list[str]:
    result = git(["status", "--porcelain"], cwd=worktree_path)
    require_success(result, "collect changed files")
    files: list[str] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        files.append(line[3:])
    return sorted(files)


def diff_patch(worktree_path: Path) -> str:
    intent = git(["add", "--intent-to-add", "--all"], cwd=worktree_path)
    require_success(intent, "mark untracked files for diff")
    result = git(["diff", "--", "."], cwd=worktree_path)
    require_success(result, "collect diff")
    return result.stdout
```

- [ ] **Step 4: Run git operation tests**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_gitops.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/local_cli_coordinator/gitops.py tests/helpers.py tests/test_gitops.py
git commit -m "feat: manage task worktrees"
```

Expected: commit succeeds.

## Task 7: Generic CLI Agent Adapter

**Files:**
- Create: `src/local_cli_coordinator/agent.py`
- Create: `tests/test_agent.py`

- [ ] **Step 1: Write agent adapter tests**

Add this to `tests/test_agent.py`:

```python
import sys
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.agent import run_agent
from local_cli_coordinator.config import AgentConfig


class AgentRunnerTests(unittest.TestCase):
    def test_runs_configured_command_and_captures_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run"
            worktree.mkdir()
            run_dir.mkdir()
            prompt = run_dir / "prompt.md"
            prompt.write_text("write output")
            agent = AgentConfig(
                id="fake",
                command=f"{sys.executable} -c \"from pathlib import Path; Path('agent-output.txt').write_text('done')\"",
                capabilities=["code"],
                max_concurrency=1,
            )

            result = run_agent(agent, prompt, worktree, run_dir)

            self.assertEqual(result.exit_code, 0)
            self.assertTrue((worktree / "agent-output.txt").exists())
            self.assertTrue(result.log_path.exists())
```

- [ ] **Step 2: Run agent adapter tests and verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_agent.py -v
```

Expected: FAIL because `local_cli_coordinator.agent` does not exist.

- [ ] **Step 3: Implement the adapter**

Add this to `src/local_cli_coordinator/agent.py`:

```python
from dataclasses import dataclass
from pathlib import Path
import os
import shlex
import subprocess

from .config import AgentConfig


@dataclass(frozen=True)
class AgentRunResult:
    agent_id: str
    command: str
    exit_code: int
    log_path: Path


def run_agent(
    agent: AgentConfig,
    prompt_path: Path,
    worktree_path: Path,
    run_dir: Path,
) -> AgentRunResult:
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "agent.log"
    command = agent.command.format(prompt_path=str(prompt_path), worktree_path=str(worktree_path))
    env = os.environ.copy()
    env["COORDINATOR_AGENT_ID"] = agent.id
    env["COORDINATOR_PROMPT_PATH"] = str(prompt_path)
    env["COORDINATOR_WORKTREE_PATH"] = str(worktree_path)
    result = subprocess.run(
        shlex.split(command),
        cwd=worktree_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    log_path.write_text(result.stdout + result.stderr)
    return AgentRunResult(
        agent_id=agent.id,
        command=command,
        exit_code=result.returncode,
        log_path=log_path,
    )
```

- [ ] **Step 4: Run agent adapter tests**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_agent.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/local_cli_coordinator/agent.py tests/test_agent.py
git commit -m "feat: run configured CLI agents"
```

Expected: commit succeeds.

## Task 8: Verification Runner

**Files:**
- Create: `src/local_cli_coordinator/verify.py`
- Create: `tests/test_verify.py`

- [ ] **Step 1: Write verification tests**

Add this to `tests/test_verify.py`:

```python
import sys
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.verify import run_verification


class VerificationTests(unittest.TestCase):
    def test_verification_pass_and_fail_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run"
            worktree.mkdir()
            run_dir.mkdir()

            passed = run_verification([f"{sys.executable} -c \"print('ok')\""], worktree, run_dir)
            self.assertTrue(passed.passed)
            self.assertEqual(passed.results[0].exit_code, 0)

            failed = run_verification([f"{sys.executable} -c \"raise SystemExit(7)\""], worktree, run_dir)
            self.assertFalse(failed.passed)
            self.assertEqual(failed.results[0].exit_code, 7)
            self.assertTrue((run_dir / "verifier.log").exists())
```

- [ ] **Step 2: Run verification tests and verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_verify.py -v
```

Expected: FAIL because `local_cli_coordinator.verify` does not exist.

- [ ] **Step 3: Implement verification runner**

Add this to `src/local_cli_coordinator/verify.py`:

```python
from dataclasses import dataclass
from pathlib import Path
import shlex
import subprocess


@dataclass(frozen=True)
class CommandResult:
    command: str
    exit_code: int


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    results: list[CommandResult]
    log_path: Path


def run_verification(
    commands: list[str],
    worktree_path: Path,
    run_dir: Path,
) -> VerificationResult:
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "verifier.log"
    output: list[str] = []
    results: list[CommandResult] = []
    for command in commands:
        result = subprocess.run(
            shlex.split(command),
            cwd=worktree_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        output.append(f"$ {command}\n")
        output.append(result.stdout)
        output.append(result.stderr)
        results.append(CommandResult(command=command, exit_code=result.returncode))
        if result.returncode != 0:
            break
    log_path.write_text("".join(output))
    return VerificationResult(
        passed=all(result.exit_code == 0 for result in results),
        results=results,
        log_path=log_path,
    )
```

- [ ] **Step 4: Run verification tests**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_verify.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/local_cli_coordinator/verify.py tests/test_verify.py
git commit -m "feat: run verification commands"
```

Expected: commit succeeds.

## Task 9: Engine One-Task Flow Without Push

**Files:**
- Create: `src/local_cli_coordinator/engine.py`
- Modify: `src/local_cli_coordinator/gitops.py`
- Modify: `src/local_cli_coordinator/db.py`
- Create: `tests/test_engine.py`

- [ ] **Step 1: Write engine integration test**

Add this to `tests/test_engine.py`:

```python
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from helpers import init_git_repo
from local_cli_coordinator.config import AgentConfig, CoordinatorConfig, PolicyConfig, RepoConfig
from local_cli_coordinator.db import connect, create_task, get_task, init_db
from local_cli_coordinator.engine import run_one_ready_task


def test_config(repo_path: Path) -> CoordinatorConfig:
    return CoordinatorConfig(
        agents={
            "fake": AgentConfig(
                id="fake",
                command=f"{sys.executable} -c \"from pathlib import Path; Path('feature.txt').write_text('done')\"",
                capabilities=["code", "tests"],
                max_concurrency=1,
            )
        },
        repos={
            "demo": RepoConfig(
                id="demo",
                path=repo_path,
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=False,
                merge_policy="no_push",
                verify_commands=[f"{sys.executable} -c \"from pathlib import Path; assert Path('feature.txt').read_text() == 'done'\""],
            )
        },
        policy=PolicyConfig(
            require_single_repo=True,
            require_acceptance_criteria=True,
            require_verification_commands=True,
            require_handoff_summary=True,
            max_files_touched=3,
            max_expected_minutes=30,
            max_attempts=3,
            split_if_touches_multiple_subsystems=True,
            split_if_research_and_code_are_mixed=True,
        ),
    )


class EngineTests(unittest.TestCase):
    def test_runs_agent_verifies_and_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_task(
                conn,
                title="Create feature file",
                repo="demo",
                source_path="tasks/inbox/feature.md",
                priority="normal",
                capabilities=["code"],
                goal="Create feature.txt.",
                acceptance_criteria=["feature.txt contains done"],
                verification_commands=[],
            )

            processed = run_one_ready_task(conn, test_config(repo), root)

            self.assertTrue(processed)
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "done")
            self.assertTrue(task["branch"].startswith("coord/"))
            self.assertTrue(Path(task["worktree_path"]).exists())
```

- [ ] **Step 2: Run engine test and verify it fails**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_engine.py -v
```

Expected: FAIL because `local_cli_coordinator.engine` does not exist.

- [ ] **Step 3: Add git commit helper**

Append this to `src/local_cli_coordinator/gitops.py`:

```python
def commit_all(worktree_path: Path, message: str) -> str:
    add_result = git(["add", "--all"], cwd=worktree_path)
    require_success(add_result, "git add")
    commit_result = git(["commit", "-m", message], cwd=worktree_path)
    require_success(commit_result, "git commit")
    rev_result = git(["rev-parse", "HEAD"], cwd=worktree_path)
    require_success(rev_result, "read commit hash")
    return rev_result.stdout.strip()
```

- [ ] **Step 4: Add database update helpers**

Append this to `src/local_cli_coordinator/db.py`:

```python
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
```

- [ ] **Step 5: Implement engine one-task flow**

Add this to `src/local_cli_coordinator/engine.py`:

```python
from pathlib import Path
import re
import sqlite3

from .agent import run_agent
from .config import CoordinatorConfig
from .db import add_artifact, next_ready_task, set_task_branch_and_worktree, transition_task
from .gitops import collect_changed_files, commit_all, create_worktree, diff_patch
from .policy import check_changed_files
from .verify import run_verification


def _slug(text: str) -> str:
    lowered = text.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return cleaned[:40] or "task"


def _select_agent(config: CoordinatorConfig, capabilities: list[str]):
    required = set(capabilities)
    for agent in config.agents.values():
        if required.issubset(set(agent.capabilities)):
            return agent
    return next(iter(config.agents.values()))


def _write_prompt(task, run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt = run_dir / "prompt.md"
    prompt.write_text(
        f"# Task: {task['title']}\n\n"
        f"Repo: {task['repo']}\n\n"
        f"## Goal\n\n{task['goal']}\n\n"
        f"## Acceptance Criteria\n\n{task['acceptance_criteria']}\n"
    )
    return prompt


def run_one_ready_task(conn: sqlite3.Connection, config: CoordinatorConfig, root: Path) -> bool:
    task = next_ready_task(conn)
    if task is None:
        return False
    repo = config.repos[task["repo"]]
    capabilities = [part for part in task["capabilities"].split(",") if part]
    agent = _select_agent(config, capabilities)
    branch = f"{repo.branch_prefix}{task['id']}-{_slug(task['title'])}"
    run_dir = root / "runs" / task["id"]

    transition_task(conn, task["id"], "running", f"assigned to {agent.id}")
    worktree = create_worktree(
        repo_path=repo.path,
        worktrees_root=root / "worktrees" / repo.id,
        task_id=task["id"],
        branch_name=branch,
    )
    set_task_branch_and_worktree(conn, task["id"], branch, worktree)
    prompt = _write_prompt(task, run_dir)
    agent_result = run_agent(agent, prompt, worktree, run_dir)
    add_artifact(conn, task["id"], "agent_log", agent_result.log_path)
    if agent_result.exit_code != 0:
        transition_task(conn, task["id"], "failed", "agent command failed")
        return True

    changed_files = collect_changed_files(worktree)
    policy_result = check_changed_files(changed_files, config.policy)
    if not policy_result.accepted:
        transition_task(conn, task["id"], "needs_split", "; ".join(policy_result.reasons))
        return True

    patch_path = run_dir / "diff.patch"
    patch_path.write_text(diff_patch(worktree))
    add_artifact(conn, task["id"], "diff", patch_path)

    transition_task(conn, task["id"], "verifying", "running verification")
    commands = [line for line in task["verification_commands"].splitlines() if line] or repo.verify_commands
    verification = run_verification(commands, worktree, run_dir)
    add_artifact(conn, task["id"], "verifier_log", verification.log_path)
    if not verification.passed:
        transition_task(conn, task["id"], "failed", "verification failed")
        return True

    transition_task(conn, task["id"], "committing", "creating commit")
    commit_all(
        worktree,
        f"{task['title']}\n\nTask: {task['id']}\nAgent: {agent.id}",
    )
    transition_task(conn, task["id"], "done", "committed locally")
    return True
```

- [ ] **Step 6: Run engine tests**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_engine.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/local_cli_coordinator/engine.py src/local_cli_coordinator/gitops.py src/local_cli_coordinator/db.py tests/test_engine.py
git commit -m "feat: run one coordinated task"
```

Expected: commit succeeds.

## Task 10: Push And Merge Policies

**Files:**
- Modify: `src/local_cli_coordinator/gitops.py`
- Modify: `src/local_cli_coordinator/engine.py`
- Create: `tests/test_push_merge.py`

- [ ] **Step 1: Write push policy tests**

Add this to `tests/test_push_merge.py`:

```python
import tempfile
import unittest
from pathlib import Path

from helpers import init_git_repo, run
from local_cli_coordinator.gitops import commit_all, create_worktree, push_branch


class PushMergeTests(unittest.TestCase):
    def test_push_branch_to_bare_remote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            remote = root / "remote.git"
            worktrees = root / "worktrees"
            init_git_repo(repo)
            result = run("git", "init", "--bare", str(remote), cwd=root)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run("git", "remote", "add", "origin", str(remote), cwd=repo)
            self.assertEqual(result.returncode, 0, result.stderr)

            worktree = create_worktree(
                repo_path=repo,
                worktrees_root=worktrees,
                task_id="task-push",
                branch_name="coord/task-push-demo",
            )
            (worktree / "feature.txt").write_text("done\n")
            commit_all(worktree, "add feature")

            push_branch(worktree, "origin", "coord/task-push-demo")

            branches = run("git", "branch", "--list", "coord/task-push-demo", cwd=remote)
            self.assertIn("coord/task-push-demo", branches.stdout)
```

- [ ] **Step 2: Run push tests and verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_push_merge.py -v
```

Expected: FAIL because `push_branch` does not exist.

- [ ] **Step 3: Implement push and merge helpers**

Append this to `src/local_cli_coordinator/gitops.py`:

```python
def push_branch(worktree_path: Path, remote: str, branch_name: str) -> None:
    result = git(["push", remote, f"HEAD:{branch_name}"], cwd=worktree_path)
    require_success(result, "git push branch")


def merge_branch_to_default(repo_path: Path, branch_name: str, default_branch: str, remote: str) -> None:
    checkout = git(["checkout", default_branch], cwd=repo_path)
    require_success(checkout, "checkout default branch")
    pull = git(["pull", "--ff-only", remote, default_branch], cwd=repo_path)
    if pull.returncode != 0:
        raise RuntimeError(f"pull default branch failed: {pull.stderr.strip()}")
    merge = git(["merge", "--ff-only", branch_name], cwd=repo_path)
    require_success(merge, "merge task branch")
    push = git(["push", remote, default_branch], cwd=repo_path)
    require_success(push, "push default branch")
```

- [ ] **Step 4: Wire push policies into engine**

Modify the end of `run_one_ready_task` in `src/local_cli_coordinator/engine.py` after the `commit_all` call:

```python
    if repo.allow_push and repo.merge_policy != "no_push":
        from .gitops import merge_branch_to_default, push_branch

        transition_task(conn, task["id"], "pushing", "pushing task branch")
        push_branch(worktree, repo.remote, branch)
        if repo.merge_policy == "auto_merge_default_branch":
            transition_task(conn, task["id"], "merging", "merging into default branch")
            merge_branch_to_default(repo.path, branch, repo.default_branch, repo.remote)
    transition_task(conn, task["id"], "done", "completed")
    return True
```

Remove the earlier line:

```python
    transition_task(conn, task["id"], "done", "committed locally")
```

- [ ] **Step 5: Run push tests and engine tests**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_push_merge.py tests/test_engine.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/local_cli_coordinator/gitops.py src/local_cli_coordinator/engine.py tests/test_push_merge.py
git commit -m "feat: apply push and merge policies"
```

Expected: commit succeeds.

## Task 11: CLI Commands For Scan, Status, Tasks, Logs, And Daemon Once

**Files:**
- Modify: `src/local_cli_coordinator/cli.py`
- Modify: `src/local_cli_coordinator/db.py`
- Create: `tests/test_cli_commands.py`

- [ ] **Step 1: Write CLI command tests**

Add this to `tests/test_cli_commands.py`:

```python
import tempfile
import textwrap
import unittest
from pathlib import Path

from helpers import run_cli


class CliCommandTests(unittest.TestCase):
    def test_status_and_inbox_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "tasks" / "inbox").mkdir(parents=True)
            (root / "config" / "agents.toml").write_text("[agents.echo]\ncommand = \"python -c 'print(1)'\"\ncapabilities = [\"code\"]\nmax_concurrency = 1\n")
            (root / "config" / "repos.toml").write_text("[repos.demo]\npath = \"/tmp/demo\"\ndefault_branch = \"main\"\nremote = \"origin\"\nbranch_prefix = \"coord/\"\nallow_push = false\nmerge_policy = \"no_push\"\nverify_commands = [\"python -m unittest\"]\n")
            (root / "config" / "policy.toml").write_text("[task_policy]\nrequire_single_repo = true\nrequire_acceptance_criteria = true\nrequire_verification_commands = true\nrequire_handoff_summary = true\nmax_files_touched = 3\nmax_expected_minutes = 30\nmax_attempts = 3\nsplit_if_touches_multiple_subsystems = true\nsplit_if_research_and_code_are_mixed = true\n")
            (root / "tasks" / "inbox" / "one.md").write_text(textwrap.dedent("""
                # Task: One

                repo: demo
                priority: normal
                capabilities: [code]
                verification: [python -m unittest]

                ## Goal

                Do one thing.

                ## Acceptance Criteria

                - Works.
            """).strip())

            scan = run_cli("--root", str(root), "inbox", "scan")
            self.assertEqual(scan.returncode, 0, scan.stderr)
            self.assertIn("imported 1 task", scan.stdout)
            self.assertFalse((root / "tasks" / "inbox" / "one.md").exists())
            self.assertTrue((root / "tasks" / "accepted" / "one.md").exists())

            status = run_cli("--root", str(root), "status")
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn("ready: 1", status.stdout)
```

- [ ] **Step 2: Run CLI command tests and verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_cli_commands.py -v
```

Expected: FAIL because `--root` and `inbox scan` behavior are not implemented.

- [ ] **Step 3: Add database list helpers**

Append this to `src/local_cli_coordinator/db.py`:

```python
def task_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("select state, count(*) as count from tasks group by state").fetchall()
    return {row["state"]: int(row["count"]) for row in rows}


def list_tasks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("select * from tasks order by created_at, id").fetchall()
```

- [ ] **Step 4: Implement CLI command behavior**

Replace `src/local_cli_coordinator/cli.py` with:

```python
import argparse
from pathlib import Path

from .config import load_config
from .db import connect, create_task, get_task, init_db, list_tasks, task_counts, transition_task
from .engine import run_one_ready_task
from .tasks import scan_inbox


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coordinator")
    parser.add_argument("--root", default=".", help="coordinator project root")
    parser.add_argument("--db", default="coordinator.db", help="SQLite database path relative to root")
    subparsers = parser.add_subparsers(dest="command")

    daemon = subparsers.add_parser("daemon")
    daemon.add_argument("--once", action="store_true")

    subparsers.add_parser("status")
    subparsers.add_parser("doctor")

    inbox = subparsers.add_parser("inbox")
    inbox_subparsers = inbox.add_subparsers(dest="inbox_command")
    inbox_subparsers.add_parser("scan")

    task = subparsers.add_parser("task")
    task_subparsers = task.add_subparsers(dest="task_command")
    task_subparsers.add_parser("list")
    task_subparsers.add_parser("show").add_argument("task_id")
    task_subparsers.add_parser("retry").add_argument("task_id")
    task_subparsers.add_parser("block").add_argument("task_id")

    agent = subparsers.add_parser("agent")
    agent_subparsers = agent.add_subparsers(dest="agent_command")
    agent_subparsers.add_parser("list")

    repo = subparsers.add_parser("repo")
    repo_subparsers = repo.add_subparsers(dest="repo_command")
    repo_subparsers.add_parser("list")

    subparsers.add_parser("logs").add_argument("task_id")
    return parser


def _open(root: Path, db_name: str):
    conn = connect(root / db_name)
    init_db(conn)
    return conn


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    db_name = args.db

    if args.command == "doctor":
        print("Coordinator doctor")
        print(f"root: {root}")
        print("status: ok")
        return 0
    if args.command is None:
        parser.print_help()
        return 0

    conn = _open(root, db_name)

    if args.command == "inbox" and args.inbox_command == "scan":
        config = load_config(root)
        imported = 0
        for draft in scan_inbox(root):
            if draft.repo not in config.repos:
                raise SystemExit(f"repo is not allowlisted: {draft.repo}")
            create_task(
                conn,
                title=draft.title,
                repo=draft.repo,
                source_path=draft.source_path,
                priority=draft.priority,
                capabilities=draft.capabilities,
                goal=draft.goal,
                acceptance_criteria=draft.acceptance_criteria,
                verification_commands=draft.verification_commands,
            )
            source = root / draft.source_path
            accepted_dir = root / "tasks" / "accepted"
            accepted_dir.mkdir(parents=True, exist_ok=True)
            source.replace(accepted_dir / source.name)
            imported += 1
        print(f"imported {imported} task" + ("" if imported == 1 else "s"))
        return 0

    if args.command == "status":
        counts = task_counts(conn)
        if not counts:
            print("no tasks")
            return 0
        for state in sorted(counts):
            print(f"{state}: {counts[state]}")
        return 0

    if args.command == "task" and args.task_command == "list":
        for task in list_tasks(conn):
            print(f"{task['id']} {task['state']} {task['title']}")
        return 0

    if args.command == "task" and args.task_command == "show":
        task = get_task(conn, args.task_id)
        print(f"id: {task['id']}")
        print(f"state: {task['state']}")
        print(f"title: {task['title']}")
        print(f"repo: {task['repo']}")
        return 0

    if args.command == "task" and args.task_command == "retry":
        transition_task(conn, args.task_id, "ready", "manual retry")
        print(f"retried {args.task_id}")
        return 0

    if args.command == "task" and args.task_command == "block":
        transition_task(conn, args.task_id, "blocked", "manual block")
        print(f"blocked {args.task_id}")
        return 0

    if args.command == "daemon":
        config = load_config(root)
        processed = run_one_ready_task(conn, config, root)
        print("processed 1 task" if processed else "no ready tasks")
        return 0

    print(f"{args.command}: command is registered")
    return 0
```

- [ ] **Step 5: Run CLI command tests**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_cli_commands.py tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/local_cli_coordinator/cli.py src/local_cli_coordinator/db.py tests/test_cli_commands.py
git commit -m "feat: add coordinator CLI commands"
```

Expected: commit succeeds.

## Task 12: Planner Interface And Generated Tasks

**Files:**
- Modify: `src/local_cli_coordinator/engine.py`
- Modify: `src/local_cli_coordinator/tasks.py`
- Create: `tests/test_planner.py`

- [ ] **Step 1: Write planner output test**

Add this to `tests/test_planner.py`:

```python
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.tasks import write_generated_task
from local_cli_coordinator.models import TaskDraft


class PlannerTests(unittest.TestCase):
    def test_write_generated_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = TaskDraft(
                title="Generated small task",
                repo="demo",
                priority="normal",
                capabilities=["code"],
                goal="Change one file.",
                acceptance_criteria=["Verification passes."],
                verification_commands=["python -m unittest"],
            )

            path = write_generated_task(root, task)

            self.assertTrue(path.exists())
            content = path.read_text()
            self.assertIn("# Task: Generated small task", content)
            self.assertIn("repo: demo", content)
            self.assertIn("## Acceptance Criteria", content)
```

- [ ] **Step 2: Run planner tests and verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_planner.py -v
```

Expected: FAIL because `write_generated_task` does not exist.

- [ ] **Step 3: Implement generated task writer**

Append this to `src/local_cli_coordinator/tasks.py`:

```python
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
    path.write_text(
        f"# Task: {task.title}\n\n"
        f"repo: {task.repo}\n"
        f"priority: {task.priority}\n"
        f"capabilities: [{capabilities}]\n"
        f"verification: [{verification}]\n\n"
        f"## Goal\n\n{task.goal}\n\n"
        f"## Acceptance Criteria\n\n{acceptance}\n"
    )
    return path
```

- [ ] **Step 4: Add planner notes to engine without invoking external LLMs**

Append this function to `src/local_cli_coordinator/engine.py`:

```python
def queue_follow_up_task(root: Path, task_draft) -> Path:
    from .tasks import write_generated_task

    return write_generated_task(root, task_draft)
```

This gives later planner-capable agents a stable write path while keeping the MVP deterministic.

- [ ] **Step 5: Run planner tests**

Run:

```bash
PYTHONPATH=src python -m unittest tests/test_planner.py tests/test_tasks.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/local_cli_coordinator/tasks.py src/local_cli_coordinator/engine.py tests/test_planner.py
git commit -m "feat: write generated follow-up tasks"
```

Expected: commit succeeds.

## Task 13: Final Verification And README

**Files:**
- Create: `README.md`
- Modify: `docs/superpowers/specs/2026-06-16-local-cli-agent-coordinator-design.md` only if implementation reveals a verified correction.

- [ ] **Step 1: Add README**

Add this to `README.md`:

````markdown
# Local CLI Agent Coordinator

A local coordinator for running CLI agents against small, verified tasks.

## First Version

- Human tasks enter through `tasks/inbox/*.md`.
- Runtime state is stored in `coordinator.db`.
- Repositories must be listed in `config/repos.toml`.
- Agents are configured in `config/agents.toml`.
- Every task runs in its own git worktree and branch.
- Verification runs before commit and push.

## Quick Commands

```bash
PYTHONPATH=src python -m local_cli_coordinator doctor
PYTHONPATH=src python -m local_cli_coordinator inbox scan
PYTHONPATH=src python -m local_cli_coordinator status
PYTHONPATH=src python -m local_cli_coordinator daemon --once
```

## Task Format

```md
# Task: Small focused change

repo: example
priority: normal
capabilities: [code]
verification: [python -m unittest]

## Goal

Make one focused change.

## Acceptance Criteria

- Verification passes.
- The task stays within the configured file-change limit.
```
````

- [ ] **Step 2: Run all tests**

Run:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Expected: PASS.

- [ ] **Step 3: Run CLI doctor**

Run:

```bash
PYTHONPATH=src python -m local_cli_coordinator doctor
```

Expected output includes:

```text
Coordinator doctor
status: ok
```

- [ ] **Step 4: Inspect git status**

Run:

```bash
git status --short
```

Expected: only `README.md` and any intentional final documentation updates are uncommitted.

- [ ] **Step 5: Commit**

Run:

```bash
git add README.md
git commit -m "docs: add coordinator usage guide"
```

Expected: commit succeeds.

## Final Acceptance

Run:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m local_cli_coordinator doctor
git status --short
```

Expected:

- All tests pass.
- Doctor prints `status: ok`.
- `git status --short` is empty after the final commit.
