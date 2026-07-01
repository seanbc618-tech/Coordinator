# Phase 14 Daily Operator Hardening — Acceptance Handoff

**Branch:** `phase14-daily-operator-hardening`  
**Base:** `main` @ Phase 13 merged  
**Status:** Ready for Gemini Gate F + Codex Gate G

## Scope delivered

- Migration 024: `diagnostic_runs`, `repair_audit_events`, `global_control_events`,
  `agent_health_snapshots`, `morning_handoffs`, plus `projects.status` /
  `projects.pause_reason`
- Safe doctor repair: dry-run default, whitelisted apply, symlink/PID guards
- Global pause/resume with durable audit and whitelist-only resume
- Operator dashboard: global pause, task/approval/PR/health counts, next actions
- Failure explanations: deterministic `/why <task-id>` without LLM or full logs
- Agent health from durable attempts (no external agent CLI invocations)
- Morning handoff persisted and reproducible from durable state
- CLI: `doctor --repair`, `pause --all`, `resume --all`, `operator summary --morning`
- RPCs: `operator.doctor`, `operator.repair`, `operator.explain_failure`,
  `operator.health`, `operator.morning`, `global.pause`, `global.resume`
- Slash: `/doctor`, `/repair`, `/health`, `/morning`, `/why <task-id>`,
  `/pause all`, `/resume all`, enriched `/dashboard`

## Gemini design red lines (tests)

- Symlink lock/socket paths outside `COORDINATOR_HOME` are skipped
- Stale lock removal requires absent PID (`os.kill(pid, 0)`)
- `resume --all` restores only projects from the latest global pause whitelist

## Verification (Grok)

```bash
git diff --check
PYTHONPATH=src python3 -m unittest \
  tests.test_doctor_repair \
  tests.test_operator_dashboard \
  tests.test_failure_explainer \
  tests.test_agent_health \
  tests.test_morning_handoff \
  tests.test_global_controls \
  tests.test_phase14_daily_operator_e2e -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_bundle tests.test_wheel_migrations -v
python3 -m build
```

**Focused results:** 48/48 Phase 14 tests pass; 155/155 ui-tui tests pass.

## Clean-wheel smoke (Gate G)

After `pip install` of the built wheel (no `PYTHONPATH`):

```bash
coordinator init --dry-run --json
coordinator init --yes --json
coordinator doctor --repair --dry-run --json
coordinator --print -p "/dashboard"
coordinator --print -p "/health"
coordinator --print -p "/morning"
coordinator pause --all --reason "gate smoke"
coordinator --print -p "/dashboard"
coordinator resume --all
```

Register a project before slash commands on a fresh `COORDINATOR_HOME`.

## Out of scope

Web dashboard, production SaaS notification integrations, automatic source-repo repair.