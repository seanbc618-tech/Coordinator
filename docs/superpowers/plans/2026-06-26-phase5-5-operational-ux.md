# Phase 5.5 Operational UX — Plan Draft

> **Status:** DRAFT — for Gemini adversarial review, then Claude docs/tests, then design spec freeze.  
> **Prerequisite:** Phase 5.4 merged (`external/coordinator-global-tui` → `main`).  
> **Review routing:** **Gemini** → `docs/superpowers/handoffs/2026-06-26-phase5-4-gemini-review.md` · **Claude** red tests + docs → **Grok** impl → **Codex** Gate E.

**Goal:** Make Coordinator feel like a daily **总管** (orchestrator): readable tasks, live worker visibility, explicit task control, multi-project situational awareness, and safe rollback/cleanup — without replacing the Hermes/Ink TUI substrate or Supervisor socket boundary.

**Non-goals:** OS-level sandboxing, remote RPC clients, web dashboard, trading/dashboard code, config editing inside TUI.

**Tech stack:** Existing Python 3.13 engine + Supervisor RPC v1 + Hermes/Ink `ui-tui` bundle + `unittest` / vitest.

**Design doc:** *(to create after Gemini review)* `docs/superpowers/specs/2026-06-26-phase5-5-operational-ux-design.md`

---

## Problem statement (from real usage)

Phase 5.4 made the CLI headless-capable. Operators still report:

1. **Chat feels like a pipe, not a 总管** — replies lack orchestration framing (what was admitted, what is blocked, what needs human action).
2. **Task detail is hard to scan** — `project.task` exists but TUI layout buries goal, criteria, policy, and failure reason.
3. **Running workers are opaque** — engine writes logs under `runs/` but TUI shows little live tail during `running` / `verifying`.
4. **Lifecycle control is fragmented** — `coordinator task retry` exists in admin CLI; no first-class **approve / cancel / retry** in TUI or Supervisor RPC with project scope.
5. **Multi-project blindness** — global Supervisor runs many projects; no single **dashboard** view across projects.
6. **Cleanup is scary** — worktree removal and task rollback need explicit, dry-run-first commands.

---

## Ownership and merge order

| Role | Owns |
|------|------|
| **Claude Code** | Red tests, docs (`cli.md`, `troubleshooting.md`, TUI help text), E2E smoke scripts |
| **Grok** | Supervisor methods, engine hooks, TUI bundle wiring, CLI admin commands |
| **Gemini** | Adversarial review of spec + plan + safety matrix |
| **Codex** | Gate E after Task 12 integration |

Suggested wave gates:

```text
Gemini review (this draft)
  → Claude 0 (red tests)
  → Grok 1–3 (chat persona + task detail)
  → Codex Gate A
  → Grok 4–6 (live logs + task control RPC)
  → Claude 7 (docs + E2E)
  → Grok 8 (multi-project dashboard)
  → Grok 9 (safe cleanup/rollback)
  → Claude 10 (docs + regression)
  → Grok 11 (integration)
  → Codex Gate E
```

---

## Theme map → deliverables

| Theme | Operator outcome | Primary surface |
|-------|------------------|-----------------|
| **总管对话** | Commander replies structured: status, admitted/rejected, next action | TUI chat + JSON/RPC metadata |
| **任务可读** | One screen: title, goal, criteria, verify cmds, policy, last event | TUI `/task` + enriched `project.task` |
| **实时日志** | See worker/verifier tail while state is `running`/`verifying` | TUI panel + `project.task.log` RPC (tail) |
| **任务控制** | Approve human gate, cancel run, retry failed | Supervisor `project.task.*` + TUI actions |
| **多项目总览** | All projects: goal state, task counts, worker load | `supervisor.dashboard` + TUI home |
| **安全清理** | Dry-run rollback/cleanup with confirmation tokens | CLI `coordinator repo cleanup-* --dry-run` |

---

## Wave A — 总管对话 + 任务详情可读性

### Task 0: Red tests (Claude)

**Files:** `tests/test_phase5_5_chat_persona.py`, `tests/test_phase5_5_task_detail.py`

- [ ] Assert `chat.send` / JSON output includes `next_action`, `admitted_summary`, `rejection_reasons` when present.
- [ ] Assert `project.task` returns bounded fields: `execution_policy`, `context_manifest` summary, `latest_note`, `failure_class`.
- [ ] Assert TUI slash `/task <id>` formatter includes goal + acceptance criteria (fixture-level string tests in `ui-tui`).

### Task 1: Chat response enrichment (Grok)

**Files:** `supervisor_commander.py`, `cli_chat.py`, `tui_bundle` chat reducer

- [ ] Extend `chat.send` result with stable `orchestration` object:
  ```json
  {
    "admitted": 1,
    "rejected": 0,
    "next_action": "daemon will pick up task-abc",
    "blocking_reasons": []
  }
  ```
- [ ] TUI renders coordinator messages with compact orchestration footer (not raw JSON).
- [ ] JSON/RPC modes include same fields; text mode gets one-line summary.

### Task 2: Task detail schema v2 (Grok)

**Files:** `supervisor_methods.py`, `db.py`, `tui_bundle` slash display

- [ ] `project.task` adds: `execution_policy`, `latest_transition`, `failure_summary`, `human_review_required`.
- [ ] Format long text with truncation + "show more" in TUI (bounded lines).
- [ ] Link artifacts list with kinds: `attempt_log`, `verifier_log`, `diff`.

### Task 3: Docs (Claude)

- [ ] Update `docs/cli.md` with orchestration fields and `/task` behavior.

**Gate A:** Codex — project isolation on enriched RPC; no secret leakage in task detail.

---

## Wave B — Running worker 实时日志

### Task 4: Log tail RPC (Grok)

**Files:** `supervisor_methods.py`, `reporting.py`, new `log_tail.py`

- [ ] `project.task.log` params: `task_id`, `kind` (`attempt`|`verifier`|`agent`), `offset`, `max_bytes` (cap 64 KiB).
- [ ] Only allow tail when task state ∈ `running`, `verifying`, `reviewing_*`, or terminal (read-only).
- [ ] Project scope enforced; reject cross-project task ids.

### Task 5: TUI live tail panel (Grok)

**Files:** `ui-tui/src/*`, bundle rebuild

- [ ] When subscribed task enters `running`, poll `project.task.log` every 500ms (backoff to 2s).
- [ ] Show last N lines in footer or split pane; stop poll on state change.
- [ ] Ctrl+C / detach does not kill worker (existing invariant).

### Task 6: Tests (Claude)

- [ ] Fake worker emits delayed stdout; assert tail RPC returns incremental bytes.
- [ ] ResourceWarning: no leaked file handles on tail polling teardown.

**Gate B:** Codex — tail cannot read paths outside artifact registry.

---

## Wave C — Task approve / cancel / retry

### Task 7: Supervisor task mutations (Grok)

**Files:** `supervisor_methods.py`, `engine.py`, `db.py`

New methods (all require `project_id`):

| Method | Effect | Guards |
|--------|--------|--------|
| `project.task.approve` | `awaiting_human` → `ready` or merge path | human review only |
| `project.task.cancel` | `running`/* → `failed` or `blocked`; release lease | no silent data loss note required |
| `project.task.retry` | `failed`/`blocked` → `ready`; increment attempt metadata | respect `max_attempts` |

- [ ] Mirror semantics of existing `coordinator task retry` but project-scoped via RPC.
- [ ] Emit `task.updated` events on mutation.

### Task 8: TUI actions (Grok)

- [ ] `/approve <task-id>`, `/cancel <task-id>`, `/retry <task-id>` slash commands.
- [ ] Confirm prompt for cancel (`Cancel task N? [y/N]`).

### Task 9: Red / E2E tests (Claude)

**Files:** `tests/test_phase5_5_task_control.py`

- [ ] Cancel running task → lease released, state terminal.
- [ ] Approve without `awaiting_human` → stable error code.
- [ ] Retry respects policy caps.

**Gate C:** Codex — cancel cannot delete worktree without explicit `--purge` flag (defer purge to Wave D).

---

## Wave D — Multi-project Supervisor dashboard

### Task 10: `supervisor.dashboard` RPC (Grok)

**Files:** `supervisor_methods.py`, `supervisor_process.py`

- [ ] Returns per-project: `goal_status`, task counts by state, `active_workers`, `last_tick_at`.
- [ ] No `project_id` required on request (global read).
- [ ] Bounded: max 32 projects, stable sort by `updated_at`.

### Task 11: TUI dashboard view (Grok)

- [ ] `/dashboard` or startup summary when multiple projects registered.
- [ ] Select project → existing project view (no navigation rewrite).

### Task 12: Tests + docs (Claude)

- [ ] Three-project fixture: dashboard lists all, events stay isolated.
- [ ] Document in `docs/cli.md`: `coordinator --print -p "/dashboard"` if exposed via slash proxy.

**Gate D:** Codex — dashboard never leaks another project's task titles across scope.

---

## Wave E — 安全回滚 / 清理

### Task 13: Safe cleanup commands (Grok)

**Files:** `cli.py`, `gitops.py`, `engine.py`

```bash
coordinator repo cleanup-worktrees --dry-run
coordinator repo cleanup-worktrees --apply --project <id>
coordinator task rollback <task-id> --dry-run    # reset branch/worktree to pre-attempt
coordinator supervisor drain --dry-run         # show active leases + running tasks
```

- [ ] `--dry-run` always default; `--apply` requires `--confirm <token>` printed by dry-run.
- [ ] Never delete dirty worktree without `--force` + confirmation.
- [ ] Log every destructive action to `events` table.

### Task 14: Tests (Claude)

- [ ] Dry-run prints candidate paths; apply without token fails.
- [ ] Rollback leaves audit event.

**Gate E (final):** Codex — full suite + wheel + polymarket smoke + cleanup dry-run on temp repo.

---

## Task 15: Integration (Grok)

- [ ] Merge Claude tests without weakening assertions.
- [ ] Run:

```bash
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src \
  python3 -m unittest discover -s tests -q
PYTHONPATH=src python3 -m unittest \
  tests.test_phase5_5_chat_persona tests.test_phase5_5_task_detail \
  tests.test_phase5_5_task_control -v
```

---

## Safety matrix (Gemini must challenge)

| Action | Risk | Mitigation |
|--------|------|------------|
| Cancel running task | orphan worktree | lease release + explicit note; optional `--purge` later |
| Rollback task | lose uncommitted work | dry-run diff summary; require confirm token |
| Log tail | path traversal | artifact registry only; no arbitrary paths |
| Dashboard | cross-project leak | aggregate counts only; detail still needs `project_id` |
| Approve | skip human review policy | only from `awaiting_human`; audit event |

---

## Dependencies on Phase 5.4

| 5.4 feature | 5.5 use |
|-------------|---------|
| `execution_policy` on tasks | Show in task detail; cancel respects stage |
| `context_manifest` | Show hashed file list in task detail (no content) |
| `--mode rpc` | Dashboard/log tail consumable by future automation |
| Goal lineage | Dashboard shows `parent_goal_id` on forked goals |

---

## Open questions (resolve in design spec)

1. Should **approve** trigger merge/push or only unblock daemon?
2. Log tail: poll vs push events (`task.log.append`)?
3. Dashboard in TUI only, or also `coordinator supervisor dashboard` CLI table?
4. Chinese copy for 总管 persona — fixed strings vs Commander-generated?
5. Cancel: default preserve worktree vs auto-cleanup after 24h?

---

## Review handoff

Gemini adversarial checklist and attack tasks:
`docs/superpowers/handoffs/2026-06-26-phase5-4-gemini-review.md` (Attack Task 2 + 3).

Claude deliverables after Gemini **5.5 PASS** or **CONDITIONAL PASS**: see same handoff
"After your review" section.

---

## Suggested first sprint (if scope must shrink)

**MVP 5.5a** (2 weeks): Wave A + Wave C (`approve`/`retry` only) + dashboard counts only.  
**5.5b**: Live log tail + cancel + safe cleanup dry-run.