# Claude Code Handoff: Phase 5.4 Tests and Documentation

Repository: `/Users/xiafan/Coordinator`
Branch: `external/coordinator-global-tui`
Baseline: `0f43ac6`
Plan: `docs/superpowers/plans/2026-06-26-phase5-4-context-sessions-tools.md`

Your work is deliberately mechanical. Do not edit production code.

## Wave A: Task 0

Create only `tests/test_cli_file_context.py` with the exact attacks and
persistence assertions in Plan Task 0. Run it red and commit:

```text
test: capture Phase 5.4 file context requirements
```

Stop and send the hash to Grok.

## Wave B: Task 4

Start only after Codex Gate A PASS. Create only
`tests/test_goal_sessions.py`. Cover every resume state, project isolation,
conflicts, fork lineage, no copied execution history, candidate output, and
parser mutual exclusion. Commit:

```text
test: capture Phase 5.4 goal session requirements
```

Stop and send the hash to Grok.

## Wave C: Task 7

Start only after Codex Gate B PASS. Create only
`tests/test_execution_policy.py`. Cover parsing, intersection, persistence,
admission, engine stage enforcement, and RPC envelopes. Commit:

```text
test: capture Phase 5.4 execution policy and RPC requirements
```

Stop and send the hash to Grok.

## Final Support: Task 10

After Grok Tasks 8–9 pass their focused tests:

- create `tests/test_phase5_4_e2e.py`;
- update fake Supervisor/Commander fixtures;
- update `docs/cli.md` and `docs/troubleshooting.md`;
- create the Phase 5.4 acceptance handoff.

Use two commits:

```text
test: add Phase 5.4 integrated CLI workflow
docs: document context sessions tools and RPC mode
```

## Forbidden Scope

Do not modify:

- `context_files.py`
- `goal_sessions.py`
- `execution_policy.py`
- `engine.py`
- Commander/Supervisor production modules
- migrations

If a red test reveals a design ambiguity or bug, report the exact reproduction
to Grok. Do not patch around it or relax the assertion.
