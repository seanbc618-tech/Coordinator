# Phase 16 Agent Capability Routing — Acceptance Handoff

**Branch:** `phase16-20-implementation`  
**Base:** `main` @ Phase 15 merged  
**Status:** Ready for Gemini Gate F + Codex Gate G

## Scope delivered

- Migration 026: `agent_capability_profiles`, `agent_benchmark_runs`, `agent_route_decisions`, `agent_fallback_edges`
- Capability profiles synced from `agents.toml` with durable overrides
- Local fixture benchmarks (no paid-provider calls)
- Explainable router scoring with bounded fallback graph
- Route decisions persisted on task claim
- CLI: `agent list`, `agent show`, `route preview`, `agent benchmark`
- RPCs: `agent.list`, `agent.detail`, `agent.route_preview`, `agent.benchmark`
- Slash: `/agents`, `/agent <id>`, `/route <task-id>`, `/benchmark agents`

## Gemini design red lines (tests)

- Disabled capability profiles excluded from routing
- Benchmark runner rejects blocked provider commands
- Fallback graph respects max hops and avoids cycles
- Route preview does not mutate tasks or leases

## Verification (Grok)

```bash
git diff --check
PYTHONPATH=src python3 -m unittest \
  tests.test_agent_capabilities \
  tests.test_agent_benchmarks \
  tests.test_agent_router \
  tests.test_phase16_agent_routing_e2e -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_bundle tests.test_wheel_migrations -v
python3 -m build
```

## Clean-wheel smoke (Gate G)

After `pip install` of the built wheel (no `PYTHONPATH`):

```bash
coordinator init --yes --json
coordinator --print -p "/agents"
coordinator --print -p "/route task-1"
```

Register a project before slash commands on a fresh `COORDINATOR_HOME`.

## Out of scope

Automatic agent installation, cloud benchmark farms, policy bypass via routing.