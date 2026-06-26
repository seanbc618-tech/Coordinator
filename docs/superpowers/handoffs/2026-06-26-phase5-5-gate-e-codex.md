# Codex Gate E Handoff: Phase 5.5 Operational UX (5.5a + 5.5b)

Date: 2026-06-26  
Branch: `main`  
HEAD: **`14d65bc`** (merge PR #4)  
Prerequisite merges: PR #3 (`d279aaf` 5.5a), PR #4 (`a898043` 5.5b)

## Purpose

Independent **acceptance** sign-off for the full Phase 5.5 deliverable. This is not
implementation — run commands, capture exact output, return PASS/FAIL.

**Recommended:** Gemini read-only review completes first
(`2026-06-26-phase5-5b-gemini-review.md`). Gate E may proceed in parallel if schedule
is tight, but note any Gemini P1 in your verdict.

---

## Scope under review

| Wave | Deliverable | PR |
|------|-------------|-----|
| **A** | `chat.send` orchestration + `project.task` detail v2 | #3 |
| **C (partial)** | `project.task.approve` / `retry` | #3 |
| **D (partial)** | `supervisor.dashboard` counts only | #3 |
| **B** | `project.task.log`, `task.log.append`, `/task log` | #4 |
| **C (complete)** | `project.task.cancel` + worker kill | #4 |
| **E** | cleanup dry-run, rollback, supervisor drain | #4 |

Design specs:

- `docs/superpowers/specs/2026-06-26-phase5-5-operational-ux-design.md`
- `docs/superpowers/specs/2026-06-26-phase5-5b-operational-ux-design.md`

---

## Verdict template

```text
=== PHASE 5.5 GATE E ===
VERDICT: PASS | FAIL
P0: None | ...
P1: None | ...
Evidence: (paste command blocks + exit codes + counts)
Blocking sign-off: yes | no
```

---

## Gate 1 — Repository hygiene

```bash
cd /Users/xiafan/Coordinator
git checkout main
git pull origin main
git rev-parse --short HEAD
# expect: 14d65bc or later on main

git diff --check
git status --short
# expect: clean working tree for reviewer
```

---

## Gate 2 — TypeScript (ui-tui)

```bash
cd /Users/xiafan/Coordinator

npm ci --prefix ui-tui
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
```

**Expected (at handoff time):**

```text
typecheck: PASS
lint: PASS
vitest: 14 files, 139 passed
```

**5.5-specific files touched:**

- `ui-tui/src/eventReducer.ts` (`task.log.append`)
- `ui-tui/src/__tests__/eventReducer.test.ts`

---

## Gate 3 — Python full suite (strict)

```bash
cd /Users/xiafan/Coordinator

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src \
  python3 -m unittest discover -s tests -q
```

**Expected (at handoff time):**

```text
Ran 997 tests in ~400s
OK
```

Inspect stderr after `OK` — **zero** `ResourceWarning` lines.

---

## Gate 4 — Phase 5.5 focused suites

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_phase5_5_chat_persona \
  tests.test_phase5_5_task_detail \
  tests.test_phase5_5_task_control \
  tests.test_phase5_5_dashboard \
  tests.test_phase5_5_log_tail \
  tests.test_phase5_5_cleanup \
  tests.test_worker_registry \
  tests.test_worktree_cleanup -v
```

**Expected:** all tests OK (no skips on 5.5b suites).

---

## Gate 5 — Wheel packaging

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_tui_bundle.WheelPackagingTest \
  tests.test_wheel_migrations -v

python3 -m build --wheel --outdir /tmp/coord-gate-e-wheel
```

**Pass criteria:**

- `WheelPackagingTest` + `test_wheel_migrations` green
- Wheel builds without error
- `tui_bundle/entry.js` + `manifest.json` included (hash matches bundle)

**Optional clean-wheel install:**

```bash
python3 -m venv /tmp/coord-gate-e-venv
/tmp/coord-gate-e-venv/bin/pip install --force-reinstall \
  /tmp/coord-gate-e-wheel/local_cli_coordinator-*.whl

COORDINATOR_HOME=/tmp/coord-gate-e-home \
  /tmp/coord-gate-e-venv/bin/coordinator supervisor status
# expect: coherent error or status (no import crash)
```

---

## Gate 6 — 5.5b admin ops smoke (temp repo)

Use an isolated legacy-layout tmp (no production data):

```bash
TMP=$(mktemp -d)
cd "$TMP"
git init -q repo && cd repo && git commit --allow-empty -m init -q

# Minimal coordinator config (adapt from tests/test_worktree_cleanup.py)
mkdir -p ../config
# ... write agents.toml, repos.toml, policy.toml, coordinator.db ...

cd /Users/xiafan/Coordinator

# Dry-run cleanup
PYTHONPATH=src python3 -m local_cli_coordinator \
  --root "$TMP" repo cleanup-worktrees
# expect: confirm_token, mode: dry-run, exit 0

# Apply without token — must fail
PYTHONPATH=src python3 -m local_cli_coordinator \
  --root "$TMP" repo cleanup-worktrees --apply
# expect: non-zero, confirm_required

# Drain
PYTHONPATH=src python3 -m local_cli_coordinator \
  --root "$TMP" supervisor drain
# expect: action: supervisor-drain, exit 0

# Rollback missing task
PYTHONPATH=src python3 -m local_cli_coordinator \
  --root "$TMP" task rollback 99999
# expect: non-zero
```

Or run the committed red tests (equivalent contract):

```bash
PYTHONPATH=src python3 -m unittest tests.test_phase5_5_cleanup tests.test_worktree_cleanup -v
```

---

## Gate 7 — Headless RPC smoke (FakeSupervisor)

Requires `FakeSupervisor` fixture pattern from `tests/test_phase5_5_*.py`.

| Command | Expect |
|---------|--------|
| `coordinator --mode rpc -p "/dashboard"` | `ok: true`, `projects` array |
| `coordinator --mode rpc -p "/task 1 log"` | `ok: true`, log tail payload |
| `coordinator --mode rpc -p "/task 1 cancel"` | `ok: true`, `worker_terminated` key |
| `coordinator --mode rpc -p "/task 1 approve"` | `ok: false` or invalid_state (no awaiting_human) |

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_phase5_5_log_tail.LogTailRPCTests.test_log_tail_rpc_method_exists \
  tests.test_phase5_5_task_control.TaskCancelTests.test_cancel_reports_worker_terminated_field -v
```

---

## Gate 8 — Real TUI smoke (polymarket) — optional but recommended

Per Phase 5.4 acceptance pattern. Skip if polymarket repo unavailable; note in verdict.

```bash
# Prerequisite: Supervisor running, project registered
cd /Users/xiafan/polymarket-crypto-threshold

coordinator --print -p "/status"
coordinator --print -p "/dashboard"
coordinator --print -p "/tasks"
coordinator --mode rpc -p "/task <known-id> log"
```

**Pass criteria:**

- No TUI crash on slash commands
- `/dashboard` shows counts without foreign project titles
- Detach (Ctrl+C) does not kill background workers (existing invariant)

---

## Gate 9 — Regression spot checks (5.4 carry-forward)

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_cli_file_context \
  tests.test_goal_sessions \
  tests.test_execution_policy \
  tests.test_phase5_4_e2e -q
```

**Expected:** all pass (no Phase 5.4 regressions from 5.5 RPC/slash changes).

---

## Safety checklist (must be true on inspection)

| Risk | Mitigation verified |
|------|---------------------|
| Cancel orphan worker | `WorkerRegistry` + terminate before lease release |
| Log path traversal | `log_tail.py` artifact registry only |
| Dashboard cross-project leak | counts only in `build_dashboard_payload` |
| Destructive cleanup | dry-run + confirm token |
| Rate-limited log RPC | 2 req/s per project+task |

---

## Evidence to append

After running gates, append to **`docs/superpowers/handoffs/2026-06-26-phase5-5-acceptance.md`**
(create if missing):

1. Exact test counts (Python + TS)
2. `git rev-parse HEAD`
3. Wheel smoke result
4. Polymarket smoke result (or SKIP reason)
5. Gate E verdict block

Then update sign-off record (mirror `2026-06-25-phase5-2-signoff.md`).

---

## Out of scope for Gate E

- Implementing fixes
- Gemini adversarial review (separate handoff)
- `supervisor drain --apply` (deferred)
- `project.task.cancel --purge` (deferred)

---

## Quick reference — one-shot script

```bash
cd /Users/xiafan/Coordinator
git checkout main && git pull

set -e
npm ci --prefix ui-tui
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src \
  python3 -m unittest discover -s tests -q

PYTHONPATH=src python3 -m unittest \
  tests.test_phase5_5_chat_persona \
  tests.test_phase5_5_task_detail \
  tests.test_phase5_5_task_control \
  tests.test_phase5_5_dashboard \
  tests.test_phase5_5_log_tail \
  tests.test_phase5_5_cleanup \
  tests.test_worker_registry \
  tests.test_worktree_cleanup -v

PYTHONPATH=src python3 -m unittest \
  tests.test_tui_bundle.WheelPackagingTest \
  tests.test_wheel_migrations -v

git diff --check
echo "Gate E script complete — paste outputs into acceptance handoff"
```