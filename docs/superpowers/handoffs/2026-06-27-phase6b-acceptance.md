# Phase 6B Self-Sustaining Autonomy — Acceptance Handoff

Date: 2026-06-27
Branch: `phase6-autonomous-loop-core`
Plan: `docs/superpowers/plans/2026-06-27-phase6b-self-sustaining-autonomy.md`

## Status

**Task 0 (Claude):** Red tests + deterministic fixtures committed — 10/10 new tests red
**Task 5 (Claude):** Docs + handoff committed

**Grok Tasks 1–4:** Pending implementation

## Critical contract

**Commander generation creates backlog rows, not tasks.**

Generated Commander proposals must flow:

```text
CommanderTaskProposal → BacklogDraft → project_backlog_items
```

They must **not** call `create_task()`, `admit_commander_response()`, or
`_admit_task_proposal()` in the same iteration. Promotion to worker tasks
happens on a later loop tick via `promote_next_backlog_item()`.

## Commits (Claude)

| Commit | Message |
|--------|---------|
| `7669d81` | `test: capture Phase 6B self-sustaining generation contracts` |
| `9dd68e0` | `docs: document Phase 6B self-sustaining autonomy` |

## Red test suites

| File | New tests | Status |
|------|-----------|--------|
| `tests/test_commander_backlog.py` | 3 | 🔴 `ModuleNotFoundError: commander_backlog` |
| `tests/test_loop_autonomy.py` | 5 | 🔴 assertion failures (`wait` vs `generate`) |
| `tests/test_phase6_autonomous_loop_e2e.py` | 2 | 🔴 RPC returns `wait` instead of `generate` |
| `tests/test_supervisor_process.py` | 1 | 🔴 30s readiness timeout instead of immediate error |
| **Total new** | **11** | **10 failing + 1 guard passing** |

### Required red test names

| Test name | File | Captures |
|-----------|------|----------|
| `test_commander_task_proposal_converts_to_backlog_draft` | `test_commander_backlog.py` | Proposal → `BacklogDraft` |
| `test_generation_never_creates_task_directly` | `test_commander_backlog.py` | No direct task creation |
| `test_generation_caps_to_configured_max` | `test_commander_backlog.py` | `max_items` cap |
| `test_loop_generates_backlog_when_idle_and_empty` | `test_loop_autonomy.py` | Idle → `generate` decision |
| `test_loop_generation_does_not_admit_task_same_iteration` | `test_loop_autonomy.py` | No same-tick admission |
| `test_loop_does_not_generate_when_ready_backlog_exists` | `test_loop_autonomy.py` | Admit before generate |
| `test_loop_does_not_generate_when_commander_run_active` | `test_loop_autonomy.py` | Active run short-circuit |
| `test_duplicate_generated_backlog_is_idempotent` | `test_loop_autonomy.py` | Dedupe across ticks |
| `test_loop_step_generates_backlog_and_reports_generate` | `test_phase6_autonomous_loop_e2e.py` | `/loop step` → `generate` |
| `test_backlog_rpc_shows_commander_generated_item` | `test_phase6_autonomous_loop_e2e.py` | `/backlog` source=commander |
| `test_supervisor_start_reports_missing_config_file` | `test_supervisor_process.py` | Immediate config error |

## Deterministic fixtures

| File | Purpose |
|------|---------|
| `tests/fixtures/phase6b_commander.py` | Proposal/response/run-result builders + autonomy config |
| `tests/fixtures/fake_commander.py` | Headless Commander agent (`COORDINATOR_FAKE_COMMANDER_TASKS`) |

## Focused test commands

Phase 6B red suite (expected: 10 failures until Grok implements Tasks 1–4):

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_commander_backlog \
  tests.test_loop_autonomy \
  tests.test_phase6_autonomous_loop_e2e \
  tests.test_supervisor_process -v
```

Adapter-only (after Task 1):

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_backlog -v
```

Loop generation (after Task 2):

```bash
PYTHONPATH=src python3 -m unittest tests.test_loop_autonomy tests.test_commander_backlog -v
```

## Full suite command (Codex Gate D/E)

```bash
git diff --check
PYTHONPATH=src python3 -m unittest \
  tests.test_commander_backlog \
  tests.test_autonomous_backlog \
  tests.test_loop_autonomy \
  tests.test_phase6_autonomous_loop_e2e \
  tests.test_supervisor_process -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
python3 -m build
```

## Clean-wheel configured smoke

```bash
tmpdir="$(mktemp -d)"
mkdir -p "$tmpdir/home/config"
cp config/*.toml "$tmpdir/home/config/"
python3 -m venv "$tmpdir/venv"
"$tmpdir/venv/bin/pip" install dist/*.whl
COORDINATOR_HOME="$tmpdir/home" \
  "$tmpdir/venv/bin/coordinator" project add \
  /Users/xiafan/polymarket-crypto-threshold --yes
cd /Users/xiafan/polymarket-crypto-threshold
COORDINATOR_HOME="$tmpdir/home" \
  "$tmpdir/venv/bin/coordinator" --print -p "/loop"
```

## Docs

| File | Content |
|------|---------|
| `docs/autonomous-loop.md` | Self-sustaining generation, config, operator examples |
| `docs/cli.md` | `/loop`, `/loop step`, `/backlog` examples and generation output |

## Task ownership

| Task | Owner | Status |
|------|-------|--------|
| 0: Red tests + fixtures | Claude | ✅ done |
| 1: Commander-to-backlog adapter | Grok | pending |
| 2: `_maybe_generate_backlog()` | Grok | pending |
| 3: Dedupe race hardening | Grok | pending |
| 4: Supervisor events + startup diagnostic | Grok | pending |
| 5: Docs + handoff | Claude | ✅ done |
| 6: Gemini adversarial review | Gemini | pending |
| 7: Codex Gate D/E | Codex | pending |

## Gate sequence

```text
Task 0 (Claude red tests) ← DONE
  → Task 1 (Grok adapter) → Gate A (Codex)
  → Task 2 (Grok loop generation) → Gate B (Codex)
  → Task 3 (Grok dedupe) 
  → Task 4 (Grok events/diagnostic) → Gate C (Codex)
  → Task 5 (Claude docs) ← DONE
  → Task 6 (Gemini adversarial)
  → Task 7 (Codex Gate D/E)
```