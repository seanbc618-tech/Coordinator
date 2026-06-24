# Claude Code Handoff: Phase 5.2 Tests and Documentation Only

Your scope is intentionally mechanical. Do not modify production architecture.

Repository: `/Users/xiafan/Coordinator`
Branch: `external/coordinator-global-tui`
Plan: `docs/superpowers/plans/2026-06-25-phase5-2-conversation-runtime.md`

## Task 0: Red Tests

Add only the reproduction tests listed in Plan Task 0:

- greetings/questions create zero tasks;
- explicit task request can create work;
- internal admission language is hidden;
- 40/80/120-column layout fixtures;
- unknown slash remains local.

Run focused tests and confirm the new assertions fail for the intended reason.
Commit:

```text
test: capture Phase 5.2 runtime and conversation regressions
```

Then stop. Send Grok the commit hash. Do not make production tests pass by
loosening assertions.

Do not edit `tests/test_supervisor_process.py` or
`tests/test_supervisor_cli.py`; Grok owns runtime lifecycle and its tests.

## Task 5: Fixtures and Docs

Start only after Grok Tasks 1–4 pass Gemini review.

- update `tests/fixtures/fake_supervisor.py`;
- add deterministic PTY fixtures;
- update `docs/tui.md`;
- update `docs/troubleshooting.md`;
- write the acceptance handoff with exact command output.

Allowed production change: none.

Use two commits:

```text
test: cover Phase 5.2 TUI conversation flows
docs: document trusted runtime and conversational Commander
```

If a test exposes a product bug, report the reproduction to Grok instead of
patching production code.
