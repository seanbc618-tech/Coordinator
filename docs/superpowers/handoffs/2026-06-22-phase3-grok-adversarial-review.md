# Phase 3 Grok Adversarial Review Handoff

## Role

Review Claude Code's Phase 3 implementation against:

- `docs/superpowers/specs/2026-06-20-global-multi-project-tui-design.md`
- `docs/superpowers/plans/2026-06-20-hermes-coordinator-tui.md`
- `docs/superpowers/handoffs/2026-06-22-phase3-claude-code-implementation.md`

Do not rewrite production code during review. Reproduce defects, rank them P0 to
P2, identify the responsible task commit, and return a focused repair request.

## Review Waves

### Gate A: Task 1 License and Scope

- Compare every adapted file with `/Users/xiafan/.hermes`.
- Verify substantial copied portions have attribution and MIT notice coverage.
- Audit package dependencies and bundle contents.
- Reject Hermes runtime, gateway, model, provider, MCP, memory, skill, voice,
  image, telemetry, sidecar, or absolute local-path dependencies.

### Gate B: Tasks 2 and 3 Protocol and State

- Fuzz newline framing, malformed JSON, oversized messages, protocol mismatch,
  timeout, disconnect during response, and request correlation.
- Test reconnect replay, duplicate cursors, cursor gaps, out-of-order events,
  foreign project events, and two simultaneous clients.
- Confirm reducer purity and bounded output memory.

### Gate C: Task 4 Layout

- Inspect 120-, 80-, and 50-column renders.
- Attack long unbroken commands, Unicode width, rapid streaming, empty state,
  large activity counts, fallback labels, resize, and footer stability.
- Reject overlap, clipped controls, unstable dimensions, or unreadable narrow
  layouts.

### Gate D: Tasks 5 and 6 Interaction and Lifecycle

- Exercise multiline editing, history boundaries, bracketed paste, completion,
  busy submission, Tab ambiguity, Ctrl+C, `/quit`, `/stop`, and `/shutdown`.
- Verify destructive confirmation cannot be bypassed by reconnect or repeated
  input.
- Kill the socket, Supervisor, and TUI at each lifecycle stage; verify terminal
  restoration and that detach never stops workers.
- Check reconnect backoff for tight loops and replay duplication.

### Gate E: Task 7 PTY and Bundle

- Run PTY tests repeatedly at 120, 80, and 50 columns and during resize.
- Kill the TUI during active fake work and prove work continues.
- Inspect the production bundle and source map for test code, secrets, local
  paths, and forbidden Hermes runtime modules.
- Verify manifest protocol version/build hash and deterministic rebuild behavior.

## Required Commands

```bash
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_pty -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
git diff --check
```

## Report Format

1. Findings first, ordered by severity with file and line references.
2. Exact reproduction command and observed output for every blocking finding.
3. Plan acceptance matrix for Tasks 1 through 7.
4. Hermes attribution and forbidden-import audit.
5. Test counts and commands actually run.
6. Explicit verdict: reject, conditional pass, or pass.

Only Codex may declare Phase 3 accepted.
