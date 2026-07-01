# Phase 17 Autonomy Simulation — Acceptance Handoff

**Branch:** `phase16-20-implementation`  
**Base:** `main` @ Phase 15 merged  
**Status:** Ready for Gemini Gate F + Codex Gate G

## Scope delivered

- Migration 027: `simulation_runs`, `simulation_events`, `simulation_forecasts`
- Dry-run autonomy simulator over scheduler, budget, capacity, approvals, and agent usage
- Persisted simulation reports with reproducible forecasts
- CLI: `coordinator simulate overnight`, `coordinator simulate --project`
- RPCs: `simulation.run`, `simulation.report`, `simulation.list`
- Slash: `/simulate`, `/simulate project`, `/what-if` (onboard `/simulate` preset preserved)

## Gemini design red lines (tests)

- Simulation does not mutate tasks, leases, PRs, or worktrees
- Paused projects skipped in scheduler forecast
- Reports listable and fetchable by id
- Onboard preset simulate remains distinct from autonomy simulation

## Verification (Grok)

```bash
git diff --check
PYTHONPATH=src python3 -m unittest \
  tests.test_autonomy_simulator \
  tests.test_simulation_reports \
  tests.test_phase17_simulation_e2e -v
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
coordinator simulate overnight --json
coordinator --print -p "/simulate"
```

## Out of scope

Real autonomous execution, LLM-only predictions, hidden task admission.