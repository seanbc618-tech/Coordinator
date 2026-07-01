# Phase 19 User Preference Rules — Acceptance Handoff

**Branch:** `phase16-20-implementation`  
**Base:** `main` @ Phase 15 merged  
**Status:** Ready for Gemini Gate F + Codex Gate G

## Scope delivered

- Migration 029: `preference_observations`, `preference_rules`, `preference_rule_evidence`
- Observation layer for approvals, rejections, retries, route overrides, and command patterns
- Evidence-backed suggested rules (inactive until approved)
- Approve, reject, disable, delete, and export flows
- Router scoring hints from approved rules (no permission grants)
- CLI/RPC/slash: `/preferences`, `/learned`, `/prefer`, `/forget`

## Gemini design red lines (tests)

- Forbidden permission keys rejected at rule creation
- Suggested rules inactive until explicit approval
- Observations redact secrets in evidence payloads
- Project-scoped rules do not affect other projects
- Approved agent preference influences route preview only

## Verification (Grok)

```bash
git diff --check
PYTHONPATH=src python3 -m unittest \
  tests.test_preference_observer \
  tests.test_preference_suggestions \
  tests.test_preference_rules \
  tests.test_phase19_preferences_e2e -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_bundle tests.test_wheel_migrations -v
python3 -m build
```

## Clean-wheel smoke (Gate G)

```bash
coordinator --print -p "/preferences"
coordinator --print -p "/learned"
```

## Out of scope

Keystroke tracking, hidden personalization, remote profile sync, policy bypass.