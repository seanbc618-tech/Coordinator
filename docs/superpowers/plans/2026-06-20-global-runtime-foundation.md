# Global Runtime Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build global Coordinator paths, project registration, a versioned Unix-socket protocol, single-instance Supervisor lifecycle, and safe migration primitives.

**Architecture:** Add focused Python modules beside the existing CLI and database layer. This phase does not run multiple project loops or ship the TUI; it establishes contracts that later phases consume while keeping all existing commands compatible.

**Tech Stack:** Python 3.11+, argparse, sqlite3, Unix domain sockets, dataclasses, unittest.

---

## Ownership and Order

- Claude Code: Tasks 1, 2, and 5.
- Grok: Tasks 3 and 4.
- Claude Code: Task 6 integration.
- Codex: review after Tasks 2, 4, and 6.
- All work starts from the accepted integration branch. Each task gets its own
  worktree and commit. Do not edit files owned by another in-progress task.

### Task 1: Resolve Global Runtime Paths

**Files:**
- Create: `src/local_cli_coordinator/runtime_paths.py`
- Create: `tests/test_runtime_paths.py`

- [ ] **Step 1: Write failing path tests**

~~~python
import stat
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from local_cli_coordinator.runtime_paths import RuntimePaths, resolve_runtime_paths


class RuntimePathTests(TestCase):
    def test_xdg_overrides_are_respected(self) -> None:
        env = {
            "XDG_CONFIG_HOME": "/tmp/cfg",
            "XDG_DATA_HOME": "/tmp/data",
            "XDG_STATE_HOME": "/tmp/state",
        }
        with patch.dict("os.environ", env, clear=True):
            paths = resolve_runtime_paths()
        self.assertEqual(paths.config_dir, Path("/tmp/cfg/coordinator"))
        self.assertEqual(paths.database, Path("/tmp/data/coordinator/coordinator.db"))
        self.assertEqual(paths.socket, Path("/tmp/state/coordinator/coordinator.sock"))

    def test_create_makes_private_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            paths = RuntimePaths(base / "config", base / "data", base / "state")
            paths.create()
            for directory in (paths.config_dir, paths.data_dir, paths.state_dir):
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
~~~

- [ ] **Step 2: Run the focused test and confirm import failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_runtime_paths -v`
Expected: FAIL because `runtime_paths` does not exist.

- [ ] **Step 3: Implement the immutable path contract**

~~~python
@dataclass(frozen=True)
class RuntimePaths:
    config_dir: Path
    data_dir: Path
    state_dir: Path

    @property
    def database(self) -> Path:
        return self.data_dir / "coordinator.db"

    @property
    def socket(self) -> Path:
        return self.state_dir / "coordinator.sock"

    @property
    def lock(self) -> Path:
        return self.state_dir / "supervisor.lock"

    def create(self) -> None:
        for directory in (self.config_dir, self.data_dir, self.state_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
~~~

`resolve_runtime_paths()` must use XDG variables when present and otherwise use
`~/.config/coordinator`, `~/.local/share/coordinator`, and
`~/.local/state/coordinator`. Add `COORDINATOR_HOME` as a test-only/operator
override that places config, data, and state below one directory.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_runtime_paths -v`
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/local_cli_coordinator/runtime_paths.py tests/test_runtime_paths.py
git commit -m "feat: add global Coordinator runtime paths"
~~~

### Task 2: Add the Project Registry

**Files:**
- Create: `migrations/008_projects.sql`
- Create: `src/local_cli_coordinator/projects.py`
- Create: `tests/test_projects.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write failing registry tests**

Test canonicalization from a repository subdirectory, rejection outside a Git
worktree, idempotent registration through symlinks, explicit confirmation, path
movement detection, and lookup by canonical root.

Use a frozen `ProjectDraft` containing canonical path, repo ID, default branch,
branch prefix, and verification commands. Expose `inspect_project(path)`,
`register_project(conn, draft, confirmed=<bool>)`, `find_project_by_path(conn,
path)`, and `list_projects(conn)` as the complete public registry API.

- [ ] **Step 2: Run tests and confirm missing migration/API failures**

Run: `PYTHONPATH=src python3 -m unittest tests.test_projects tests.test_db -v`
Expected: FAIL.

- [ ] **Step 3: Add migration 008**

Create a `projects` table with text primary key, unique canonical path, repo ID,
default branch, branch prefix, newline-separated verification commands, active
flag, created timestamp, and updated timestamp. Do not modify existing task tables
in this phase.

- [ ] **Step 4: Implement inspection and confirmed registration**

Use `git -C <path> rev-parse --show-toplevel` and
`git symbolic-ref refs/remotes/origin/HEAD` with a local-branch fallback.
Calling `register_project` with `confirmed=False` must raise
`PermissionError("project registration requires confirmation")`.

- [ ] **Step 5: Run focused and full database tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_projects tests.test_db -v`
Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add migrations/008_projects.sql src/local_cli_coordinator/projects.py tests/test_projects.py tests/test_db.py
git commit -m "feat: add confirmed project registry"
~~~

### Task 3: Define the Versioned Supervisor Protocol

**Files:**
- Create: `src/local_cli_coordinator/supervisor_protocol.py`
- Create: `tests/test_supervisor_protocol.py`

- [ ] **Step 1: Write failing codec tests**

Cover valid request/response/event round trips, unknown protocol versions, missing
request IDs, missing project IDs for project methods, malformed JSON, and event
cursor monotonicity.

~~~python
request = RequestEnvelope(
    protocol_version=1,
    request_id="req-1",
    project_id="project-a",
    method="project.status",
    params={},
)
self.assertEqual(decode_envelope(encode_envelope(request)), request)
~~~

- [ ] **Step 2: Run the test and confirm import failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_supervisor_protocol -v`
Expected: FAIL.

- [ ] **Step 3: Implement strict dataclasses and newline JSON codec**

Define `RequestEnvelope`, `ResponseEnvelope`, `EventEnvelope`,
`ProtocolError`, `encode_envelope`, and `decode_envelope`. Reject extra top-level
keys and messages larger than 1 MiB. Keep `PROTOCOL_VERSION = 1` in one place.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_supervisor_protocol -v`
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/local_cli_coordinator/supervisor_protocol.py tests/test_supervisor_protocol.py
git commit -m "feat: define Supervisor protocol envelopes"
~~~

### Task 4: Implement a Single-Instance Unix Socket Server

**Files:**
- Create: `src/local_cli_coordinator/supervisor_server.py`
- Create: `tests/test_supervisor_server.py`
- Modify: `src/local_cli_coordinator/locks.py`

- [ ] **Step 1: Write failing lifecycle tests**

Use a temporary socket and state directory. Verify start, ping, clean shutdown,
stale socket cleanup only when no process owns it, mode 0o600, a second server
being rejected, malformed request isolation, and protocol mismatch responses.

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_supervisor_server -v`
Expected: FAIL.

- [ ] **Step 3: Implement the server lifecycle**

Expose `SupervisorServer(paths, handler)`, `serve_forever()`,
`request_shutdown()`, and `send_request(socket_path, request, timeout=2.0)`.
`send_request` returns one validated `ResponseEnvelope` or raises a typed
transport/protocol error.

Use `socket.AF_UNIX`, one bounded thread per connected client, newline framing,
and existing lock helpers extended to support a supplied lock path. The built-in
methods are `system.ping` and `system.shutdown`.

- [ ] **Step 4: Run focused tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_supervisor_server tests.test_locks -v`
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/local_cli_coordinator/supervisor_server.py src/local_cli_coordinator/locks.py tests/test_supervisor_server.py
git commit -m "feat: add single-instance Supervisor server"
~~~

### Task 5: Build Safe Legacy Migration

**Files:**
- Create: `src/local_cli_coordinator/global_migration.py`
- Create: `tests/test_global_migration.py`

- [ ] **Step 1: Write failing migration tests**

Cover dry run, timestamped backup, copied database/config/logs, idempotent rerun,
schema validation failure, missing source directories, interrupted staging, and
preservation of the original source.

Define a frozen `MigrationResult` with status (`migrated`, `already_migrated`, or
`dry_run`), optional backup path, and copied paths. Expose
`migrate_legacy_root(source, paths, dry_run=<bool>)` as the only write entry point.

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_global_migration -v`
Expected: FAIL.

- [ ] **Step 3: Implement staged copy and validation**

Copy only known Coordinator paths: database, `config/`, `runs/`, `state/`, and
`tasks/`. Back up an existing destination before replacement. Validate the copied
database by opening it and running `init_db`. Write a migration marker containing
source path, source database hash, and completion time. Never delete the source.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_global_migration -v`
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/local_cli_coordinator/global_migration.py tests/test_global_migration.py
git commit -m "feat: add safe legacy state migration"
~~~

### Task 6: Add Administrative Supervisor Commands

**Files:**
- Modify: `src/local_cli_coordinator/cli.py`
- Modify: `src/local_cli_coordinator/__main__.py`
- Create: `tests/test_supervisor_cli.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing CLI tests**

Cover `coordinator supervisor start --foreground`, `status`, `stop`,
`project inspect <path>`, `project add <path> --yes`, refusal without `--yes`,
and all existing commands with explicit `--root`.

- [ ] **Step 2: Run tests and confirm parser failures**

Run: `PYTHONPATH=src python3 -m unittest tests.test_supervisor_cli tests.test_cli -v`
Expected: FAIL.

- [ ] **Step 3: Add command handlers**

Handlers must resolve global paths once, use `send_request` for status/stop, run
`SupervisorServer.serve_forever` only for foreground start, and render project
inspection without registering it. Keep current root-based commands unchanged.

- [ ] **Step 4: Document the phase-one administrative workflow**

Document global directories, project inspection/confirmation, foreground server
diagnostics, migration dry run, and the fact that no TUI ships in Phase 1.

- [ ] **Step 5: Run phase and regression verification**

Run:

~~~bash
PYTHONPATH=src python3 -m unittest tests.test_runtime_paths tests.test_projects tests.test_supervisor_protocol tests.test_supervisor_server tests.test_global_migration tests.test_supervisor_cli -v
PYTHONPATH=src python3 -m unittest discover -s tests -v
git diff --check
~~~

Expected: all tests PASS and no whitespace errors.

- [ ] **Step 6: Commit**

~~~bash
git add src/local_cli_coordinator/cli.py src/local_cli_coordinator/__main__.py tests/test_supervisor_cli.py README.md
git commit -m "feat: expose global Supervisor administration"
~~~

## Phase 1 Acceptance

- Run the focused and full test commands above.
- Start the Supervisor against a temporary COORDINATOR_HOME.
- Confirm a second start is rejected.
- Register a temporary Git repository only after explicit confirmation.
- Exercise ping and graceful shutdown over the Unix socket.
- Rehearse migration on a copy of the current database.
- Codex reviews scope and confirms no TUI or multi-project scheduler was added.
