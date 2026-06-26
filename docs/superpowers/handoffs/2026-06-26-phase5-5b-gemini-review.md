# Gemini Handoff: Phase 5.5b Implementation Adversarial Review

You are a **read-only adversarial reviewer**. Do not implement production fixes, do not
open PRs, do not stage untracked local files.

Repository: `/Users/xiafan/Coordinator`  
Branch: `main`  
Review commit: **`14d65bc`** (merge PR #4) · implementation **`a898043`**

Return **one verdict** for 5.5b implementation (post-merge):

```text
=== PHASE 5.5b IMPLEMENTATION ===
VERDICT: PASS | CONDITIONAL PASS | FAIL
P0: None | (data loss / security bypass / cross-project leak)
P1: (incorrect safety claim, missing test, race still present)
P2: (doc drift, hygiene, scope)
Reproduction commands: (exact bash for each finding)
Blocking Phase 5.5 sign-off: yes | no
```

Severity guide:

| Level | Meaning |
|-------|---------|
| **P0** | Data loss, security bypass, cross-project leak, orphan processes writing after cancel |
| **P1** | Gemini P1 blocker still open; attack scenario fails; test gap hiding regression |
| **P2** | Doc drift, naming, non-blocking UX |

---

## Purpose

Phase 5.5 planning review (`2026-06-26-phase5-4-gemini-review-result.md`) deferred two
**P1** findings to **5.5b**. This handoff asks Gemini to verify the **merged**
implementation on `main`, not the draft plan.

**5.5a** (PR #3, `d279aaf`): orchestration metadata, task detail v2, approve/retry,
dashboard counts — assumed green; spot-check only where 5.5b touches shared code.

**5.5b** (PR #4, `14d65bc`): log tail, cancel worker kill, safe cleanup/rollback/drain.

---

## Inputs (read in this order)

| Doc | Purpose |
|-----|---------|
| `docs/superpowers/specs/2026-06-26-phase5-5b-operational-ux-design.md` | Frozen 5.5b contract |
| `docs/superpowers/specs/2026-06-26-phase5-5-operational-ux-design.md` | 5.5a contract + deferrals |
| `docs/superpowers/plans/2026-06-26-phase5-5-operational-ux.md` | Original plan + safety matrix |
| `docs/superpowers/handoffs/2026-06-26-phase5-4-gemini-review-result.md` | Prior P1 blockers |

### Code hotspots (5.5b)

| Area | Paths |
|------|-------|
| Log tail | `src/local_cli_coordinator/log_tail.py`, `supervisor_methods.py` (`project.task.log`) |
| Push events | `event_stream_reporter.py`, `supervisor.py`, `ui-tui/src/eventReducer.ts` |
| Cancel / worker kill | `worker_registry.py`, `process.py`, `task_control.py` |
| Safe admin ops | `ops_safety.py`, `cli_admin_ops.py`, `cli.py` |
| CLI slash | `cli_chat.py` (`/task <id> log`) |
| Tests | `tests/test_phase5_5_log_tail.py`, `test_phase5_5_cleanup.py`, `test_phase5_5_task_control.py`, `test_worker_registry.py`, `test_worktree_cleanup.py` |

---

## Attack Task 1: Gemini P1 blocker closure

Verify the two planning P1 items are **actually** closed in code, not only in spec prose.

### P1-a — Cancel lease race

**Claim:** `project.task.cancel` signals/kills worker **before** lease release.

**Checks:**

1. Read `cancel_task()` order: `GLOBAL_WORKER_REGISTRY.terminate()` → `release_task_lease()` → state transition.
2. Read `process.run_command()`: registers `task_id` on `Popen` start; unregisters in `finally`.
3. Confirm no code path releases lease while `Popen` is still registered without `terminate()`.

**Reproduction (unit):**

```bash
cd /Users/xiafan/Coordinator
git checkout 14d65bc
PYTHONPATH=src python3 -m unittest tests.test_worker_registry -v
PYTHONPATH=src python3 -m unittest tests.test_phase5_5_task_control.TaskCancelTests -v
```

**Reproduction (manual — optional):**

- Start a long-running agent task (`sleep 300` or slow verify).
- Call `coordinator -p "/task <id> cancel" --mode rpc` while `running`.
- Expect: worker process group gone within ~5s; task → `failed`; `worker_terminated: true` when process was registered.

**Fail if:** worker still writing to attempt log or DB after cancel; lease released with no signal attempt.

### P1-b — Log tail poll DoS

**Claim:** Push-primary (`task.log.append`) + RPC rate limit (≥ 500ms → 2 req/s).

**Checks:**

1. `EventStreamReporter` publishes `task.log.append` on stdout/stderr with `task_id`.
2. `supervisor_methods._handle_project_task_log` enforces rate limit → `rate_limited`.
3. TUI `eventReducer` handles `task.log.append` (alias of `task.output`).

**Reproduction:**

```bash
PYTHONPATH=src python3 -m unittest tests.test_phase5_5_log_tail -v
cd ui-tui && npm test -- --run src/__tests__/eventReducer.test.ts
```

**Fail if:** unbounded tail RPC without rate limit; arbitrary filesystem paths accepted.

---

## Attack Task 2: Safety matrix scenarios (A–E)

From `2026-06-26-phase5-4-gemini-review-result.md` Attack Task 2. **Pass criteria** must
hold on `14d65bc`.

| # | Scenario | Pass criteria | Primary evidence |
|---|----------|---------------|------------------|
| **A** | Cancel during verify / running worker | SIGTERM→grace→SIGKILL; lease released; terminal `failed`; `task.updated` event | `worker_registry.py`, `task_control.py`, manual repro above |
| **B** | Tail `../../etc/passwd` or arbitrary path | Only `artifacts` table paths; `artifact_not_found` / empty safe response; no open() outside registry | `log_tail.py` `_resolve_artifact_path` |
| **C** | Dashboard 3 projects; client A = proj-a only | Aggregate payload has counts only — **no task titles** | `task_control.build_dashboard_payload`, `test_phase5_5_dashboard.py` |
| **D** | Approve with policy forbidding `commit` | `approve_task` never calls `commit_all`; only `awaiting_human` → `ready` | `task_control.approve_task`, 5.5a tests |
| **E** | `cleanup-worktrees --dry-run` on dirty wt | Lists candidates (or excludes dirty without `--force`); `--apply` without `--confirm` fails | `cli_admin_ops.py`, `test_worktree_cleanup.py`, `test_phase5_5_cleanup.py` |

### Scenario B — path traversal (describe attack, do not run on production)

1. Insert artifact row with path `../../etc/passwd` for a task (test DB only).
2. Call `project.task.log` for that task.
3. **Pass:** read stays within resolved artifact path logic; no host file leak in `content`.

### Scenario E — confirm token

```bash
# Legacy root layout (isolated tmp)
coordinator --root <tmp> repo cleanup-worktrees
# expect: confirm_token line, mode: dry-run

coordinator --root <tmp> repo cleanup-worktrees --apply
# expect: non-zero, confirm_required

coordinator --root <tmp> repo cleanup-worktrees --apply --confirm WRONG
# expect: non-zero, confirm_mismatch
```

---

## Attack Task 3: Scope & doc consistency

1. **5.5b non-goals honored?**
   - No `--purge` on cancel
   - `supervisor drain --apply` not implemented (dry-run only)
   - Rollback = `reset --hard` + `clean -fd`, not arbitrary commit rewind

2. **Overlap with legacy cleanup**
   - `repo cleanup-worktrees` now dry-run-first; legacy immediate-delete behavior removed.
   - Confirm `test_worktree_cleanup.py` updated — not silent regression for operators.

3. **COORDINATOR_HOME vs `--root`**
   - `_use_global_home()` only when `root` is `.` — explicit `--root` must not pick up stray `COORDINATOR_HOME` from shell (see `cli_admin_ops.py`).

4. **Open questions from 5.5 plan**
   - Cancel preserves worktree by default — still true?
   - Log slash documented? (`docs/cli.md` may lag — flag P2 if missing)

---

## Attack Task 4: Test adequacy (read-only)

| File | Minimum bar |
|------|-------------|
| `test_phase5_5_log_tail.py` | RPC method exists; cross-project reject; resource warning |
| `test_phase5_5_cleanup.py` | dry-run default; apply requires confirm; drain/rollback smoke |
| `test_phase5_5_task_control.py` | cancel returns `worker_terminated` |
| `test_worker_registry.py` | SIGTERM/SIGKILL path on real subprocess |
| `test_worktree_cleanup.py` | two-step dry-run → apply with token |

**Challenge:** Are any attack scenarios **untested** but claimed in spec? List as P1/P2.

---

## Out of scope for Gemini

- Implementing fixes (Grok / Claude owners)
- Codex Gate E execution (separate handoff)
- ui-tui visual design
- GitHub CI (repo has no Actions workflows)

---

## Suggested verification commands (read-only)

```bash
cd /Users/xiafan/Coordinator
git checkout 14d65bc

PYTHONPATH=src python3 -m unittest \
  tests.test_phase5_5_log_tail \
  tests.test_phase5_5_cleanup \
  tests.test_phase5_5_task_control \
  tests.test_phase5_5_chat_persona \
  tests.test_phase5_5_task_detail \
  tests.test_phase5_5_dashboard \
  tests.test_worker_registry \
  tests.test_worktree_cleanup -v

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src \
  python3 -m unittest discover -s tests -q
# Expected at handoff time: 997 OK

git diff --check  d279aaf..14d65bc
```

---

## After your review

| Verdict | Next owner | Action |
|---------|------------|--------|
| **PASS** | Codex | Run Gate E (`2026-06-26-phase5-5-gate-e-codex.md`) |
| **CONDITIONAL** | Grok | Fix P1; append result to `2026-06-26-phase5-5b-gemini-review-result.md` |
| **FAIL** | Grok | Fix P0/P1; Gemini re-review attack scenarios only |

Write result to: `docs/superpowers/handoffs/2026-06-26-phase5-5b-gemini-review-result.md`