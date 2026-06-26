# Codex Gate B Re-Sign Handoff: Phase 5.4 Wave B (Goal Sessions)

Date: 2026-06-26
Plan: `docs/superpowers/plans/2026-06-26-phase5-4-context-sessions-tools.md`
Design: `docs/superpowers/specs/2026-06-26-phase5-4-context-sessions-tools-design.md`
Branch: `external/coordinator-global-tui`
Review commit: **`b5f43bf`** (stack `072ca26` → `b5f43bf`)

## Purpose

This is a **re-sign** request. Codex Gate B initially **FAIL**ed on commit
`7ff494f` with three P1 findings. Grok repaired them in `17eef6f`; Claude Code
added boundary and PTY selector tests in `b5f43bf`. Gemini adversarial review
on `b5f43bf` returned **PASS** (one non-blocking P2).

Do **not** re-audit `7ff494f` in isolation. Verify the full stack ending at
`b5f43bf`.

---

## Commit Stack (Wave B)

| Commit | Owner | Summary |
|--------|-------|---------|
| `072ca26` | Claude | Task 4 red tests: `tests/test_goal_sessions.py` |
| `8a59709` | Grok | Task 5: `goal_sessions.py`, migration 012 |
| `7ff494f` | Grok | Task 6: CLI `--resume`/`--fork`, Supervisor RPC |
| `17eef6f` | Grok | Gate B repair: resume w/o prompt, TTY selector, fork bounds |
| `b5f43bf` | Claude | PTY selector + fork field boundary tests |

Wave A (Gate A PASS) ends at `4aa3a11`. Wave B starts at `072ca26`.

---

## Prior Gate B Findings → Resolution

| Prior P1 | Reproduction (old) | Fix | Evidence |
|----------|-------------------|-----|----------|
| Resume ID requires prompt | `coordinator --resume 42 --print` → exit 2, "prompt text is required" | `17eef6f`: `resume is not None` skips prompt gate | `test_resume_id_without_prompt_succeeds` |
| Missing TTY selector | `--resume` no ID always lists + exit 2 | `17eef6f`: `_is_interactive_session` + `_interactive_resume_selection` | `GoalPTYSelectorDetailTests`, `GoalInteractiveSelectorTests` |
| Fork fields unbounded | Adversarial fork: objective ~600k, JSON ~200k each, progress ~200k | `17eef6f`: `MAX_FORK_*` + `_bounded_fork_metadata` | `GoalForkBoundsTests`, `GoalForkFieldBoundaryTests` |

---

## Gemini Adversarial Review (read-only)

**Verdict: PASS** (Attack checklist A–E all satisfied on `b5f43bf`)

**Non-blocking P2:** Fork objective injection — instruction with newlines or
fake `Fork instruction:` lines is appended into the fork objective without
sanitization. Acceptable for Wave B; may harden later.

---

## Required Verification Commands

Run from repository root:

```bash
cd /Users/xiafan/Coordinator
git checkout b5f43bf   # or branch tip containing this stack

PYTHONPATH=src python3 -m unittest tests.test_goal_sessions -v
PYTHONPATH=src python3 -m unittest tests.test_cli_prompt tests.test_cli_file_context -q
PYTHONPATH=src python3 -m unittest tests.test_migration_mirror_sync -q
git diff --check
```

Expected:

```text
tests.test_goal_sessions: 51/51 OK
tests.test_cli_prompt + test_cli_file_context: 46/46 OK
tests.test_migration_mirror_sync: OK
git diff --check: clean
```

Wheel (required on Gate B):

```bash
PYTHONPATH=src python3 -m unittest tests.test_wheel_packaging -q
```

Confirm migration 012 is byte-identical in both paths and included in the wheel.

---

## Manual Spot Checks (optional but recommended)

Use an isolated `COORDINATOR_HOME` and registered git repo (see
`tests/test_goal_sessions.py` fixtures).

```bash
# P1 repair: resume without chat message
coordinator --root <repo> --resume <id> --print
# expect: exit 0, "Resumed goal <id>."

# Non-interactive candidate listing
coordinator --root <repo> --resume --print
# expect: exit 2, candidate table on stdout

# JSON candidates
coordinator --root <repo> --resume --mode json
# expect: exit 2, JSON with "candidates" array

# Fork bounded fields (after completing a terminal source goal)
# Insert oversized objective/progress via test DB or adversarial script;
# fork and assert len(objective) <= 20000, len(progress) <= 2000, etc.
```

---

## Gate B Acceptance Criteria (Wave B)

From design spec § Sessions:

- [ ] Paused/blocked goals resume to `active`; draft stays `draft`
- [ ] Terminal goals cannot resume; error recommends `--fork`
- [ ] Cross-project resume/fork rejected (`goal_wrong_project`)
- [ ] Fork from terminal creates draft with `parent_goal_id`; no Commander
- [ ] Fork copies no tasks, runs, attempts, leases, or artifacts
- [ ] Fork copies bounded objective/metadata (all `MAX_FORK_*` enforced)
- [ ] Only one non-terminal goal per project (migration 011 index)
- [ ] `--continue`, `--resume`, `--fork` mutually exclusive
- [ ] Resume/fork mutations via Supervisor RPC, not CLI direct DB writes
- [ ] `--resume` no ID: TTY selector + confirmation; non-TTY/JSON exit 2
- [ ] Migration 012: `parent_goal_id` column + index; mirror sync
- [ ] Wave A regressions unaffected

---

## Production Files in Scope

| Path | Role |
|------|------|
| `src/local_cli_coordinator/goal_sessions.py` | Candidate list, resume, fork, bounds |
| `src/local_cli_coordinator/cli_chat.py` | CLI session operators, TTY selector |
| `src/local_cli_coordinator/cli.py` | Mutually exclusive parser flags |
| `src/local_cli_coordinator/supervisor_methods.py` | `project.goals`, `.goal.resume`, `.goal.fork` |
| `migrations/012_goal_lineage.sql` | Wheel copy |
| `src/local_cli_coordinator/migrations/012_goal_lineage.sql` | Package copy |
| `tests/test_goal_sessions.py` | 51 contract tests |
| `tests/fixtures/fake_supervisor.py` | RPC handlers for subprocess tests |

---

## Fork Bound Constants (for adversarial re-check)

Defined in `goal_sessions.py`:

```text
MAX_FORK_SOURCE_OBJECTIVE_CHARS = 8_000
MAX_FORK_OBJECTIVE_CHARS        = 20_000
MAX_FORK_PROGRESS_CHARS         = 2_000
MAX_FORK_INSTRUCTION_CHARS      = 2_000
MAX_FORK_TITLE_CHARS            = 200
MAX_FORK_JSON_FIELD_CHARS       = 4_000
MAX_FORK_JSON_LIST_ITEMS        = 50
MAX_FORK_JSON_ITEM_CHARS        = 500
MAX_FORK_MESSAGES               = 5
MAX_FORK_MESSAGE_CHARS          = 500
MAX_FORK_TASKS                  = 20
```

---

## Verdict Format (requested from Codex)

```text
GATE B VERDICT: PASS | FAIL
P0:
P1:
P2:
Reproduction commands:
Blocking Wave C: yes | no
```

On **PASS**, Grok may proceed to Wave C (wait for Claude Task 7 red tests
first per plan). On **FAIL**, return concrete reproductions; Grok fixes, Claude
may add tests, then Gemini + Codex again.

---

## Out of Scope for Gate B

- Wave C: execution policy (`execution_policy.py`), `--no-tools`, RPC output mode
- Wave A file context (already Gate A PASS at `4aa3a11`)
- TUI/PTY layout regressions beyond goal-session CLI entry (unless Wave B broke them)