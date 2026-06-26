# Phase 5.5b Operational UX — Design Spec

> **Status:** FROZEN for 5.5b implementation  
> **Prerequisite:** Phase 5.5a merged to `main` (`d279aaf`)  
> **Scope:** Wave B (log tail) + Wave C (`cancel` worker kill) + Wave E (safe cleanup/rollback/drain)

---

## 1. Goals

Complete deferred 5.5a items so operators can:

1. Tail worker/verifier logs during active runs (RPC + slash + push events).
2. Cancel running tasks without orphan subprocesses or corrupted artifacts.
3. Preview destructive repo/task operations with dry-run + confirm tokens.

---

## 2. Non-goals (5.5b)

- Web dashboard, remote clients, config editing in TUI
- Auto-purge worktrees on cancel (`--purge` deferred)
- Full git rewind to arbitrary commit (rollback = clean worktree to branch tip)

---

## 3. Wave B — Live log tail

### 3.1 `project.task.log` RPC

**Params:**

| Param | Required | Default | Rules |
|-------|----------|---------|-------|
| `task_id` | yes | — | Project-scoped |
| `kind` | no | `attempt` | `attempt` \| `verifier` \| `agent` |
| `offset` | no | `0` | Byte offset into artifact file |
| `max_bytes` | no | `65536` | Cap 64 KiB |

**Response:**

```json
{
  "task_id": "task-abc",
  "kind": "attempt",
  "offset": 0,
  "next_offset": 128,
  "content": "...",
  "eof": false,
  "truncated": false
}
```

**State gating:** Allow when state ∈ `running`, `verifying`, `reviewing_*`, or terminal (`done`, `failed`, `blocked`, `rejected`, `awaiting_human`). Reject `ready` / `needs_split` → `invalid_state`.

**Path safety:** Resolve log path only from `artifacts` table (`attempt_log`, `verifier_log`, `agent_log`). Reject paths outside registered artifacts → `artifact_not_found`.

### 3.2 Push vs poll (Gemini P1)

- **Primary:** Supervisor publishes `task.log.append` on stdout/stderr during worker execution (same payload shape as `task.output`: `{task_id, output}`).
- **Fallback:** TUI/CLI may poll `project.task.log` at ≥ 500ms; RPC rate-limited to **2 req/s per (project_id, task_id)** → `rate_limited`.

### 3.3 CLI slash

- `/task <id> log` → `project.task.log` (text mode prints tail; JSON/RPC return envelope).

---

## 4. Wave C — Cancel with worker termination

### 4.1 Protocol (Gemini P1)

Order of operations for `project.task.cancel`:

1. Look up active subprocess for `task_id` in `WorkerRegistry`.
2. Send **SIGTERM** to process group; wait **5s** grace.
3. If still alive, send **SIGKILL**.
4. Release task lease.
5. Transition non-terminal tasks → `failed` with note `cancelled by operator`.
6. Emit `task.updated` event. **Do not** delete worktree.

### 4.2 `WorkerRegistry`

- Thread-safe map `task_id → Popen`.
- `process.run_command` registers on start, unregisters in `finally`.
- Idempotent `terminate(task_id)` safe when no worker registered.

### 4.3 Guards

- `task_not_found`, `project_mismatch` unchanged from 5.5a.
- Cancel on terminal state: release lease if held, preserve state.

---

## 5. Wave E — Safe cleanup / rollback / drain

### 5.1 Confirm token pattern

```text
--dry-run (default)  → prints plan + confirm token
--apply --confirm <token>  → executes plan
```

Token = first 16 hex chars of `sha256(action + canonical_json(plan))`.

### 5.2 `coordinator repo cleanup-worktrees`

| Flag | Behavior |
|------|----------|
| (default) / `--dry-run` | List removable worktrees; print token |
| `--apply --confirm <token>` | Remove listed worktrees |
| `--force` | Allow dirty worktrees (still needs confirm on apply) |
| `--project <id>` | Filter to one project (global mode) |

Respects existing eligibility: task state `done` only (unless extended later). Logs destructive actions to `events` with `note` prefix `cleanup:`.

### 5.3 `coordinator task rollback <task-id>`

| Flag | Behavior |
|------|----------|
| (default) / `--dry-run` | Show worktree path + `git status --short` summary |
| `--apply --confirm <token>` | `git reset --hard` + `git clean -fd` in worktree |
| | Writes `events` row: `rollback applied` |

### 5.4 `coordinator supervisor drain`

| Flag | Behavior |
|------|----------|
| (default) / `--dry-run` | List active leases + `running` tasks; no kill |
| `--apply --confirm <token>` | Request supervisor shutdown + join workers (optional future) |

5.5b implements **dry-run listing only** for drain; apply is reserved.

### 5.5 Global vs legacy root

When `COORDINATOR_HOME` is set, commands use `resolve_runtime_paths()` + `load_config_for_paths()`; worktree root = `paths.data_dir`.

---

## 6. Error codes (additive)

| Code | When |
|------|------|
| `invalid_state` | log tail on `ready`; wrong approve/retry state |
| `artifact_not_found` | no registered log for kind |
| `rate_limited` | log tail RPC too fast |
| `confirm_required` | `--apply` without token |
| `confirm_mismatch` | wrong token |

---

## 7. Test plan (5.5b Gate)

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_phase5_5_log_tail \
  tests.test_phase5_5_cleanup \
  tests.test_phase5_5_task_control -v

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src \
  python3 -m unittest discover -s tests -q
```

---

## 8. Implementation order

1. `log_tail.py` + `project.task.log` + slash `/task log`
2. `worker_registry.py` + `cancel_task` enhancement + `task.log.append` push
3. `ops_safety.py` + CLI dry-run/apply for cleanup, rollback, drain
4. FakeSupervisor + TUI `task.log.append` reducer
5. Full suite

---

## 9. References

- 5.5a spec: `docs/superpowers/specs/2026-06-26-phase5-5-operational-ux-design.md`
- Plan: `docs/superpowers/plans/2026-06-26-phase5-5-operational-ux.md`
- Gemini P1: `docs/superpowers/handoffs/2026-06-26-phase5-4-gemini-review-result.md`