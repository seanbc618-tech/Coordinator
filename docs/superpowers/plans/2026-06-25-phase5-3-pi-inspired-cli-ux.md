# Phase 5.3 Pi-Inspired CLI UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let operators send Commander messages from the shell, print structured results without Ink, continue the latest project goal, and inspect config — all through the global Supervisor path Phase 5.2 established.

**Architecture:** Extend `cli.py` with prompt/print/mode flags, add a small headless Supervisor client module, reuse `tui_launcher` project resolution and runtime trust checks, and keep Ink as the optional follow-on UI.

**Tech Stack:** Python 3.13, argparse, Unix sockets, existing Supervisor/Commander stack, unittest.

**Design:** `docs/superpowers/specs/2026-06-25-phase5-3-pi-inspired-cli-ux-design.md`

---

## Ownership

- **Grok:** production implementation and integration commits.
- **Gemini:** read-only adversarial review after every Grok task.
- **Claude Code:** red tests, fixtures, docs, gate output collection only.
- **Codex:** final Gate E acceptance (full suite, wheel, smoke).

Claude must not modify Commander schema, admission policy, or TUI/Ink architecture.

## Task 0: Freeze Reproductions

**Owner:** Claude Code

**Files:**
- Create: `tests/test_cli_prompt.py`
- Modify: `tests/test_tui_launcher.py` (only if needed for shared helpers)

- [ ] Add failing tests for `coordinator -p "你好"` print mode: uses Supervisor
  `chat.send`, stdout contains `user_reply`, no TUI spawn.
- [ ] Add failing test for `--mode json`: valid JSON envelope with stable keys.
- [ ] Add failing test for unknown project path exit code != 0.
- [ ] Add failing test for `--continue` binding latest non-terminal goal.
- [ ] Add failing test for `coordinator config` read-only output sections.
- [ ] Add failing test proving legacy `coordinator chat` path is unchanged (no
  accidental regression).
- [ ] Run only new tests; confirm failures for the intended reason.
- [ ] Commit as `test: capture Phase 5.3 CLI prompt regressions`.

## Task 1: CLI Argument Model

**Owner:** Grok

**Files:**
- Modify: `src/local_cli_coordinator/cli.py`
- Test: `tests/test_cli_prompt.py`

- [ ] Add top-level optional prompt positional: `coordinator [prompt ...]`.
- [ ] Add `-p` / `--prompt`, `--print`, `--mode {text,json}`, `--continue`,
  `--no-tui`.
- [ ] Parsing must not break existing subcommands (`supervisor`, `project`, etc.).
- [ ] `--print` implies `--no-tui`.
- [ ] Commit as `feat: add Pi-inspired CLI prompt flags`.

**Gate A:** Gemini checks argparse collisions, help text, and backward compat with
no-arg `coordinator` TUI launch.

## Task 2: Headless Supervisor Chat Client

**Owner:** Grok

**Files:**
- Create: `src/local_cli_coordinator/cli_chat.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Modify: `src/local_cli_coordinator/tui_launcher.py` (shared helpers only)
- Test: `tests/test_cli_prompt.py`

- [ ] Resolve git root and registered `project_id` (same rules as TUI launcher).
- [ ] Call `ensure_supervisor()` and enforce Phase 5.2 compatibility.
- [ ] Implement `chat.send` RPC with project-scoped envelope.
- [ ] Wait for `commander.completed` or structured `chat.send` result (match
  existing Supervisor contract).
- [ ] Map Commander failures to exit codes (missing project, no goal, timeout).
- [ ] Commit as `feat: route CLI prompts through Supervisor chat.send`.

**Gate B:** Gemini tests missing project, incompatible Supervisor, no goal,
terminal goal, and RPC timeout.

## Task 3: Text and JSON Output Envelopes

**Owner:** Grok

**Files:**
- Modify: `src/local_cli_coordinator/cli_chat.py`
- Test: `tests/test_cli_prompt.py`

- [ ] `--mode text`: print `user_reply` only (plus admitted task summary sentence
  when tasks are created — reuse Phase 5.2 operator language).
- [ ] `--mode json`: emit stable public CLI envelope (see design spec).
- [ ] Leading `/` in print mode dispatches local slash handlers where they exist
  (`/status`, `/tasks`) without calling Commander.
- [ ] Commit as `feat: add CLI text and JSON output modes`.

**Gate C:** Gemini checks JSON stability, slash local dispatch, and no internal
orchestration leakage to stdout.

## Task 4: Continue Latest Goal

**Owner:** Grok

**Files:**
- Modify: `src/local_cli_coordinator/cli_chat.py`
- Modify: `src/local_cli_coordinator/goals.py` (read-only helper if needed)
- Test: `tests/test_cli_prompt.py`

- [ ] `--continue` selects latest non-terminal goal for resolved project.
- [ ] Clear error when no continuable goal exists.
- [ ] Commit as `feat: add CLI --continue goal binding`.

## Task 5: Read-Only Config Command

**Owner:** Grok

**Files:**
- Modify: `src/local_cli_coordinator/cli.py`
- Create: `src/local_cli_coordinator/cli_config.py`
- Test: `tests/test_cli_prompt.py`

- [ ] Add `coordinator config` subcommand.
- [ ] Show agents, repo allowlist, budget caps, runtime paths, Commander agent.
- [ ] Surface actionable validation errors (missing agent binary, empty allowlist).
- [ ] Commit as `feat: add read-only coordinator config command`.

## Task 6: Prompt + Optional TUI Handoff

**Owner:** Grok

**Files:**
- Modify: `src/local_cli_coordinator/cli.py`
- Test: `tests/test_cli_prompt.py`, `tests/test_global_tui_e2e.py` (if needed)

- [ ] Default: after successful prompt send, call `launch_tui()` unless
  `--no-tui` or `--print`.
- [ ] Ensure pre-sent message appears in TUI history via normal event replay.
- [ ] Commit as `feat: open TUI after CLI prompt by default`.

**Gate D:** Gemini checks double-send, missing replay, and detach behavior.

## Task 7: Docs and Examples

**Owner:** Claude Code

**Files:**
- Modify: `docs/tui.md` or create `docs/cli.md`
- Modify: `docs/troubleshooting.md` (if needed)

- [ ] Document prompt, print, json, continue, config, and `--no-tui`.
- [ ] Add polymarket-oriented examples.
- [ ] Commit as `docs: document Pi-inspired CLI prompt modes`.

## Task 8: Integration and Acceptance

**Owner:** Grok + Claude Code gates

- [ ] Run TypeScript gates (must remain green — no TUI source changes expected).
- [ ] Run isolated XDG full Python suite.
- [ ] Run focused `tests.test_cli_prompt` + PTY/E2E smoke subset.
- [ ] Run wheel tests.
- [ ] Real smoke in `/Users/xiafan/polymarket-crypto-threshold`:

```bash
coordinator --print -p "你好"
coordinator --mode json -p "现在有什么任务？"
coordinator --continue -p "生成一个只读验收任务"
coordinator config
coordinator "打开 TUI 继续"
```

- [ ] Claude Code records gate output in acceptance handoff.
- [ ] Gemini Gate D + Codex Gate E sign-off.

## Final Gates

- **Gate D (Gemini):** adversarial review of prompt/print/json/continue/config.
- **Gate E (Codex):** independent full-suite + wheel + smoke rerun.

No merge before Gate E PASS.

## Out of Scope (Phase 5.3)

- `@file` references
- `--resume` / `--fork`
- tool restriction flags
- async Commander queue
- Pi source import

See Phase 5.4 backlog in the design spec open questions.