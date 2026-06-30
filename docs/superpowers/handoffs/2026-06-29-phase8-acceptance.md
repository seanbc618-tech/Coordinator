# Phase 8 Evidence Intelligence and Review Gates — Acceptance Handoff

Date: 2026-06-29
Branch: `phase8-evidence-review`
Plan: `docs/superpowers/plans/2026-06-29-phase8-evidence-intelligence-review-gates.md`

## Scope Delivered

Phase 8 adds an evidence and review layer before terminal task acceptance:

- Durable task evidence (`evidence.py`, migration 018): command, diff, acceptance
- Rules-v2 completion gate (`evidence_evaluator.py`) with independent reviewer verdicts
- Task risk assessment (`risk.py`) for migrations, secrets, protected paths
- Review packets v2 (`review_packets_v2.py`) under `.coordinator/review_packets_v2/`
- Engine done-gate: evidence + risk required before `done`
- Supervisor RPCs: `project.evidence`, `project.review`, `project.risk`, `project.merge_ready`
- CLI/TUI slash commands: `/evidence`, `/review`, `/risk`, `/merge-ready`

Single global Supervisor preserved. No second daemon. No auto-merge policy expansion.
Worker output cannot forge `rules-v2` reviewer verdicts.

## Commit Stack (Tasks 0–8, 10)

| Task | Message |
| --- | --- |
| 0 | `test: capture Phase 8 evidence contracts` |
| 1 | `feat: persist task evidence records` |
| 2 | `feat: collect command and diff evidence` |
| 3 | `feat: evaluate acceptance evidence` |
| 4 | `feat: add task risk assessment` |
| 5 | `feat: require evidence before done` |
| 6 | `feat: write evidence review packets` |
| 7 | `feat: expose evidence review RPCs` |
| 8 | `feat: add evidence review slash commands` |
| 10 | `docs: document Phase 8 evidence review gates` |

## Review Status

| Reviewer | Verdict | Handoff |
| --- | --- | --- |
| Codex Gate E | PASS | User re-sign 2026-06-29 (85/85 focused RPC/PTY) |
| Gemini Gate F | PASS | `docs/superpowers/handoffs/2026-06-29-phase8-gemini-review.md` |
| Codex Gate G | Pending | This document |

## Operator Quick Start

```bash
coordinator --print -p "/evidence task-abc"
coordinator --print -p "/review task-abc"
coordinator --print -p "/risk task-abc"
coordinator --print -p "/merge-ready task-abc"
```

Use these to see which commands passed, what files changed, which acceptance
criteria are covered, why human review is required, and whether merge is allowed
under the repo's existing `review_policy`.

## Gate E Commands (Codex re-sign 2026-06-29)

```bash
git diff --check
# PASS

PYTHONPATH=src python3 -m unittest \
  tests.test_supervisor_methods tests.test_cli_prompt tests.test_tui_pty \
  tests.test_phase8_evidence_review_e2e -v
# Ran 85 tests in 266.464s — OK

npm run typecheck --prefix ui-tui
# PASS

npm run lint --prefix ui-tui
# PASS

npm test --prefix ui-tui -- --run
# 154/154 PASS
```

Notes: PTY tests emit Python 3.13 `os.fork()` DeprecationWarning; Vitest logs one
existing `coordinator-tui: uncaught: Error: test failure` noise line. Both
commands exited 0.

## Gate G Commands

```bash
git diff --check
PYTHONPATH=src python3 -m unittest \
  tests.test_evidence \
  tests.test_evidence_evaluator \
  tests.test_risk \
  tests.test_review_packets_v2 \
  tests.test_phase8_evidence_review_e2e -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_bundle tests.test_wheel_migrations -v
python3 -m build
```

Clean-wheel smoke without `PYTHONPATH`:

```bash
python3 -m venv /tmp/coord-phase8-venv
/tmp/coord-phase8-venv/bin/pip install dist/*.whl
env -u PYTHONPATH /tmp/coord-phase8-venv/bin/coordinator init --dry-run --json
env -u PYTHONPATH COORDINATOR_HOME=/tmp/coord-phase8-home /tmp/coord-phase8-venv/bin/coordinator init --yes --json
env -u PYTHONPATH COORDINATOR_HOME=/tmp/coord-phase8-home /tmp/coord-phase8-venv/bin/coordinator doctor --json
```

## Safety Properties (verified by tests + Gemini review)

- Command, diff, acceptance, risk, and reviewer evidence are durable and project-scoped
- Failed commands and no-op code tasks cannot be hidden from completion
- Done-state requires evidence and rules-v2 evaluator approval
- Risky changes route to human review per repo policy
- Review packets v2 stay under repo root with secret redaction
- `/merge-ready` does not claim readiness when human review is required
- Slash commands route through Supervisor RPC only