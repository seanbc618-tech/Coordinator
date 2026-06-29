# Phase 6D Claw-Inspired Operability Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the eight Claw-inspired operability features that make Coordinator easier to initialize, inspect, script, replay, and debug while preserving the existing single-Supervisor architecture.

**Architecture:** Keep the Phase 6 autonomous loop, Commander, and global Supervisor unchanged as the execution core. Add a thin operability layer around them: structured admin output, project initialization, config explanation, worker-state snapshots, event schema v2, mock-provider parity tests, permission-mode reporting, and a few high-signal slash commands. This phase must not create a second service, rewrite the TUI shell, or let diagnostics bypass existing safety policy.

**Tech Stack:** Python `unittest`, SQLite migration 016 mirrored in both migration roots, existing Supervisor RPC, existing CLI/TUI slash routing, TypeScript/Vitest for TUI formatting tests, clean-wheel smoke tests without `PYTHONPATH`.

---

## 0. Why Phase 6D Exists

Phase 6A-6C made Coordinator capable of evaluating, generating, admitting, and continuously running autonomous work. The next bottleneck is not raw autonomy. The bottleneck is trust.

The user needs to answer these questions quickly:

```text
Can this repo be onboarded safely?
Which config file won?
Which Supervisor am I connected to?
Which agent is missing or overloaded?
What did this worker know when it acted?
Which event explains this state?
Can CI test Commander behavior without spending model calls?
Can the TUI show the plan, scan findings, and jump targets without guesswork?
```

Phase 6D borrows useful ideas from Claw-style open-source agent tooling without copying its architecture:

- keep Coordinator Python-first;
- keep one global Supervisor;
- keep repo allowlist and per-repo safety;
- add machine-readable diagnostics and replayable evidence.

---

## 1. The Eight Features

1. **Machine-readable admin output**  
   Add `--json` support and typed error codes to `doctor`, `supervisor status`, `config`, `loop`, `loop run`, and selected prompt/status commands.

2. **`coordinator init` project bootstrap**  
   Let a user run `coordinator init` inside a repo to create or update the global config entries needed for Coordinator to manage that project.

3. **Permission modes and tool whitelist reporting**  
   Formalize read-only, workspace-write, and danger execution modes in config/status output, and expose which tools/commands each agent is allowed to use.

4. **Worker-state snapshots**  
   Persist per-attempt worker state snapshots so failed, cancelled, or successful tasks can be inspected without scraping logs.

5. **Canonical event schema v2**  
   Add a normalized event envelope with typed name, sequence, source, actor, severity, provenance, and terminal fingerprint while preserving existing event replay.

6. **Mock provider parity harness**  
   Add a deterministic fake Commander/worker provider contract test harness so CI can validate prompt/RPC behavior without live model calls.

7. **Advanced slash commands**  
   Add `/plan`, `/scan`, `/jump` and `/open` in CLI/TUI, backed by Supervisor methods instead of ad-hoc local parsing.

8. **Explainable config precedence**  
   Add `coordinator config explain --json` to show exactly which file/env/default produced each effective setting.

---

## 2. Non-Negotiable Contracts

- Do not add a new daemon or background service.
- Do not rewrite in Rust or replace the Hermes-derived TUI shell.
- Do not make `init` silently grant dangerous automation. It may scaffold allowlist/config entries, but autonomy stays off unless explicitly requested.
- Do not let `--json` include human-only prose that scripts must parse.
- Do not break current text output unless a test explicitly updates the contract.
- Do not store full secret values in state snapshots, events, or config explanation output.
- Do not use live model calls in the mock-provider parity gate.
- Do not introduce remote plugin/MCP sprawl in this phase.
- Gemini must review for false-green JSON tests, config precedence drift, event duplication, path traversal, secret leakage, and TUI commands that bypass Supervisor.

---

## 3. Ownership

| Role | Work |
| --- | --- |
| **Grok** | Main implementation. Owns Tasks 1-8, one coherent commit per task, plus final handoff. |
| **Gemini / .pi agent** | Adversarial review. Focus on machine-readable contracts, false-green tests, path safety, secret redaction, and state/event consistency. |
| **Codex** | Gate owner. Reviews Gate A/B/C/D/E/F and final clean-wheel smoke. |

Claude Code is not required for Phase 6D. If it is used at all, give it only doc drift or fixture cleanup, never core event/config/state logic.

---

## 4. File Map

Create:

- `migrations/016_operability_layer.sql`
- `src/local_cli_coordinator/migrations/016_operability_layer.sql`
- `src/local_cli_coordinator/admin_json.py`
- `src/local_cli_coordinator/init_project.py`
- `src/local_cli_coordinator/config_explain.py`
- `src/local_cli_coordinator/permission_modes.py`
- `src/local_cli_coordinator/worker_state.py`
- `src/local_cli_coordinator/event_schema_v2.py`
- `src/local_cli_coordinator/mock_provider.py`
- `tests/test_admin_json.py`
- `tests/test_init_project.py`
- `tests/test_config_explain.py`
- `tests/test_permission_modes.py`
- `tests/test_worker_state.py`
- `tests/test_event_schema_v2.py`
- `tests/test_mock_provider_parity.py`
- `tests/test_phase6d_operability_e2e.py`
- `docs/superpowers/handoffs/2026-06-28-phase6d-acceptance.md`
- `docs/superpowers/handoffs/2026-06-28-phase6d-gemini-review.md`

Modify:

- `src/local_cli_coordinator/cli.py`
- `src/local_cli_coordinator/cli_chat.py`
- `src/local_cli_coordinator/cli_config.py`
- `src/local_cli_coordinator/config.py`
- `src/local_cli_coordinator/config_runtime.py`
- `src/local_cli_coordinator/db.py`
- `src/local_cli_coordinator/engine.py`
- `src/local_cli_coordinator/project_runtime.py`
- `src/local_cli_coordinator/supervisor_methods.py`
- `src/local_cli_coordinator/supervisor_events.py`
- `src/local_cli_coordinator/supervisor_protocol.py`
- `src/local_cli_coordinator/task_control.py`
- `ui-tui/src/slash.ts`
- `ui-tui/src/slashDisplay.ts`
- `ui-tui/src/app.tsx` only if command routing needs a display hook
- `tests/test_cli.py`
- `tests/test_cli_prompt.py`
- `tests/test_supervisor_methods.py`
- `tests/test_tui_pty.py`
- `tests/test_tui_bundle.py`
- `tests/test_wheel_migrations.py`
- `docs/cli.md`
- `docs/tui.md`
- `docs/troubleshooting.md`

---

## 5. Data Model

Add migration `016_operability_layer.sql` in both migration roots. The two files must be byte-identical.

```sql
CREATE TABLE IF NOT EXISTS worker_state_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    task_id TEXT,
    attempt_id INTEGER,
    agent_id TEXT,
    run_id TEXT,
    state_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    redaction_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_worker_state_project_task
ON worker_state_snapshots(project_id, task_id, created_at);

CREATE TABLE IF NOT EXISTS supervisor_events_v2 (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    actor TEXT,
    severity TEXT NOT NULL DEFAULT 'info',
    provenance TEXT NOT NULL DEFAULT 'supervisor',
    terminal_fingerprint TEXT,
    payload TEXT NOT NULL,
    legacy_cursor INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_supervisor_events_v2_project_seq
ON supervisor_events_v2(project_id, seq);

CREATE INDEX IF NOT EXISTS idx_supervisor_events_v2_name
ON supervisor_events_v2(name, created_at);
```

Allowed `state_type`: `pre_prompt`, `post_attempt`, `failure`, `cancellation`, `handoff`.

Allowed event `severity`: `debug`, `info`, `warn`, `error`.

Allowed `provenance`: `supervisor`, `commander`, `worker`, `evaluator`, `operator`, `tui`, `cli`.

---

## 6. Public Contracts

### 6.1 Admin JSON Envelope

All new `--json` admin output must use this shape:

```python
{
    "ok": True,
    "command": "doctor",
    "schema_version": 1,
    "generated_at": "2026-06-28T12:00:00Z",
    "data": {},
    "warnings": [],
    "errors": [],
}
```

On failure:

```python
{
    "ok": False,
    "command": "supervisor.status",
    "schema_version": 1,
    "generated_at": "2026-06-28T12:00:00Z",
    "data": {},
    "warnings": [],
    "errors": [
        {
            "code": "supervisor_not_running",
            "message": "Supervisor is not running",
            "hint": "Run `coordinator supervisor start`."
        }
    ],
}
```

Text output may stay friendly. JSON output must be stable and tested by keys, not substring matching.

### 6.2 `coordinator init`

The command:

```bash
coordinator init
coordinator init --repo-id polymarket --autonomy off
coordinator init --repo-id polymarket --verify "uv run pytest"
coordinator init --dry-run --json
```

Required behavior:

- resolves the current git root;
- refuses non-git directories unless `--path` points at a git repo;
- writes or updates global config under `RuntimePaths.config_dir`;
- adds a repo allowlist entry;
- creates minimal `agents.toml`, `repos.toml`, and `policy.toml` if missing;
- never enables autonomy by default;
- is idempotent;
- supports `--dry-run`;
- supports `--json`;
- never overwrites existing custom agent commands without `--yes`.

### 6.3 Permission Modes

Add config parsing for:

```toml
[policy.permissions]
default_mode = "workspace-write"
danger_requires_confirmation = true

[agents.claude_worker.permissions]
mode = "workspace-write"
allowed_tools = ["read", "edit", "shell:test", "shell:lint"]
denied_tools = ["shell:push", "shell:merge"]
```

If absent, default to existing safe behavior:

- commander: `read-only`;
- reviewer/evaluator: `read-only`;
- worker: `workspace-write`;
- merge/push actions remain governed by existing repo policy.

This phase reports and records permission policy. It does not need to sandbox external CLIs beyond existing command templates.

### 6.4 Worker-State Snapshot

Each worker attempt should write at least one `post_attempt` snapshot with:

```python
{
    "task_id": "task-...",
    "agent_id": "grok_worker",
    "attempt": 1,
    "command": ["grok", "..."],
    "cwd": "/repo/worktree",
    "exit_code": 0,
    "timed_out": False,
    "changed_files": ["src/example.py"],
    "verification": {
        "commands": ["uv run pytest"],
        "result": "passed"
    },
    "log_path": "/.../agent.log"
}
```

Redaction rules:

- remove environment values;
- replace token-like substrings with `[REDACTED]`;
- store command arguments that are already visible in logs, but never store full prompt text if it contains `@file` content.

### 6.5 Event Schema v2

The existing `supervisor_events` table remains authoritative for replay compatibility during this phase. Event v2 mirrors newly published events and gives operators a richer view.

Canonical names:

- `project.registered`
- `goal.created`
- `goal.activated`
- `chat.received`
- `commander.started`
- `commander.completed`
- `backlog.generated`
- `task.created`
- `task.started`
- `task.completed`
- `task.failed`
- `task.cancelled`
- `loop.iteration`
- `run.started`
- `run.stopped`
- `diagnostic.warning`

Every v2 event must have a monotonic `seq` per project and, when possible, a `legacy_cursor` linking back to `supervisor_events.cursor`.

### 6.6 Mock Provider Parity Harness

Add deterministic fake provider support for Commander and workers:

```bash
coordinator mock-provider run commander --fixture tests/fixtures/commander/one-task.json
coordinator mock-provider run worker --fixture tests/fixtures/worker/success.json
```

The harness must validate:

- fixture schema;
- command rendering;
- prompt file exists;
- output conforms to Commander schema v2 or worker result expectations;
- no network or live model binary is required.

### 6.7 Advanced Slash Commands

Add these commands:

```text
/plan
/scan
/jump <target>
/open <target>
```

Contracts:

- `/plan` shows active goal, next autonomous decision, current backlog, and running tasks.
- `/scan` runs read-only project diagnostics and reports repo cleanliness, configured verify commands, missing binaries, and recent failed tasks.
- `/jump <task-id|goal|log|worktree>` returns a path or command hint. In TUI it displays the resolved target; it does not launch external editors in this phase.
- `/open <target>` is an alias of `/jump` for now, to avoid surprising side effects.

All four commands must call Supervisor RPC methods, not local-only TUI parsing.

### 6.8 Config Explain

Add:

```bash
coordinator config explain
coordinator config explain --json
coordinator config explain policy.max_tasks_per_day --json
```

Each setting must show:

- effective value;
- source kind: `default`, `config_file`, `environment`, `computed`;
- source path or env var name;
- whether it was redacted;
- short explanation.

Secret-looking values must be redacted in both text and JSON.

---

## 7. Task Breakdown

### Task 0 — Grok: Red Tests for All Eight Features

**Files:**

- Create: `tests/test_admin_json.py`
- Create: `tests/test_init_project.py`
- Create: `tests/test_config_explain.py`
- Create: `tests/test_permission_modes.py`
- Create: `tests/test_worker_state.py`
- Create: `tests/test_event_schema_v2.py`
- Create: `tests/test_mock_provider_parity.py`
- Create: `tests/test_phase6d_operability_e2e.py`
- Modify: `tests/test_tui_pty.py`

- [ ] **Step 1: Add JSON admin red tests**

Add tests with these exact names:

- `test_doctor_json_uses_admin_envelope`
- `test_supervisor_status_json_reports_not_running_with_code`
- `test_loop_status_json_contains_project_run_and_next_decision`

Expected pre-implementation failure: `--json` is rejected or text output is emitted.

- [ ] **Step 2: Add init red tests**

Add tests:

- `test_init_creates_minimal_global_config_for_current_git_repo`
- `test_init_is_idempotent`
- `test_init_dry_run_json_does_not_write`
- `test_init_refuses_non_git_directory`
- `test_init_does_not_enable_autonomy_by_default`

- [ ] **Step 3: Add config explain and permission red tests**

Add tests:

- `test_config_explain_reports_source_for_policy_value`
- `test_config_explain_json_redacts_secret_like_values`
- `test_permission_modes_default_roles_are_safe`
- `test_agent_allowed_tools_are_reported_in_config_json`

- [ ] **Step 4: Add worker-state and event-v2 red tests**

Add tests:

- `test_worker_attempt_writes_post_attempt_snapshot`
- `test_worker_snapshot_redacts_environment_secrets`
- `test_event_v2_mirrors_task_created_with_legacy_cursor`
- `test_event_v2_sequence_is_monotonic_per_project`

- [ ] **Step 5: Add mock-provider and slash red tests**

Add tests:

- `test_mock_commander_fixture_validates_schema_without_live_binary`
- `test_mock_worker_fixture_generates_deterministic_agent_log`
- `test_plan_slash_uses_supervisor_rpc`
- `test_scan_slash_reports_read_only_diagnostics`
- `test_jump_slash_resolves_task_log_without_opening_editor`

- [ ] **Step 6: Run focused red tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_admin_json \
  tests.test_init_project \
  tests.test_config_explain \
  tests.test_permission_modes \
  tests.test_worker_state \
  tests.test_event_schema_v2 \
  tests.test_mock_provider_parity \
  tests.test_phase6d_operability_e2e -v
```

Expected: fail for missing modules, missing CLI flags, or unsupported Supervisor methods.

- [ ] **Step 7: Commit red tests**

```bash
git add tests/test_admin_json.py tests/test_init_project.py tests/test_config_explain.py tests/test_permission_modes.py tests/test_worker_state.py tests/test_event_schema_v2.py tests/test_mock_provider_parity.py tests/test_phase6d_operability_e2e.py tests/test_tui_pty.py
git commit -m "test: capture Phase 6D operability contracts"
```

**Gate A — Codex:** Red tests must fail for real missing behavior, not because fixtures are invalid or imports are wrong.

---

### Task 1 — Grok: Admin JSON Envelope and Typed Error Codes

**Files:**

- Create: `src/local_cli_coordinator/admin_json.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Modify: `src/local_cli_coordinator/cli_chat.py`
- Modify: `src/local_cli_coordinator/supervisor_process.py`
- Test: `tests/test_admin_json.py`
- Test: `tests/test_cli_prompt.py`

- [ ] **Step 1: Implement admin envelope helpers**

Create `admin_json.py` with:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AdminError:
    code: str
    message: str
    hint: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


def envelope(
    *,
    command: str,
    ok: bool,
    data: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[AdminError] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "command": command,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": data or {},
        "warnings": warnings or [],
        "errors": [error.to_dict() for error in errors or []],
    }


def print_envelope(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
```

- [ ] **Step 2: Add `--json` parser support**

Add `--json` to `doctor`, `supervisor status`, `config`, `loop`, `loop run`, and `coordinator -p/--print` status-style paths. For commands that already have `--mode json`, keep it and normalize it through the same envelope when it is admin/status output.

- [ ] **Step 3: Return typed errors**

Use codes:

- `supervisor_not_running`
- `missing_config_file`
- `project_not_registered`
- `unsupported_method`
- `invalid_project`
- `loop_not_active`

- [ ] **Step 4: Run focused tests**

```bash
PYTHONPATH=src python3 -m unittest tests.test_admin_json tests.test_cli_prompt -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/local_cli_coordinator/admin_json.py src/local_cli_coordinator/cli.py src/local_cli_coordinator/cli_chat.py src/local_cli_coordinator/supervisor_process.py tests/test_admin_json.py tests/test_cli_prompt.py
git commit -m "feat: add machine-readable admin output"
```

---

### Task 2 — Grok: `coordinator init`

**Files:**

- Create: `src/local_cli_coordinator/init_project.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Modify: `src/local_cli_coordinator/config_runtime.py`
- Test: `tests/test_init_project.py`
- Docs: `docs/cli.md`

- [ ] **Step 1: Implement git-root discovery and repo-id derivation**

`init_project.py` must expose:

```python
def discover_repo_root(path: Path) -> Path:
    """Return the git root for path or raise InitProjectError."""


def derive_repo_id(repo_root: Path) -> str:
    """Return a stable lowercase id from the directory name."""
```

`derive_repo_id(Path("/Users/xiafan/polymarket-crypto-threshold"))` should return `polymarket_crypto_threshold`.

- [ ] **Step 2: Implement dry-run plan**

Expose:

```python
def build_init_plan(paths, *, repo_root: Path, repo_id: str, verify_commands: list[str], autonomy_enabled: bool) -> dict:
    """Return the files and TOML sections that would be created or updated."""
```

The plan must include `agents.toml`, `repos.toml`, and `policy.toml`, with `autonomy_enabled = false` unless explicitly requested.

- [ ] **Step 3: Implement safe writes**

Write missing files. Update only the matching repo section in `repos.toml`. If an existing file contains non-default agent commands, preserve them.

- [ ] **Step 4: Wire CLI**

Add:

```bash
coordinator init [--path PATH] [--repo-id ID] [--verify CMD] [--autonomy off|on] [--dry-run] [--json] [--yes]
```

- [ ] **Step 5: Run focused tests**

```bash
PYTHONPATH=src python3 -m unittest tests.test_init_project -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/local_cli_coordinator/init_project.py src/local_cli_coordinator/cli.py src/local_cli_coordinator/config_runtime.py tests/test_init_project.py docs/cli.md
git commit -m "feat: add safe project initialization"
```

**Gate B — Codex:** `init` must be idempotent, non-git safe, dry-run clean, and autonomy-off by default.

---

### Task 3 — Grok: Config Explain and Permission Modes

**Files:**

- Create: `src/local_cli_coordinator/config_explain.py`
- Create: `src/local_cli_coordinator/permission_modes.py`
- Modify: `src/local_cli_coordinator/config.py`
- Modify: `src/local_cli_coordinator/config_runtime.py`
- Modify: `src/local_cli_coordinator/cli_config.py`
- Test: `tests/test_config_explain.py`
- Test: `tests/test_permission_modes.py`
- Docs: `docs/cli.md`

- [ ] **Step 1: Add permission dataclasses**

Add to `permission_modes.py`:

```python
VALID_PERMISSION_MODES = {"read-only", "workspace-write", "danger"}

@dataclass(frozen=True)
class AgentPermissions:
    mode: str
    allowed_tools: tuple[str, ...] = ()
    denied_tools: tuple[str, ...] = ()
```

Validation must reject unknown modes and tool names containing newlines.

- [ ] **Step 2: Parse permissions**

Update config parsing so each agent gets permissions. Defaults:

- commander: read-only;
- reviewer/evaluator: read-only;
- worker: workspace-write.

- [ ] **Step 3: Implement config source explanation**

`config_explain.py` must expose:

```python
def explain_config(paths, *, key: str | None = None) -> list[dict]:
    """Return effective config values with source and redaction metadata."""
```

Redact keys containing `token`, `secret`, `password`, `api_key`, or `key`.

- [ ] **Step 4: Wire CLI**

`coordinator config` keeps existing summary. Add:

```bash
coordinator config --json
coordinator config explain
coordinator config explain --json
coordinator config explain policy.max_tasks_per_day --json
```

- [ ] **Step 5: Run focused tests**

```bash
PYTHONPATH=src python3 -m unittest tests.test_config_explain tests.test_permission_modes -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/local_cli_coordinator/config_explain.py src/local_cli_coordinator/permission_modes.py src/local_cli_coordinator/config.py src/local_cli_coordinator/config_runtime.py src/local_cli_coordinator/cli_config.py tests/test_config_explain.py tests/test_permission_modes.py docs/cli.md
git commit -m "feat: explain config precedence and permissions"
```

---

### Task 4 — Grok: Worker-State Snapshots

**Files:**

- Create: `migrations/016_operability_layer.sql`
- Create: `src/local_cli_coordinator/migrations/016_operability_layer.sql`
- Create: `src/local_cli_coordinator/worker_state.py`
- Modify: `src/local_cli_coordinator/db.py`
- Modify: `src/local_cli_coordinator/engine.py`
- Modify: `src/local_cli_coordinator/project_runtime.py`
- Modify: `src/local_cli_coordinator/task_control.py`
- Test: `tests/test_worker_state.py`
- Test: `tests/test_migration_mirror_sync.py`

- [ ] **Step 1: Add mirrored migration 016**

Use the SQL from section 5. Verify:

```bash
PYTHONPATH=src python3 -m unittest tests.test_migration_mirror_sync -v
```

- [ ] **Step 2: Implement snapshot writer**

`worker_state.py` must expose:

```python
def redact_worker_state(value: object) -> object:
    """Return JSON-safe state with secrets removed."""


def write_worker_state_snapshot(conn, *, project_id: str, task_id: str | None, attempt_id: int | None, agent_id: str | None, run_id: str | None, state_type: str, payload: dict) -> str:
    """Persist a redacted worker state snapshot and return its id."""


def list_worker_state_snapshots(conn, *, project_id: str, task_id: str | None = None, limit: int = 20) -> list[dict]:
    """Return recent snapshots newest first."""
```

- [ ] **Step 3: Write snapshots at attempt finish**

Hook `engine.py` and `project_runtime.py` so every task attempt records `post_attempt`, including failed launch and cancellation paths.

- [ ] **Step 4: Expose task detail**

Update `/task <id>` detail data so the newest snapshot id and path/log metadata can be shown.

- [ ] **Step 5: Run focused tests**

```bash
PYTHONPATH=src python3 -m unittest tests.test_worker_state tests.test_phase5_5_task_detail -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add migrations/016_operability_layer.sql src/local_cli_coordinator/migrations/016_operability_layer.sql src/local_cli_coordinator/worker_state.py src/local_cli_coordinator/db.py src/local_cli_coordinator/engine.py src/local_cli_coordinator/project_runtime.py src/local_cli_coordinator/task_control.py tests/test_worker_state.py tests/test_migration_mirror_sync.py
git commit -m "feat: persist worker state snapshots"
```

**Gate C — Codex:** Snapshot tests must prove no secret leakage and no missing snapshots on worker failure/cancel paths.

---

### Task 5 — Grok: Event Schema v2

**Files:**

- Create: `src/local_cli_coordinator/event_schema_v2.py`
- Modify: `src/local_cli_coordinator/supervisor_events.py`
- Modify: `src/local_cli_coordinator/supervisor_methods.py`
- Modify: `src/local_cli_coordinator/supervisor_protocol.py`
- Test: `tests/test_event_schema_v2.py`
- Test: `tests/test_supervisor_events.py`
- Test: `tests/test_supervisor_protocol.py`

- [ ] **Step 1: Implement v2 event model**

`event_schema_v2.py` must define:

```python
@dataclass(frozen=True)
class EventV2:
    project_id: str
    seq: int
    name: str
    source: str
    actor: str | None
    severity: str
    provenance: str
    terminal_fingerprint: str | None
    payload: dict[str, object]
    legacy_cursor: int | None
```

- [ ] **Step 2: Mirror newly published legacy events**

`EventBroker.publish()` should keep returning legacy cursor. After the legacy insert succeeds, create one v2 event in the same connection with:

- `seq = max(seq)+1` for the project;
- `legacy_cursor = cursor`;
- normalized `name` from the legacy event type.

- [ ] **Step 3: Add replay method**

Add Supervisor method:

```text
events.v2.replay
```

Params:

```json
{"after": 0, "limit": 100}
```

Result:

```json
{"events": [{"seq": 1, "name": "task.created", "...": "..."}]}
```

- [ ] **Step 4: Run focused tests**

```bash
PYTHONPATH=src python3 -m unittest tests.test_event_schema_v2 tests.test_supervisor_events tests.test_supervisor_protocol -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/local_cli_coordinator/event_schema_v2.py src/local_cli_coordinator/supervisor_events.py src/local_cli_coordinator/supervisor_methods.py src/local_cli_coordinator/supervisor_protocol.py tests/test_event_schema_v2.py tests/test_supervisor_events.py tests/test_supervisor_protocol.py
git commit -m "feat: mirror supervisor events into schema v2"
```

---

### Task 6 — Grok: Mock Provider Parity Harness

**Files:**

- Create: `src/local_cli_coordinator/mock_provider.py`
- Create: `tests/fixtures/commander/one-task.json`
- Create: `tests/fixtures/commander/goal-complete.json`
- Create: `tests/fixtures/worker/success.json`
- Create: `tests/fixtures/worker/failure.json`
- Modify: `src/local_cli_coordinator/cli.py`
- Modify: `src/local_cli_coordinator/commander_runner.py`
- Modify: `src/local_cli_coordinator/process.py`
- Test: `tests/test_mock_provider_parity.py`
- Test: `tests/test_commander_runner.py`

- [ ] **Step 1: Add fixture validator**

`mock_provider.py` must expose:

```python
def validate_commander_fixture(path: Path) -> dict:
    """Load and validate fixture against Commander schema v2."""


def validate_worker_fixture(path: Path) -> dict:
    """Load worker fixture and require exit_code, stdout, stderr, changed_files."""
```

- [ ] **Step 2: Add fake command renderer**

Support CLI:

```bash
coordinator mock-provider run commander --fixture tests/fixtures/commander/one-task.json
coordinator mock-provider run worker --fixture tests/fixtures/worker/success.json
```

The command prints the fixture output in the same stdout/stderr shape a real provider command would produce.

- [ ] **Step 3: Add config examples for tests**

Tests should be able to set agent command to:

```text
coordinator mock-provider run commander --fixture /abs/fixture.json
```

and run Commander without a live model binary.

- [ ] **Step 4: Run focused tests**

```bash
PYTHONPATH=src python3 -m unittest tests.test_mock_provider_parity tests.test_commander_runner -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/local_cli_coordinator/mock_provider.py src/local_cli_coordinator/cli.py src/local_cli_coordinator/commander_runner.py src/local_cli_coordinator/process.py tests/fixtures/commander/one-task.json tests/fixtures/commander/goal-complete.json tests/fixtures/worker/success.json tests/fixtures/worker/failure.json tests/test_mock_provider_parity.py tests/test_commander_runner.py
git commit -m "feat: add deterministic mock provider harness"
```

**Gate D — Gemini:** Review must prove tests do not call live `codex`, `grok`, `claude`, or network binaries.

---

### Task 7 — Grok: `/plan`, `/scan`, `/jump`, `/open`

**Files:**

- Modify: `src/local_cli_coordinator/supervisor_methods.py`
- Modify: `src/local_cli_coordinator/cli_chat.py`
- Modify: `ui-tui/src/slash.ts`
- Modify: `ui-tui/src/slashDisplay.ts`
- Test: `tests/test_supervisor_methods.py`
- Test: `tests/test_cli_prompt.py`
- Test: `tests/test_tui_pty.py`
- Test: `ui-tui/src/slashDisplay.test.ts`
- Docs: `docs/tui.md`
- Docs: `docs/cli.md`

- [ ] **Step 1: Add Supervisor RPCs**

Add methods:

- `project.plan`
- `project.scan`
- `project.jump`

`/open` uses `project.jump` and sets `alias="open"` in params.

- [ ] **Step 2: Implement `/plan`**

Return:

```json
{
  "goal": {"id": 1, "status": "active", "title": "..."},
  "run": {"status": "running", "last_decision": "wait"},
  "backlog": {"ready": 2, "blocked": 0},
  "tasks": {"running": 1, "failed": 0},
  "next": "wait for running task"
}
```

- [ ] **Step 3: Implement `/scan`**

Read-only checks:

- project registered;
- git root exists;
- working tree cleanliness summary;
- verify commands configured;
- configured agent binaries available;
- recent failed tasks count;
- active run status.

- [ ] **Step 4: Implement `/jump` and `/open`**

Supported targets:

- `task-...`
- `task-... log`
- `goal`
- `worktree`
- `supervisor.log`

Return a path/hint only. Do not call `open`, `code`, `cursor`, or editor commands.

- [ ] **Step 5: Wire CLI/TUI display**

`coordinator -p "/plan" --print` and TUI `/plan` should show concise text. `--mode json -p "/plan"` should return the admin JSON envelope with data.

- [ ] **Step 6: Run focused tests**

```bash
PYTHONPATH=src python3 -m unittest tests.test_supervisor_methods tests.test_cli_prompt tests.test_tui_pty -v
npm test --prefix ui-tui -- --run slashDisplay
```

Expected: pass.

- [ ] **Step 7: Rebuild bundle**

```bash
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_bundle -v
```

Expected: bundle manifest matches generated output.

- [ ] **Step 8: Commit**

```bash
git add src/local_cli_coordinator/supervisor_methods.py src/local_cli_coordinator/cli_chat.py ui-tui/src/slash.ts ui-tui/src/slashDisplay.ts ui-tui/src/slashDisplay.test.ts src/local_cli_coordinator/tui_bundle tests/test_supervisor_methods.py tests/test_cli_prompt.py tests/test_tui_pty.py tests/test_tui_bundle.py docs/tui.md docs/cli.md
git commit -m "feat: add planning scan and jump slash commands"
```

**Gate E — Codex:** Reject if `/scan`, `/jump`, or `/open` mutate files, spawn editors, or bypass Supervisor RPC.

---

### Task 8 — Grok: Integration, Docs, Clean-Wheel Acceptance

**Files:**

- Modify: `docs/cli.md`
- Modify: `docs/tui.md`
- Modify: `docs/troubleshooting.md`
- Create: `docs/superpowers/handoffs/2026-06-28-phase6d-acceptance.md`
- Create: `docs/superpowers/handoffs/2026-06-28-phase6d-gemini-review.md`

- [ ] **Step 1: Update docs**

Document:

- `coordinator init`;
- `--json` admin output;
- `config explain`;
- permission modes;
- worker-state snapshots;
- event v2 replay;
- mock-provider fixtures;
- `/plan`, `/scan`, `/jump`, `/open`.

- [ ] **Step 2: Run focused Phase 6D gate**

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_admin_json \
  tests.test_init_project \
  tests.test_config_explain \
  tests.test_permission_modes \
  tests.test_worker_state \
  tests.test_event_schema_v2 \
  tests.test_mock_provider_parity \
  tests.test_phase6d_operability_e2e -v
```

Expected: all pass.

- [ ] **Step 3: Run full Python gate**

```bash
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
```

Expected: all pass, no `ResourceWarning`.

- [ ] **Step 4: Run TUI gate**

```bash
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_bundle -v
```

Expected: all pass, bundle idempotent.

- [ ] **Step 5: Run packaging gate**

```bash
python3 -m build
PYTHONPATH=src python3 -m unittest tests.test_wheel_migrations -v
```

Expected: wheel builds and migrations are available from installed package.

- [ ] **Step 6: Run clean-wheel smoke**

In a fresh temp directory with no `PYTHONPATH`, install the built wheel and run:

```bash
coordinator init --dry-run --json
coordinator init --yes
coordinator config explain --json
coordinator doctor --json
coordinator mock-provider run commander --fixture /abs/path/to/tests/fixtures/commander/one-task.json
```

Expected:

- all JSON commands parse as valid JSON;
- `init --dry-run` writes nothing on a fresh `COORDINATOR_HOME`;
- `init --yes` materializes config before `config explain` / `doctor`;
- mock provider runs without live model binaries.

- [ ] **Step 7: Gemini review**

Ask Gemini to review:

- false-green JSON tests;
- `init` path traversal and accidental autonomy enablement;
- config precedence correctness;
- permission-mode defaults;
- worker-state secret redaction;
- event v2 duplicate/ordering bugs;
- mock-provider accidental live binary calls;
- slash commands bypassing Supervisor.

Write result to `docs/superpowers/handoffs/2026-06-28-phase6d-gemini-review.md`.

- [ ] **Step 8: Acceptance handoff**

Write `docs/superpowers/handoffs/2026-06-28-phase6d-acceptance.md` with:

- commit list;
- gate outputs;
- known P2s;
- manual smoke notes;
- explicit “Phase 6D PASS/FAIL” line.

- [ ] **Step 9: Commit docs**

```bash
git add docs/cli.md docs/tui.md docs/troubleshooting.md docs/superpowers/handoffs/2026-06-28-phase6d-acceptance.md docs/superpowers/handoffs/2026-06-28-phase6d-gemini-review.md
git commit -m "docs: record Phase 6D operability acceptance"
```

---

## 8. Codex Gates

### Gate A — Red-Test Quality

Run focused red tests after Task 0. Reject if tests pass before implementation or fail due invalid fixtures/import mistakes.

### Gate B — Init Safety

After Task 2, verify:

- `init` is idempotent;
- `init --dry-run --json` writes no files;
- non-git dirs are refused;
- autonomy stays disabled by default;
- existing custom agent commands are preserved.

### Gate C — State and Secrets

After Task 4, verify:

- every worker terminal path writes a snapshot;
- snapshots do not leak env secrets or prompt file contents;
- migration 016 is mirrored;
- old DBs migrate cleanly.

### Gate D — Mock Provider and Events

After Task 6, verify:

- v2 event sequence is monotonic per project;
- `legacy_cursor` links to old events;
- mock provider tests do not call live binaries or network;
- fixtures enforce Commander schema v2.

### Gate E — Slash Command Safety

After Task 7, verify:

- `/plan`, `/scan`, `/jump`, `/open` all call Supervisor RPC;
- `/scan` and `/jump` are read-only;
- `/open` does not spawn an editor in this phase;
- TUI bundle is rebuilt and idempotent.

### Gate F — Final Independent Sign-Off

After Task 8, Codex independently runs:

```bash
git diff --check
PYTHONPATH=src python3 -m unittest \
  tests.test_admin_json \
  tests.test_init_project \
  tests.test_config_explain \
  tests.test_permission_modes \
  tests.test_worker_state \
  tests.test_event_schema_v2 \
  tests.test_mock_provider_parity \
  tests.test_phase6d_operability_e2e -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_bundle tests.test_wheel_migrations -v
python3 -m build
```

Then perform clean-wheel smoke with no `PYTHONPATH`.

---

## 9. Expected User-Facing Result

After Phase 6D, a user should be able to run:

```bash
cd /Users/xiafan/polymarket-crypto-threshold
coordinator init --dry-run --json
coordinator init --repo-id polymarket_crypto_threshold --verify "uv run pytest" --yes
coordinator doctor --json
coordinator config explain --json
coordinator -p "/scan" --print
coordinator -p "/plan" --print
coordinator -p "/jump task-abc log" --print
```

And understand:

- whether the project is ready;
- which config source produced each behavior;
- which agents/tools are available;
- what the autonomous loop plans next;
- where evidence for a task lives;
- what happened in the event stream;
- how to reproduce a Commander/worker scenario without spending live model calls.

This is the phase that turns Coordinator from “a clever local automation loop” into something closer to an inspectable local agent operating system.

---

## 10. Self-Review

- All eight requested features are mapped to tasks and gates.
- No new service, Rust rewrite, or plugin ecosystem expansion is introduced.
- Safety-sensitive actions have explicit rejection criteria.
- The plan preserves existing Supervisor/RPC architecture.
- Tests are named concretely and designed to fail before implementation.
- Clean-wheel and no-`PYTHONPATH` verification remain mandatory.
