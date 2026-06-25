# Phase 5.3 Planning Kickoff

Date: 2026-06-25
Branch: `external/coordinator-global-tui` (pushed at `bce4152`)
Previous phase: Phase 5.2 signed at `d368fe8` (Codex Gate E PASS)

## Phase 5.2 Sign-Off Summary

| Gate | Role | Result |
|------|------|--------|
| Task 6 automation | Claude Code | PASS |
| Gate D adversarial | Gemini | PASS |
| Gate E acceptance | Codex | PASS |

Delivered: runtime identity, Commander schema v2, operator-language outcomes,
line-aware TUI, deterministic fixtures, trusted restart.

## Phase 5.3 Objective

**Pi-Inspired CLI UX** — headless prompt/print/json/continue/config on top of the
global Supervisor path. Ink TUI remains the interactive shell.

- Design: `docs/superpowers/specs/2026-06-25-phase5-3-pi-inspired-cli-ux-design.md`
- Plan: `docs/superpowers/plans/2026-06-25-phase5-3-pi-inspired-cli-ux.md`

## Agent Start Order

```text
Claude Code  → Task 0 red tests → stop, send hash to Grok
Grok         → Tasks 1–6 implementation (one commit per task)
Gemini       → Gate review after each Grok commit
Claude Code  → Task 7 docs
Claude Code  → Task 8 gate recording
Gemini       → Gate D final review
Codex        → Gate E sign-off
```

## Ownership Quick Reference

| Agent | Scope |
|-------|-------|
| **Grok** | `cli.py`, new `cli_chat.py` / `cli_config.py`, Supervisor routing |
| **Claude Code** | `tests/test_cli_prompt.py`, docs, acceptance output |
| **Gemini** | Adversarial review only |
| **Codex** | Independent Gate E |

## Non-Negotiables (carry forward from 5.2)

- Route prompts through Supervisor `chat.send`, not legacy `coordinator chat` SQLite REPL.
- Enforce runtime compatibility before any RPC.
- Greetings/status questions create zero tasks.
- Visible output uses `user_reply`; no internal admission vocabulary on stdout.
- Do not replace Hermes/Ink or bypass repo policy.

## Deferred to Phase 5.4

`@file` context, `--resume`/`--fork`, tool restriction flags, `rpc` output mode.

## First Action

**Claude Code:** start Task 0 — `test: capture Phase 5.3 CLI prompt regressions`

**Grok:** wait for Task 0 hash, then read
`docs/superpowers/handoffs/2026-06-25-phase5-3-grok-implementation.md`