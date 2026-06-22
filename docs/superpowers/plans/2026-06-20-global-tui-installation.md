# Global Installation and End-to-End TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the global coordinator command, guided project onboarding, detached Supervisor lifecycle, migration, packaging, and multi-project end-to-end verification.

**Architecture:** Package the Phase 3 JavaScript bundle inside the Python distribution, make no-subcommand launch resolve the current Git project, and supervise one detached local process through Phase 1 lifecycle APIs.

**Tech Stack:** Python packaging, importlib.resources, Node bundle, argparse, subprocess, unittest, PTY integration tests.

---

## Ownership and Order

- Grok: primary implementer for Tasks 1 through 7, one focused commit per task.
- Claude Code: read-only adversarial review after each task; it does not patch
  production code. Rejected work returns to Grok for repair on the same branch.
- Codex: review after Tasks 3, 5, and 7.
- Start after Phases 1 through 3 are accepted.

### Task 1: Package and Locate the TUI Bundle

**Files:**
- Modify: `pyproject.toml`
- Create: `src/local_cli_coordinator/tui_bundle.py`
- Create: `tests/test_tui_bundle.py`
- Modify: `ui-tui/scripts/build.mjs`

- [ ] **Step 1: Write failing resource tests**

Build a wheel in a temporary directory, inspect its members, install it into a
temporary virtual environment, and assert `locate_tui_bundle()` returns the
bundled entry and matching protocol manifest without source checkout access.

- [ ] **Step 2: Run tests and confirm package-data failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_tui_bundle -v`
Expected: FAIL.

- [ ] **Step 3: Implement package resource lookup**

`tui_bundle.py` reads the manifest with `importlib.resources.files`, verifies
protocol version 1 and a SHA-256 hash, and returns an `as_file` context manager
for the JavaScript artifact. A missing or corrupt bundle raises
`TuiBundleError` with the exact rebuild/reinstall command.

- [ ] **Step 4: Configure build inclusion**

Add the bundle, manifest, source map policy, and THIRD_PARTY_NOTICES.md as package
data. Do not include node_modules, TypeScript test files, or the Hermes checkout.

- [ ] **Step 5: Run wheel and regression tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_tui_bundle -v`
Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add pyproject.toml src/local_cli_coordinator/tui_bundle.py tests/test_tui_bundle.py ui-tui/scripts/build.mjs
git commit -m "build: package Coordinator TUI bundle"
~~~

### Task 2: Add Guided Project Onboarding to the TUI

**Files:**
- Create: `ui-tui/src/components/ProjectOnboarding.tsx`
- Modify: `ui-tui/src/app.tsx`
- Modify: `ui-tui/src/slash.ts`
- Create: `ui-tui/src/__tests__/projectOnboarding.test.tsx`
- Modify: `src/local_cli_coordinator/supervisor_methods.py`
- Create: `tests/test_project_onboarding_methods.py`

- [ ] **Step 1: Write failing frontend and backend tests**

Verify inspect-only startup, rendering canonical path/default branch/verification/
push/merge/review/budget, keyboard accept/reject, no registration on rejection,
confirmed registration, and project movement requiring reconfirmation.

- [ ] **Step 2: Run tests and confirm missing methods/UI**

~~~bash
PYTHONPATH=src python3 -m unittest tests.test_project_onboarding_methods -v
npm test --prefix ui-tui -- projectOnboarding.test.tsx --run
~~~

Expected: FAIL.

- [ ] **Step 3: Add inspect and register methods**

`project.inspect` accepts a canonical path supplied by the launcher and returns a
draft without writes. `project.register` requires `confirmed: true` and rejects
draft fields that no longer match a fresh inspection.

- [ ] **Step 4: Render the one-time confirmation**

Use a full-width unframed confirmation view before chat. Display every policy
field from the server. Enter accepts; Esc rejects and exits. Do not provide an
"always trust parent directory" option.

- [ ] **Step 5: Run tests**

Run both commands from Step 2.
Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add ui-tui/src/components/ProjectOnboarding.tsx ui-tui/src/app.tsx ui-tui/src/slash.ts ui-tui/src/__tests__/projectOnboarding.test.tsx src/local_cli_coordinator/supervisor_methods.py tests/test_project_onboarding_methods.py
git commit -m "feat: confirm project onboarding in TUI"
~~~

### Task 3: Launch and Detach the Global Supervisor

**Files:**
- Create: `src/local_cli_coordinator/supervisor_process.py`
- Create: `tests/test_supervisor_process.py`
- Modify: `src/local_cli_coordinator/cli.py`

- [ ] **Step 1: Write failing process lifecycle tests**

Verify attach to an existing Supervisor, detached start when absent, simultaneous
start race producing one process, readiness timeout cleanup, stale PID handling,
log file location, graceful shutdown, and TUI detach leaving the process alive.

- [ ] **Step 2: Run tests and confirm missing lifecycle**

Run: `PYTHONPATH=src python3 -m unittest tests.test_supervisor_process -v`
Expected: FAIL.

- [ ] **Step 3: Implement controlled detached start**

`ensure_supervisor(paths)` first pings. If unavailable, it acquires a startup
lock, pings again, then starts:

~~~python
subprocess.Popen(
    [sys.executable, "-m", "local_cli_coordinator", "supervisor", "start", "--foreground"],
    stdin=subprocess.DEVNULL,
    stdout=log_handle,
    stderr=subprocess.STDOUT,
    start_new_session=True,
    close_fds=True,
)
~~~

Poll `system.ping` with a bounded timeout. Never shell-expand commands. Do not use
a shell profile, launch agent, cron entry, or network listener.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_supervisor_process tests.test_supervisor_server -v`
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/local_cli_coordinator/supervisor_process.py src/local_cli_coordinator/cli.py tests/test_supervisor_process.py
git commit -m "feat: manage detached local Supervisor"
~~~

### Task 4: Make No-Argument Coordinator Open the Current Project

**Files:**
- Create: `src/local_cli_coordinator/tui_launcher.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Create: `tests/test_tui_launcher.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing launcher tests**

Cover invocation from repository root and subdirectory, outside Git, missing Node,
existing Supervisor, absent Supervisor, registered project, unregistered project,
TUI exit code, forwarded terminal signals, and administrative subcommand parity.

- [ ] **Step 2: Run tests and confirm parser behavior is wrong**

Run: `PYTHONPATH=src python3 -m unittest tests.test_tui_launcher tests.test_cli -v`
Expected: FAIL.

- [ ] **Step 3: Implement the launcher**

Resolve the canonical Git root without changing global cwd. Ensure Supervisor,
locate the package bundle, then execute Node with explicit socket path, project
path, protocol version, and last project ID when registered. Use argv arrays and
inherit stdin/stdout/stderr for the TTY.

No arguments opens TUI. Any recognized subcommand keeps current CLI behavior.
Outside Git, print a concise error and exit 2.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_tui_launcher tests.test_cli -v`
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/local_cli_coordinator/tui_launcher.py src/local_cli_coordinator/cli.py tests/test_tui_launcher.py tests/test_cli.py
git commit -m "feat: open Coordinator TUI from current repo"
~~~

### Task 5: Integrate First-Run Legacy Migration

**Files:**
- Modify: `src/local_cli_coordinator/global_migration.py`
- Modify: `src/local_cli_coordinator/tui_launcher.py`
- Create: `tests/test_first_run_migration.py`

- [ ] **Step 1: Write failing first-run tests**

Use copied legacy fixtures. Verify detection, dry-run summary, explicit migration
confirmation, backup path, successful activation, rejection, interrupted recovery,
idempotent second launch, artifact path remapping, and original source retention.

- [ ] **Step 2: Run tests and confirm missing flow**

Run: `PYTHONPATH=src python3 -m unittest tests.test_first_run_migration -v`
Expected: FAIL.

- [ ] **Step 3: Add migration handshake**

Before starting Supervisor against empty global state, detect a legacy root from
`COORDINATOR_LEGACY_ROOT` or the installed development root. Print the migration
summary and require an interactive yes in the TUI/terminal. Non-interactive launch
must refuse migration unless an explicit administrative migrate command is used.

- [ ] **Step 4: Verify copied real schema**

Run migration against a temporary copy of the current Coordinator database, then
run `init_db`, project lookup, goal status, task counts, event lookup, and artifact
existence checks against the copy.

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_first_run_migration tests.test_global_migration -v`
Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add src/local_cli_coordinator/global_migration.py src/local_cli_coordinator/tui_launcher.py tests/test_first_run_migration.py
git commit -m "feat: migrate legacy Coordinator on first run"
~~~

### Task 6: Prove Three-Project End-to-End Operation

**Files:**
- Create: `tests/test_global_tui_e2e.py`
- Create: `scripts/soak_global_supervisor.py`

- [ ] **Step 1: Write the failing multi-PTY test**

Install the built wheel in a temporary environment, create three Git repositories,
start three PTYs with no-argument coordinator, register each, send goals, detach
one client, reconnect it, and assert event isolation and continued execution.

- [ ] **Step 2: Run test and confirm integration failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_global_tui_e2e -v`
Expected: FAIL until all packaging and lifecycle behavior is integrated.

- [ ] **Step 3: Add a deterministic soak harness**

The harness uses fake workers, fixed seeds, temporary global state, and 100
scheduler ticks. It records project scheduling order, max wait, duplicate task
IDs, event cursors, reconnect counts, and final leases as JSON.

- [ ] **Step 4: Run E2E and soak verification**

~~~bash
PYTHONPATH=src python3 -m unittest tests.test_global_tui_e2e -v
PYTHONPATH=src python3 scripts/soak_global_supervisor.py --projects 3 --ticks 100
~~~

Expected: E2E PASS; soak reports zero duplicate tasks, zero active leases at end,
strictly increasing cursors, and max runnable-project wait of two turns.

- [ ] **Step 5: Commit**

~~~bash
git add tests/test_global_tui_e2e.py scripts/soak_global_supervisor.py
git commit -m "test: prove global multi-project TUI operation"
~~~

### Task 7: Complete Installation and Operator Documentation

**Files:**
- Modify: `README.md`
- Create: `docs/install.md`
- Create: `docs/tui.md`
- Create: `docs/migration.md`
- Create: `docs/troubleshooting.md`

- [ ] **Step 1: Document the exact install and upgrade workflow**

Use the verified local tool command from the built-wheel test. Include uninstall,
TUI rebuild for developers, global directories, Supervisor status/stop, and
recovery from a corrupt bundle.

- [ ] **Step 2: Document daily TUI use**

Cover project onboarding, plain-language instructions, slash commands, activity
expansion, detach versus stop, multi-project terminals, fallback display, budgets,
and human-review states.

- [ ] **Step 3: Document migration and rollback**

Include backup discovery, dry run, confirmation, validation, rollback command,
and the guarantee that the old root is not automatically deleted.

- [ ] **Step 4: Run release verification**

~~~bash
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m build
git diff --check
~~~

Install the wheel into a fresh temporary environment and run
`coordinator supervisor status` plus a no-argument PTY smoke test.

Expected: all checks PASS.

- [ ] **Step 5: Commit**

~~~bash
git add README.md docs/install.md docs/tui.md docs/migration.md docs/troubleshooting.md
git commit -m "docs: explain global Coordinator TUI"
~~~

## Phase 4 Acceptance

- Install from a built wheel without PYTHONPATH.
- Launch from /Users/xiafan/polymarket-crypto-threshold and confirm onboarding or
  restoration uses its canonical path and existing policy.
- Open three project TUIs against one Supervisor.
- Detach every client and verify workers continue.
- Reconnect and verify missed output replay.
- Exercise pause, stop, and global shutdown.
- Rehearse migration and rollback on copied real state.
- Run the full release command set.
- Codex performs final scope, license, migration, concurrency, and UX acceptance.
