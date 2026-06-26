# Pi-Inspired Coordinator UX Backlog

Date: 2026-06-23

## Decision

Coordinator will continue using the existing Hermes/Ink-derived TUI shell as the runtime UI foundation.

The Pi agent will be used as UX inspiration only. We will not replace the TUI substrate, process lifecycle, PTY handling, bundle packaging, or Supervisor socket integration that Wave 4 already stabilized and tested.

## Rationale

Hermes/Ink already solves the hard local runtime problems for Coordinator:

- PTY rendering and keyboard input
- Ctrl+C, SIGTERM, SIGHUP, and detach behavior
- bundled TUI assets inside the Python wheel
- Supervisor socket connection, reconnect, and replay
- project onboarding from the current repository directory
- PTY and E2E test coverage

Pi's advantage is not clearly in the terminal substrate. Its stronger ideas are in the lightweight command-line product model: quick prompts, session continuation, file context, machine-readable output modes, and configuration UX.

Coordinator should absorb those ideas after Commander-backed chat works.

## Placement

This is not part of Phase 5 P0.

Recommended sequencing:

1. Finish Phase 5: Commander Intelligence Integration.
2. Verify TUI chat can create or use goals, call Commander, admit tasks, and report status.
3. Start Phase 5.1: Pi-Inspired Coordinator UX.

## Borrowed UX Ideas

### 1. Start With A Prompt

Support one-shot prompt entry from any registered project:

```bash
coordinator "检查这个项目还有哪些可以自动改进"
coordinator "生成三个很小的后续任务"
```

Expected behavior:

- launch or attach the global Supervisor
- resolve the current git repository into a registered project
- send the prompt through the same `chat.send` path as the TUI
- open the TUI by default unless `--print` or `--mode` is set

### 2. Print Mode

Support non-interactive execution:

```bash
coordinator -p "总结当前项目状态"
coordinator --print "生成一个任务草稿"
```

Expected behavior:

- no Ink UI
- no interactive onboarding prompt unless explicitly allowed
- stdout contains the visible Commander response
- exit code is non-zero on missing project, missing goal, or Commander failure

### 3. Machine-Readable Modes

Support script-friendly output:

```bash
coordinator --mode json -p "/status"
coordinator --mode rpc -p "生成一个小任务"
```

Minimum modes:

- `text`: human-readable output
- `json`: stable JSON result envelope

`rpc` can be deferred unless another local tool needs it.

### 4. File Context Inputs

Support lightweight file references:

```bash
coordinator @README.md "基于这个文档补验收任务"
coordinator @docs/tui.md @docs/install.md "检查文档是否互相矛盾"
```

Rules:

- file paths are resolved relative to the current working directory
- reject files outside the repo unless explicitly allowed
- apply size limits before sending to Commander
- store file references in Commander messages or run metadata

### 5. Continue, Resume, Fork

Support session-like operators:

```bash
coordinator --continue
coordinator --resume
coordinator --fork <conversation-or-goal-id>
```

Coordinator should map these to project-scoped goal and Commander conversation state, not copy Pi's session storage model blindly.

Suggested interpretation:

- `--continue`: continue the latest non-terminal goal for the current project
- `--resume`: list/select previous project goals or Commander runs
- `--fork`: create a new draft goal initialized from a prior goal/run summary

### 6. Config TUI

Add a lightweight config surface:

```bash
coordinator config
```

Initial scope:

- show configured agents and roles
- show current default Commander agent
- show repo allowlist entries
- show budget caps
- show install/runtime paths
- validate config and display actionable errors

Editing can be deferred. Read-only config inspection already removes much of the TOML confusion.

### 7. Temporary Tool Controls

Borrow Pi's temporary tool control idea, but map it to Coordinator policies:

```bash
coordinator --no-tools -p "只审查，不改文件"
coordinator --tools read,grep -p "检查风险"
coordinator --exclude-tools push,merge -p "做任务但不要推送"
```

These flags should not bypass repo policy. They may only further restrict the current run.

## Non-Goals

- Do not replace Hermes/Ink with Pi's TUI implementation.
- Do not import Pi source code or runtime dependencies.
- Do not introduce another long-running service.
- Do not add a second session database.
- Do not let CLI flags bypass repo allowlists, review gates, budget caps, or merge policies.

## Phase 5.1 Candidate Tasks

1. Add CLI argument parsing for prompt, `--print`, and `--mode`.
2. Route prompt mode through project resolution and `chat.send`.
3. Add JSON output envelopes for `project.status` and `chat.send`.
4. Add `@file` context parsing with repo boundary and size checks.
5. Add `--continue` based on latest project goal.
6. Add read-only `coordinator config`.
7. Add docs and examples.

## Acceptance Criteria

- `coordinator "hello"` works from a registered project and uses the Commander-backed chat path.
- `coordinator -p "/status"` prints status without opening the TUI.
- `coordinator --mode json -p "/status"` prints valid JSON.
- `coordinator @README.md -p "summarize"` includes bounded file context.
- `coordinator --continue` resumes the current project's latest non-terminal goal.
- `coordinator config` can diagnose missing agents, missing repos, and XDG path issues.
- Existing TUI PTY, detach, reconnect, and wheel packaging tests remain green.

## Open Questions

- Should `coordinator "prompt"` open the TUI after sending, or default to print mode?
- Should `--resume` be a TUI picker, a numbered CLI list, or both?
- Should `@file` content be stored verbatim, summarized, or only linked in Commander run metadata?
- Should JSON output stabilize around Supervisor protocol envelopes or a smaller public CLI schema?

## Recommendation

Keep the Phase 5 plan focused on real Commander-backed chat first.

After Phase 5 passes real-project smoke testing, use this backlog to create a separate Phase 5.1 implementation plan.
