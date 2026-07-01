# Phase 13 External Approval Channels — Acceptance Handoff

**Branch:** `phase13-external-approval-channels`  
**Base:** `main` @ Phase 12 merged  
**Status:** Ready for Codex Gate G (pending Gemini Gate F)

## Scope delivered

- Migration 023: `approval_requests`, `approval_channel_configs`, `approval_deliveries`, `approval_audit_events`
- One-time hashed approval tokens with expiry, single-use consume, and replay rejection
- Approval requests from policy-gated operator decisions
- Safe local channels: file inbox (enabled), stdout (disabled by default), macOS (disabled), webhook (dry-run), command (policy-gated)
- CLI: `coordinator approve <token> --yes`, `coordinator reject <token>`
- RPCs: `operator.approvals`, `operator.approval.create`, `operator.approval.approve`, `operator.approval.reject`, `operator.channels`
- Slash: `/approvals`, `/channels`, `/reject`, `/notify test` (existing `/approve` remains task approval)

## Verification (Grok)

```bash
git diff --check
PYTHONPATH=src python3 -m unittest \
  tests.test_approval_channels \
  tests.test_approval_tokens \
  tests.test_approval_callbacks \
  tests.test_macos_notifications \
  tests.test_webhook_notifications \
  tests.test_phase13_external_approval_e2e -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_bundle tests.test_wheel_migrations -v
python3 -m build
```

**Focused results:** 26/26 Phase 13 tests pass; 154/154 ui-tui tests pass.

## Clean-wheel smoke (Gate G)

After `pip install` of the built wheel (no `PYTHONPATH`):

```bash
coordinator init --dry-run --json
coordinator init --yes --json
coordinator project add <repo-path> --yes
coordinator --print -p "/channels"
coordinator --print -p "/approvals"
coordinator --print -p "/notify test"
```

Register the project before slash commands on a fresh `COORDINATOR_HOME`.

## Out of scope

Production Gmail/Slack/Discord integrations — not implemented on this branch.