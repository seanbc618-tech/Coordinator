# Phase 9 GitHub Delivery Loop — Acceptance Handoff

**Branch:** `phase9-github-delivery-loop` (based on `phase8-evidence-review`)  
**Implementer:** Grok  
**Status:** Codex Gate G PASS (after overnight.enabled fix)

## Delivered

- Migration `019_github_delivery_loop.sql` (mirrored)
- Durable `delivery_records` and `delivery_events` helpers
- Safe `github_cli.py` adapter (`argv` only, fake `gh` fixture)
- `delivery_policy.py` gates (evidence, merge-ready, allow_push, human review)
- Evidence-backed PR create/update and CI polling
- Bounded `ci_repair` recovery proposals
- RPCs: `project.deliver`, `project.prs`, `project.ci`, `project.delivery`, `project.merge_policy`
- Slash commands: `/deliver`, `/prs`, `/ci`, `/delivery`, `/merge-policy`

## User-facing commands

```bash
coordinator --print -p "/merge-ready task-abc"
coordinator --print -p "/deliver task-abc"
coordinator --print -p "/prs"
coordinator --print -p "/ci task-abc"
coordinator --print -p "/delivery task-abc"
coordinator --print -p "/merge-policy"
```

## Gate commands (Grok verified locally)

### Gate A — Red tests (post Task 0)

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_github_cli \
  tests.test_github_delivery \
  tests.test_delivery_policy \
  tests.test_delivery_recovery \
  tests.test_phase9_github_delivery_e2e -v
```

### Gate C — Policy and evidence

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_delivery_policy \
  tests.test_github_delivery \
  tests.test_push_merge \
  tests.test_phase8_evidence_review_e2e -v
```

### Gate E — RPC and TUI

```bash
PYTHONPATH=src python3 -m unittest tests.test_supervisor_methods tests.test_cli_prompt tests.test_tui_pty tests.test_phase9_github_delivery_e2e -v
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
```

**Gate G result (post-fix):** 1166 Python tests OK; Phase 9 suite 24/24 OK;
ui-tui typecheck/lint/test/build OK; wheel + clean-wheel smoke OK.

**Gate G blocker resolved:** `maybe_pause_for_quiet_hours()` now honors
`config.overnight.enabled=False` (default), preventing time-dependent Phase 6C
autonomous-run pauses during UTC 22:00–08:00.

## Stop points

- **Codex Gate A/C/E/G** — independent verification (Grok stops here per dispatch)
- **Gemini Gate B/D/F** — adversarial review (`docs/superpowers/handoffs/2026-06-30-phase9-gemini-review.md`)

## Out of scope (unchanged)

- Auto-merge expansion
- Live GitHub REST integration
- Infinite CI retries