# Phase 5.2 Conversation and Runtime Trust Design

Date: 2026-06-25
Branch: `external/coordinator-global-tui`
Baseline: `397ddbf`

## Problem

Phase 5.1 made tasks inspectable, but the real TUI smoke still exposes three
product-level failures:

1. Several old Supervisor processes can remain alive after reinstall/restart.
   A new TUI may connect to an older server and silently lose newer behavior.
2. Commander exposes `progress_summary` as the user-facing answer. Greetings and
   questions therefore receive internal orchestration commentary such as
   "no duplicate task should be admitted".
3. The transcript viewport budgets by item count, not rendered terminal lines.
   Long Chinese/English messages and task details can overwrite the footer or
   visually interleave.

The current task failure is also confirmed to be runtime contamination rather
than a Phase 5.1 engine failure: the worker log reports a successful read-only
ruff check, while an older Supervisor marked it `failed: no changed files`.

## Decision

Implement Phase 5.2 as three bounded layers.

### 1. Runtime Trust

`system.ping` must return a runtime identity:

- server PID;
- protocol version;
- runtime compatibility ID;
- supported method/capability names;
- start time;
- active worker count.

The launcher must reject a reachable but incompatible Supervisor instead of
opening a partly functional TUI. Add `coordinator supervisor restart` as the
single supported repair command. New Supervisor processes must periodically
verify that they still own the lock and socket; loss of ownership triggers a
clean shutdown.

Automatic killing of arbitrary matching processes is out of scope. The first
upgrade may require one explicit cleanup of legacy processes.

### 2. Conversation Contract

Commander schema version 2 separates:

- `user_reply`: natural language shown to the operator;
- `progress_summary`: internal durable goal memory;
- `intent`: `conversation`, `status_question`, or `task_request`;
- `tasks`: proposed worker tasks.

Semantic rules:

- `conversation` and `status_question` must return no tasks;
- only `task_request` may admit tasks;
- greetings and clarification questions answer normally;
- internal duplicate-title, policy, and admission details are not concatenated
  into the visible reply;
- admitted tasks are summarized separately with ID, title, state, and command;
- rejected proposals remain available through events/logs and expandable
  diagnostics.

Slash commands remain deterministic and never enter Commander.

### 3. Stable TUI Viewport

The transcript must select visible content using estimated rendered line count,
using the same wrapping rules as message/activity rendering. The transcript box
must have a fixed height and hidden overflow. Unknown slash commands must return
local help instead of becoming chat.

The TUI keeps the Hermes/Ink shell. This phase does not replace it with Pi.

## Interaction Examples

Input:

```text
你好
```

Expected:

```text
你好。当前 Coordinator 正在验收 polymarket-crypto-threshold。
已有 1 个任务运行中，需要我解释状态，还是创建一个新的小任务？
```

No task is admitted.

Input:

```text
如何启动？
```

Expected:

```text
在项目目录运行 coordinator。Supervisor 会自动连接；若版本不兼容，
运行 coordinator supervisor restart。
```

No task is admitted.

Input:

```text
创建一个只读任务，运行 uv run ruff check src/ tests/ 并报告结果。
```

Expected:

```text
已创建 1 个只读任务。
task-... [ready] Run ruff baseline check
Verify: uv run ruff check src/ tests/
```

## Safety

- Existing repo allowlists, task admission, capabilities, budgets, worktrees,
  verification, review, commit, and push policy remain authoritative.
- Conversation intent cannot bypass admission policy.
- `supervisor restart` sends graceful shutdown first and refuses to report
  success until the old socket and lock disappear.
- A restart timeout is an explicit error; it must not spawn a second server.
- Conversation history stores `user_reply` and internal progress separately.

## Acceptance

1. A stale/incompatible Supervisor produces an actionable compatibility error.
2. `supervisor restart` leaves exactly one reachable serving PID.
3. `你好` and `？？？` produce natural replies and create zero tasks.
4. An explicit small-task request creates at most the policy-allowed tasks and
   reports exact task details.
5. Internal duplicate-title text is absent from the normal transcript.
6. At 40, 80, and 120 columns, long mixed Chinese/English transcript content
   does not overlap the footer or composer.
7. Existing Phase 5.1 engine, PTY, E2E, wheel, and ResourceWarning gates remain
   green.

## Deferred

- Streaming Commander tokens.
- Multi-project switcher.
- Pi-inspired `--print`, JSON mode, `@file`, resume/fork, and config TUI.
- Automatic process-table cleanup of pre-Phase-5 legacy Supervisors.
