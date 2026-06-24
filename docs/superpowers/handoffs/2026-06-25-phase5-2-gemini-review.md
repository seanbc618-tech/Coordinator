# Gemini Handoff: Phase 5.2 Adversarial Review

You are a read-only adversarial reviewer. Do not implement production fixes.

Repository: `/Users/xiafan/Coordinator`
Plan: `docs/superpowers/plans/2026-06-25-phase5-2-conversation-runtime.md`
Design: `docs/superpowers/specs/2026-06-25-phase5-2-conversation-runtime-design.md`

Review every Grok commit independently and return:

```text
VERDICT: PASS | CONDITIONAL PASS | FAIL
P0:
P1:
P2:
Reproduction commands:
Blocking next task: yes | no
```

## Attack Task 1: Runtime Trust

- old server responds to ping but lacks capabilities;
- socket exists with missing/corrupt lock;
- lock exists with dead PID;
- two simultaneous restart commands;
- shutdown never removes socket;
- worker active during restart;
- old PID reused;
- second process loses lock/socket ownership;
- launcher must not open a degraded TUI.

## Attack Task 2: Conversation Contract

Test Chinese and English:

- `你好`
- `？？？`
- `如何启动？`
- `现在有什么任务？`
- `ruff 是什么？`
- `帮我看看`
- explicit small read-only task request
- repeat the same task request
- active, draft, paused, and blocked goals

Block if ordinary conversation creates any task, if explicit task requests can
never create work, or if visible text contains internal orchestration language.

## Attack Task 3: Task Outcomes

- accepted task;
- all proposals rejected;
- duplicate proposal;
- partial acceptance;
- no eligible worker;
- policy cap exceeded;
- missing task detail after admission.

The normal transcript must remain useful without hiding diagnostics from
`/task`, `/logs`, or replay.

## Attack Task 4: TUI Layout

- widths 40/80/120;
- mixed CJK/ASCII;
- explicit newlines;
- one message taller than viewport;
- resize narrower and wider;
- event replay;
- expanded activity with ten output lines;
- unknown `/taskz`;
- footer/composer must remain visible and non-overlapping.

## Final Review

Rerun focused tests, full TypeScript tests, isolated-XDG Python suite, PTY/E2E,
wheel install, and `git diff --check`. Compare the real smoke output against
the design examples. A green unit suite is not sufficient if the terminal
frame is visually corrupt or runtime identity can be bypassed.
