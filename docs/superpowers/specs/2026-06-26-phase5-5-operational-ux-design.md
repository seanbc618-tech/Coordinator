# Phase 5.5a Operational UX — Design Spec

> **Status:** FROZEN for 5.5a implementation  
> **Prerequisite:** Phase 5.4 merged to `main` (`417cd5e`)  
> **Scope:** Waves A + C (approve/retry only) + D (dashboard counts)  
> **Deferred to 5.5b:** live log tail, `project.task.cancel`, rollback/cleanup

---

## 1. Goals

Make daily operation feel like a **总管** (orchestrator):

1. Chat replies expose structured orchestration metadata (not just `user_reply`).
2. `project.task` returns scannable detail for TUI `/task` and headless RPC.
3. Operators can **approve** human gates and **retry** failed tasks via project-scoped RPC.
4. Global **dashboard** shows per-project counts without leaking cross-project titles.

---

## 2. Non-goals (5.5a)

- `project.task.cancel` (5.5b — requires worker signal/kill design)
- `project.task.log` tail / live polling (5.5b)
- Worktree rollback / cleanup extensions (5.5b)
- Web dashboard, remote clients, config editing in TUI

---

## 3. Wave A — Chat orchestration metadata

### 3.1 `chat.send` result shape

Add top-level `orchestration` object on successful `chat.send` (JSON, RPC, and TUI-internal mapping). Distinct from Phase 5.2 `user_reply` / schema v2 proposal fields.

```json
{
  "orchestration": {
    "admitted": 1,
    "rejected": 0,
    "next_action": "daemon will schedule task-abc",
    "blocking_reasons": [],
    "rejection_reasons": []
  }
}
```

| Field | Type | Rules |
|-------|------|-------|
| `admitted` | int | Count of tasks admitted this turn |
| `rejected` | int | Count of proposals rejected |
| `next_action` | string | Operator-facing hint; empty when idle |
| `blocking_reasons` | string[] | Why work is blocked (policy, human gate, circuit breaker) |
| `rejection_reasons` | string[] | Why proposals were rejected |

**Sources:** Commander proposal outcome in `supervisor_commander.py`; map existing `admitted`/`rejected` counts and engine notes.

### 3.2 CLI output

| Mode | Behavior |
|------|----------|
| `--mode json` | `orchestration` at top level of JSON object |
| `--mode rpc` | `orchestration` inside `result` of `ResponseEnvelope` |
| `--mode text` | One-line footer: `Next: …` when `next_action` non-empty |
| TUI | Compact footer under coordinator message (not raw JSON) |

### 3.3 Backward compatibility

Additive fields only. Clients ignoring `orchestration` continue to work.

---

## 4. Wave A — `project.task` detail v2

### 4.1 New / enriched fields

| Field | Type | Description |
|-------|------|-------------|
| `execution_policy` | object | From migration 013 (`allowed_stages`, etc.) |
| `context_manifest_summary` | object | `{file_count, total_bytes}` — no paths in summary |
| `latest_transition` | object | `{from, to, at, note}` from last `task_events` row |
| `failure_summary` | string | Bounded (≤ 200 chars) from last failure event |
| `failure_class` | string | Stable code: `policy`, `verify`, `timeout`, `human`, `unknown` |
| `human_review_required` | bool | `true` when status is `awaiting_human` |

### 4.2 Safety

- Project scope: reject `task_id` not owned by `project_id`.
- No secrets: redact context paths in manifest summary (counts only).
- Truncate long `goal` / `acceptance_criteria` in TUI; full text via RPC.

---

## 5. Wave C (partial) — approve & retry

### 5.1 `project.task.approve`

| Param | Required |
|-------|----------|
| `project_id` | yes |
| `task_id` | yes |

**Effect:** `awaiting_human` → `ready`. Emits `task.updated` event.

**Guards:**

- Reject if status ≠ `awaiting_human` → `invalid_state`
- Never calls `commit_all` or mutates git state
- Project scope enforced

### 5.2 `project.task.retry`

| Param | Required |
|-------|----------|
| `project_id` | yes |
| `task_id` | yes |

**Effect:** Parity with `coordinator task retry` / `_cmd_task_transition` → `ready`.

**Guards:**

- Allowed from `failed`, `blocked`, `rejected` only
- Respect `max_attempts` metadata → `retry_exhausted`
- Increment attempt counter in `task_events` note
- Project scope enforced

### 5.3 Deferred: `project.task.cancel`

Blocked until 5.5b spec defines worker signal/kill before lease release (Gemini P1).

### 5.4 TUI slash (5.5a)

- `/approve <task-id>` — no confirmation
- `/retry <task-id>` — no confirmation
- `/cancel` — **not implemented** in 5.5a (return helpful error pointing to 5.5b)

---

## 6. Wave D (partial) — `supervisor.dashboard`

### 6.1 Request

No `project_id` required (global read).

### 6.2 Response

```json
{
  "projects": [
    {
      "project_id": "proj-abc",
      "goal_status": "active",
      "task_counts": {"running": 1, "ready": 2, "failed": 0},
      "active_workers": 1,
      "last_tick_at": "2026-06-26T12:00:00Z"
    }
  ]
}
```

| Rule | Value |
|------|-------|
| Max projects | 32 |
| Sort | `updated_at` desc |
| **Forbidden** | Task titles, goal text, file paths in aggregate payload |

### 6.3 TUI

`/dashboard` slash shows table; selecting a row switches to existing project view.

---

## 7. Error codes (stable)

| Code | When |
|------|------|
| `invalid_state` | approve/retry on wrong status |
| `retry_exhausted` | max attempts reached |
| `task_not_found` | unknown task_id |
| `project_mismatch` | task not in project |
| `method_not_found` | pre-implementation / unknown RPC |

---

## 8. Test plan (5.5a Gate A)

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_phase5_5_chat_persona \
  tests.test_phase5_5_task_detail \
  tests.test_phase5_5_task_control \
  tests.test_phase5_5_dashboard -v

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
# Baseline 956+ all green when 5.5a tests pass
```

Cancel tests in `test_phase5_5_task_control.py` may remain red / skipped until 5.5b.

---

## 9. Implementation order (Grok)

1. `supervisor_methods.py` — `project.task` v2 fields
2. `supervisor_commander.py` + `cli_chat.py` — `orchestration` on `chat.send`
3. `supervisor_methods.py` — `project.task.approve`, `project.task.retry`
4. `supervisor_methods.py` — `supervisor.dashboard`
5. TUI bundle — footer, `/task`, `/approve`, `/retry`, `/dashboard`
6. Docs — `cli.md`, `troubleshooting.md` (Claude)

---

## 10. References

- Plan: `docs/superpowers/plans/2026-06-26-phase5-5-operational-ux.md`
- Kickoff: `docs/superpowers/handoffs/2026-06-26-phase5-5-planning-kickoff.md`
- Gemini 5.5b blockers: cancel race, log push — `2026-06-26-phase5-4-gemini-review-result.md`