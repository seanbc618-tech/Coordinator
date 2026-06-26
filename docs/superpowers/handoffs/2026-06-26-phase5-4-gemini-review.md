# Gemini Handoff: Phase 5.4 Merge + Phase 5.5 Plan Adversarial Review

You are a **read-only adversarial reviewer**. Do not implement production fixes, do not
open PRs, do not stage untracked local files.

Repository: `/Users/xiafan/Coordinator`  
Branch: `external/coordinator-global-tui` (tip `af7a082` or later)  
Gate C: **PASS** (Codex, 2026-06-26)

Return **two independent verdicts** (merge readiness + 5.5 plan draft):

```text
=== MERGE READINESS ===
VERDICT: PASS | CONDITIONAL PASS | FAIL
P0:
P1:
P2:
Reproduction commands:
Blocking merge: yes | no

=== PHASE 5.5 PLAN DRAFT ===
VERDICT: PASS | CONDITIONAL PASS | FAIL
P0:
P1:
P2:
Reproduction commands:
Blocking 5.5 kickoff: yes | no
```

**Completed proxy review:** `docs/superpowers/handoffs/2026-06-26-phase5-4-gemini-review-result.md`

Severity guide:

| Level | Meaning |
|-------|---------|
| **P0** | Data loss, security bypass, cross-project leak, merge breaks production |
| **P1** | Incorrect gate claim, missing migration path, test gap that hides regression |
| **P2** | Doc drift, scope creep, hygiene, naming |

---

## Inputs (read in this order)

### Phase 5.4 merge

| Doc | Purpose |
|-----|---------|
| `docs/superpowers/handoffs/2026-06-26-phase5-4-merge-readiness.md` | Pre-merge checklist |
| `docs/superpowers/handoffs/2026-06-26-phase5-4-acceptance.md` | Gate C sign-off + test counts |
| `docs/superpowers/plans/2026-06-26-phase5-4-context-sessions-tools.md` | Original plan |
| `docs/superpowers/specs/2026-06-26-phase5-4-context-sessions-tools-design.md` | Design spec |
| `docs/cli.md`, `docs/troubleshooting.md` | Operator docs |

### Phase 5.5 draft

| Doc | Purpose |
|-----|---------|
| `docs/superpowers/plans/2026-06-26-phase5-5-operational-ux.md` | Plan draft (DRAFT) |
| `docs/superpowers/specs/2026-06-23-pi-inspired-coordinator-ux.md` | UX backlog context |

### Code hotspots (spot-check only)

| Area | Paths |
|------|-------|
| File context | `src/local_cli_coordinator/context_files.py` |
| Goal sessions | `src/local_cli_coordinator/goal_sessions.py` |
| Execution policy | `src/local_cli_coordinator/execution_policy.py`, `engine.py` |
| RPC / CLI | `src/local_cli_coordinator/cli_chat.py`, `supervisor_protocol.py` |
| Migrations | `migrations/012_goal_lineage.sql`, `migrations/013_execution_context.sql` |
| Tests | `tests/test_cli_file_context.py`, `test_goal_sessions.py`, `test_execution_policy.py`, `test_phase5_4_e2e.py` |

---

## Attack Task 1: Merge Readiness & PR Hygiene

**Goal:** Confirm Phase 5.4 is safe to merge and push without accidental scope.

### Required checks

1. **Commit scope** — For commits `072ca26`…`4d9a8b6` (+ docs commits after Gate C):
   ```bash
   git log --oneline origin/external/coordinator-global-tui..HEAD
   git log --name-only origin/external/coordinator-global-tui..HEAD | rg 'run_attack|debug_|child[0-9]'
   ```
   Must find **no** debug script paths in history.

2. **Untracked pollution** — Working tree may contain local scratch; confirm they are
   **not** in any commit:
   ```bash
   git status --short
   git diff --cached --name-only
   ```

3. **Migration safety** — `012` / `013` idempotency on:
   - fresh install
   - DB that already has columns from partial manual apply
   - downgrade story (what breaks if operator runs older binary?)

4. **Gate claims vs evidence** — Cross-check acceptance handoff:
   - 949 full suite / 156 focused — are listed test files sufficient?
   - Gate C leak fix (`3f6e1ca`, `8307333`) — any remaining daemon-thread patterns?
   ```bash
   rg 'Thread\(.*daemon=True\).*start\(\)' tests/ --glob '*.py'
   ```

5. **Cross-project isolation** — Phase 5.4 features must not weaken:
   - `@file` path escape
   - goal resume/fork project boundaries
   - execution policy server intersection

6. **Rollback narrative** — Operator merges then needs to revert:
   - Are goals/tasks with `execution_policy` / `context_manifest` readable by older code?
   - Documented or silent failure?

### Reproduction commands (merge gate baseline)

```bash
cd /Users/xiafan/Coordinator
git diff --check

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest \
  tests.test_cli_file_context tests.test_goal_sessions \
  tests.test_execution_policy tests.test_phase5_4_e2e tests.test_cli_prompt -v

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest \
  tests.test_phase5_4_e2e.LeakRegressionTests -v
```

---

## Attack Task 2: Phase 5.5 Plan Draft — Scope, Safety, Feasibility

**Goal:** Challenge `docs/superpowers/plans/2026-06-26-phase5-5-operational-ux.md` before
Claude writes red tests or Grok implements.

### Required checks

1. **Scope** — Five waves in one phase: realistic? Recommend **5.5a / 5.5b** split or
   deferrals with explicit cut line.

2. **Protocol v1 compatibility** — New RPCs proposed:
   - `project.task.log`
   - `project.task.approve` / `cancel` / `retry`
   - `supervisor.dashboard`
   
   Do any break existing TUI client assumptions? Version bump needed?

3. **Safety matrix holes** — Challenge each row plus:
   | Attack | Question |
   |--------|----------|
   | Cancel running task | Lease vs engine race; partial artifact state? |
   | Log tail | Symlink artifact path? Unbounded poll DoS? |
   | Dashboard | Task title leakage in aggregate view? |
   | Approve | Bypass `execution_policy` `commit` forbidden path? |
   | Rollback/cleanup | Overlap with existing `coordinator repo cleanup-worktrees`? |

4. **总管 persona** — Orchestration metadata in `chat.send`: risk of leaking
   Commander diagnostics into `user_reply`? Duplication with Phase 5.2 schema v2?

5. **Live log UX** — Poll every 500ms: load on Supervisor with N running tasks?
   Prefer event push (`task.log.append`)?

6. **Dependencies** — Does plan correctly build on Phase 5.4 (`execution_policy`,
   `context_manifest`, RPC mode) without re-implementing Wave C?

7. **Open questions** — Which must be resolved **before** Task 0 red tests?
   Flag any missing question (e.g. audit retention, multi-operator approve).

### Suggested adversarial scenarios (describe expected behavior)

| # | Scenario | Pass criteria |
|---|----------|---------------|
| A | Operator cancels task while verifier running | Lease released; state terminal; note explains outcome |
| B | Tail RPC with `../../etc/passwd` artifact path | Rejected at registry boundary |
| C | Dashboard with 3 projects; client A subscribes proj-a only | No proj-b title in events |
| D | Approve task with `execution_policy` forbidding `commit` | Does not auto-commit; only unblocks human path |
| E | `cleanup-worktrees --dry-run` on dirty worktree | Lists paths; `--apply` without token fails |

---

## Attack Task 3: Doc Consistency

Cross-check for contradictions:

| Claim location | Verify against |
|----------------|----------------|
| merge-readiness "16 commits" | `git rev-list --count origin/external/coordinator-global-tui..HEAD` |
| acceptance "156 focused" | actual test method counts |
| 5.5 plan Task 7 `project.task.retry` | existing `coordinator task retry` in `cli.py` semantics |
| troubleshooting execution_policy errors | `execution_policy.py` + engine messages |

---

## Out of scope for Gemini

- Implementing fixes (Grok / Claude owners)
- Codex Gate re-sign (already PASS for 5.4)
- ui-tui visual design review
- Phase 5.5 implementation code review (future handoff)

---

## After your review

| Verdict | Next owner | Action |
|---------|------------|--------|
| Merge **PASS** | Operator | `git push origin external/coordinator-global-tui`; open PR to `main` |
| Merge **FAIL** | Grok | Fix P0/P1; Codex re-run affected gates |
| 5.5 **PASS** | Claude | `docs/superpowers/handoffs/2026-06-26-phase5-5-planning-kickoff.md` + red tests |
| 5.5 **CONDITIONAL** | Grok | Revise plan per P1; Gemini re-review scope section only |
| 5.5 **FAIL** | Grok | Rewrite plan / split phase; new design spec |

**Claude follow-up** (after Gemini merge PASS + 5.5 PASS or CONDITIONAL):

1. `docs/cli.md` merge note / cross-links
2. `tests/test_phase5_5_*.py` red suites per approved waves
3. Optional merge smoke log in acceptance handoff
4. Packaging lint: wheel must not include `run_attack_*` at repo root

---

## Reference: Gate C baseline (do not re-litigate unless regression found)

Gate C VERDICT: PASS
949/949 full suite (ResourceWarning=error)
LeakRegressionTests 4/4
test_gate_socket_client_receives_live_event_after_subscribe OK
```

If your merge review **FAIL**s full suite, provide exact command output — that overrides
the baseline above.```text
=== MERGE READINESS ===
VERDICT: CONDITIONAL PASS
P0: None
P1: (broken CLI contract) Silent drop of user prompt during interactive resume (Wave B regression). `--resume -p "prompt"` without an ID drops the prompt when user interactively selects a goal.
P2: (broken RPC contract) CLI syntax/argparse validation errors bypass `--mode rpc` JSON format, outputting directly to stderr.
P2: (doc drift) "merge-readiness" document claims "16 commits" but the actual unmerged count is different.
Reproduction commands:
coordinator --resume -p "I just wrote this long instruction and it will vanish"
coordinator --mode rpc --tools non_existent_tool -p "hello"
Blocking merge: yes

=== PHASE 5.5 PLAN DRAFT ===
VERDICT: CONDITIONAL PASS
P0: None
P1: Task 7 `project.task.cancel` has an engine race condition: releasing the lease while the worker is actively running in a background thread or process can cause a corrupted artifact state if the worker continues writing to disk or updates the database. The background process needs to be cleanly killed or signaled.
P1: Task 4 `project.task.log` tail RPC allows polling every 500ms. With 10 tasks running across clients, this generates many RPC calls per second, which scales poorly. Supervisor should use an event push (`task.log.append`) for active logs rather than relying entirely on client polling, or rate limit the RPC.
P2: Scope is extremely large for one phase (5 Waves). The 5.5a/5.5b split recommendation should be formalized into separate phase plans.
Reproduction commands: N/A (Plan Review)
Blocking 5.5 kickoff: yes
