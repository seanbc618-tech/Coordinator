# Phase 7 Strategic Autonomy and Recovery — Acceptance Handoff

Date: 2026-06-29
Branch: `phase7-strategic-autonomy`
Plan: `docs/superpowers/plans/2026-06-29-phase7-strategic-autonomy-recovery.md`

## Scope Delivered

Phase 7 adds a strategic layer above the Phase 6 autonomous loop:

- Project-scoped milestones (`strategy.py`, migration 017)
- Milestone-linked backlog and loop status
- Bounded recovery proposals for failed tasks (`recovery.py`)
- Local agent scorecards and capability-safe routing (`agent_scorecard.py`)
- Overnight quiet-hour pause and persisted summaries (`overnight.py`)
- Supervisor RPCs: `project.strategy`, `project.recoveries`, `project.agents`, `project.overnight`
- CLI/TUI slash commands: `/strategy`, `/recoveries`, `/agents`, `/overnight`, richer `/dashboard`

Single global Supervisor preserved. No second daemon. No RPC bypass.

## Commit Stack (Tasks 0–10)

| Task | Message |
| --- | --- |
| 0 | `test: capture Phase 7 strategy contracts` |
| 1 | `feat: persist strategy milestones` |
| 2 | `feat: link autonomous backlog to milestones` |
| 3 | `feat: propose bounded recoveries for failed tasks` |
| 4 | `feat: maintain local agent scorecards` |
| 5 | `feat: apply scorecard-aware worker routing` |
| 6 | `feat: add overnight run windows and summaries` |
| 7 | `feat: expose strategy and recovery RPCs` |
| 8 | `feat: add strategic autonomy slash commands` |
| 10 | `docs: document Phase 7 strategy and recovery` |

## Review Status

| Reviewer | Verdict | Handoff |
| --- | --- | --- |
| Gemini (Tasks 1–8) | CONDITIONAL PASS | `docs/superpowers/handoffs/2026-06-29-phase7-gemini-review.md` |
| Codex Gate G | PASS | This document |

Gemini conditions addressed in Task 10:

1. Documentation updated in `docs/cli.md`, `docs/tui.md`, `docs/troubleshooting.md`
2. TUI bundle rebuilt and committed under `src/local_cli_coordinator/tui_bundle/`

Codex independently verified on 2026-06-29 that Task 10 closes the Gemini
documentation condition: `docs/cli.md`, `docs/tui.md`, and
`docs/troubleshooting.md` now document `/strategy`, `/recoveries`, `/agents`,
and `/overnight`. The Gemini review file remains the original conditional
review artifact; this handoff records the post-Task-10 closure.

## Operator Quick Start

```bash
coordinator --print -p "/strategy"
coordinator --print -p "/recoveries"
coordinator --print -p "/agents"
coordinator --print -p "/overnight start --until 08:00"
coordinator --print -p "/dashboard"
```

## Gate G Commands

```bash
git diff --check
# PASS

PYTHONPATH=src python3 -m unittest \
  tests.test_strategy tests.test_recovery tests.test_agent_scorecard \
  tests.test_overnight tests.test_phase7_strategic_autonomy_e2e -v
# Ran 32 tests in 3.754s — OK

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
# Ran 1117 tests in 458.018s — OK

npm run typecheck --prefix ui-tui
# PASS

npm run lint --prefix ui-tui
# PASS

npm test --prefix ui-tui -- --run
# 154 tests / 16 files — PASS

npm run build --prefix ui-tui
# PASS, build_hash=fcd84b0b758859cc

PYTHONPATH=src python3 -m unittest tests.test_tui_bundle tests.test_wheel_migrations -v
# Ran 10 tests in 10.347s — OK

python3 -m build
# Successfully built local_cli_coordinator-0.1.0.tar.gz and local_cli_coordinator-0.1.0-py3-none-any.whl
```

Clean-wheel smoke (no `PYTHONPATH`):

```bash
python3 -m venv /tmp/coord-phase7-venv
/tmp/coord-phase7-venv/bin/pip install dist/*.whl
env -u PYTHONPATH /tmp/coord-phase7-venv/bin/coordinator init --dry-run --json
env -u PYTHONPATH COORDINATOR_HOME=/tmp/coord-phase7-home /tmp/coord-phase7-venv/bin/coordinator init --yes --json
env -u PYTHONPATH COORDINATOR_HOME=/tmp/coord-phase7-home /tmp/coord-phase7-venv/bin/coordinator config explain --json
env -u PYTHONPATH COORDINATOR_HOME=/tmp/coord-phase7-home /tmp/coord-phase7-venv/bin/coordinator doctor --json
# PASS: all commands returned ok=true JSON from the installed wheel.
```

## Safety Properties (verified by tests + Gemini review)

- Milestones and summaries are project-scoped
- Recovery proposals are deduped (one open proposal per task)
- Recovery admission requires evaluation and creates ready backlog items
- Scorecard routing never selects incapable agents
- Per-agent cooldowns do not block all workers
- Quiet hours pause scheduling without killing active workers
- Slash commands route through Supervisor RPC only
