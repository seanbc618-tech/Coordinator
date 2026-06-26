# Phase 5.3 Pi-Inspired CLI UX Design

Date: 2026-06-25
Branch: `external/coordinator-global-tui`
Baseline: Phase 5.2 signed at `bce4152` (gates at `d368fe8`)
Inspiration: `docs/superpowers/specs/2026-06-23-pi-inspired-coordinator-ux.md`

## Problem

Phase 5.2 made the TUI trustworthy and conversational, but Coordinator still
requires opening Ink for every interaction. Operators and scripts cannot:

- send one Commander message from the shell;
- print a reply without a terminal UI;
- consume structured JSON for automation;
- resume the current project's latest active goal from the CLI;
- inspect install/config/runtime paths without reading TOML.

The legacy `coordinator chat` REPL still talks to per-repo SQLite directly and
does not use the global Supervisor `chat.send` path the TUI relies on.

## Decision

Add a **headless CLI front door** that reuses the existing global Supervisor,
project registry, runtime identity checks, and Commander schema v2 contract.

Keep Hermes/Ink as the interactive UI. Phase 5.3 does not replace the TUI
substrate, bundle packaging, PTY behavior, or socket protocol.

### In scope (Phase 5.3)

1. **Prompt entry**

```bash
coordinator "检查这个项目还有哪些可以自动改进"
coordinator -p "生成三个很小的后续任务"
```

2. **Print mode** — no Ink UI:

```bash
coordinator --print "总结当前项目状态"
coordinator -p "/status"
```

3. **Output modes**

```bash
coordinator --mode text -p "hello"      # default human text
coordinator --mode json -p "/status"    # stable JSON envelope
```

4. **Continue latest goal**

```bash
coordinator --continue -p "下一步做什么？"
```

Maps to the current project's latest non-terminal goal before calling
`chat.send`.

5. **Read-only config surface**

```bash
coordinator config
```

Shows agents, repo allowlist, budget caps, XDG/runtime paths, and actionable
validation errors. Editing deferred.

6. **Default interactive behavior**

When a prompt is given without `--print`, send the message then open the TUI
unless `--no-tui` is set (explicit opt-out for scripting wrappers).

### Out of scope (defer to Phase 5.4+)

- `@file` context attachments
- `--resume` / `--fork` pickers
- `--tools` / `--no-tools` policy overlays
- `rpc` output mode
- Replacing `coordinator chat` REPL internals (may deprecate later)
- Streaming/async Commander jobs
- Multi-project switching UX

## Architecture

```text
coordinator CLI (new prompt flags)
        │
        ├─ resolve git root → global project registry
        ├─ ensure_supervisor() + runtime compatibility check
        ├─ bind goal (--continue or active non-terminal goal)
        └─ Supervisor RPC chat.send
                │
                ├─ text/json stdout (print mode)
                └─ optional launch_tui() (default after prompt)
```

### JSON envelope (public CLI schema)

Smaller than the full Supervisor protocol. Minimum fields:

```json
{
  "ok": true,
  "project_id": "proj-…",
  "goal_id": 1,
  "user_reply": "…",
  "intent": "conversation",
  "admitted": 0,
  "rejected": 0,
  "accepted_task_ids": [],
  "error": null
}
```

Errors use `"ok": false` with a stable `error.code` string.

### Semantic rules (inherit Phase 5.2)

- Greetings and status questions must not admit tasks.
- Only explicit task requests may create work.
- Visible stdout uses `user_reply`; admission diagnostics stay structured.
- Repo policy, budgets, and review gates cannot be bypassed by CLI flags.

## Interaction Examples

```bash
# One-shot conversation, then open TUI
coordinator "你好"

# Script-friendly status without UI
coordinator --print --mode json -p "现在有什么任务？"

# Continue the latest active goal for this repo
coordinator --continue -p "继续推进"

# Diagnose install/runtime
coordinator config
```

## Acceptance Criteria

- `coordinator "hello"` from a registered git repo uses Supervisor `chat.send`.
- `coordinator --print -p "你好"` prints `user_reply` and exits without Ink.
- `coordinator --mode json -p "/status"` prints valid JSON with stable keys.
- `coordinator --continue -p "…"` binds the latest non-terminal project goal.
- `coordinator config` surfaces missing agents/repos and XDG path issues.
- Incompatible Supervisor still fails with restart guidance (Phase 5.2 behavior).
- Existing TUI PTY, E2E, wheel, and full Python suites remain green.

## Open Questions

- Should slash-like text in `-p "/status"` stay local (like TUI) or always go to
  Commander? **Recommendation:** treat leading `/` as local command dispatch in
  print mode for parity with TUI slash routing.
- Should `--print` imply `--no-tui`? **Recommendation:** yes.
- Deprecate `coordinator chat` in docs now or in Phase 5.4?

## Recommendation

Ship Phase 5.3 as a bounded CLI layer only. Keep file context and session
operators for Phase 5.4 after prompt/print/json/config prove stable in real
projects.