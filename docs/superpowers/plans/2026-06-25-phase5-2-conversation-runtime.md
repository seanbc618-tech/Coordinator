# Phase 5.2 Conversation and Runtime Trust Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the installed Coordinator trustworthy and conversational: it must detect stale Supervisors, answer ordinary chat naturally, admit tasks only for explicit task requests, and render a stable terminal viewport.

**Architecture:** Add a runtime capability handshake and explicit restart lifecycle at the Python Supervisor boundary. Upgrade the Commander response contract so visible replies are distinct from internal progress and admission diagnostics. Keep the existing Ink TUI, but make transcript selection line-aware and keep slash commands local/deterministic.

**Tech Stack:** Python 3.13, SQLite, Unix sockets, existing Supervisor/Commander engine, TypeScript, React Ink, Vitest, Python `unittest`, PTY/E2E tests.

---

## Ownership

- **Grok:** all production implementation and integration commits.
- **Gemini:** read-only adversarial review after every Grok task.
- **Claude Code:** failing tests, deterministic fixtures, docs, and command-output collection only.
- **Codex:** Gate A runtime identity, Gate B schema/admission, Gate C viewport,
  Gate D real smoke, and final acceptance.

Claude must not modify Supervisor lifecycle, Commander schema/prompt policy,
admission logic, or TUI state architecture.

## Task 0: Freeze Reproductions

**Owner:** Claude Code

**Files:**
- Modify: `tests/test_supervisor_commander.py`
- Modify: `tests/test_commander_chat.py`
- Modify: `ui-tui/src/__tests__/layout.test.tsx`
- Modify: `ui-tui/src/__tests__/submitDecision.test.ts`

- [ ] Add chat fixtures for `你好`, `？？？`, `如何启动？`, and an explicit
  read-only task request. Assert the first three create zero tasks.
- [ ] Add a test proving visible Commander text does not contain
  `duplicate title`, `linked task`, `admission`, or `no duplicate`.
- [ ] Add 40/80/120-column layout fixtures with long mixed Chinese/English
  help, task, and Commander messages. Assert footer markers occur once and no
  rendered frame exceeds terminal height.
- [ ] Add an unknown slash test: `/taskz` returns local error/help and sends no
  `chat.send`.
- [ ] Run only the new tests and record the expected failures.
- [ ] Commit as `test: capture Phase 5.2 runtime and conversation regressions`.

## Task 1: Runtime Identity and Restart

**Owner:** Grok

**Files:**
- Modify: `src/local_cli_coordinator/supervisor_process.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Modify: `src/local_cli_coordinator/supervisor.py`
- Modify: `src/local_cli_coordinator/supervisor_server.py`
- Modify: `src/local_cli_coordinator/tui_launcher.py`
- Modify: `src/local_cli_coordinator/supervisor_protocol.py`
- Test: `tests/test_supervisor_process.py`
- Test: `tests/test_supervisor_cli.py`

- [ ] Define one compatibility identifier and one sorted capability set in a
  small module such as `supervisor_identity.py`. Include at least
  `project.goal`, `project.tasks`, `project.task`, `chat.commander.v2`, and
  `transcript.line-budget.v1`.
- [ ] First add failing tests asserting `system.ping` exposes `pid`,
  `runtime_compatibility`, `capabilities`, `started_at`, and `active_workers`.
- [ ] Add a failing CLI test for `coordinator supervisor restart`: graceful
  shutdown, wait for socket/lock removal, one new serving PID.
- [ ] Capture PID and start time once when the foreground Supervisor starts.
- [ ] Enrich `system.ping` with identity and active worker count.
- [ ] Change `ping_supervisor` to return structured identity rather than a
  boolean; retain a small boolean compatibility wrapper if old callers need it.
- [ ] Make TUI launch fail clearly when required capabilities are missing:

```text
Supervisor is incompatible with this Coordinator install.
Run: coordinator supervisor restart
```

- [ ] Implement `coordinator supervisor restart`:
  send `system.shutdown`, wait for socket and lock removal, fail on timeout,
  then call `ensure_supervisor`, ping it, and verify the PID changed.
- [ ] Add an ownership watchdog. A current Supervisor that no longer owns its
  lock/socket requests shutdown instead of continuing as an orphan scheduler.
- [ ] Never use `pkill` or process-name matching in production code.
- [ ] Run:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_supervisor_process tests.test_supervisor_cli tests.test_supervisor_server -v
```

- [ ] Commit as `feat: add Supervisor runtime identity and safe restart`.

**Gate A:** Gemini attacks stale lock, missing socket, shutdown timeout,
concurrent restart, active worker reporting, and old-server compatibility.

## Task 2: Commander Response Schema v2

**Owner:** Grok

**Files:**
- Modify: `src/local_cli_coordinator/commander_protocol.py`
- Modify: `src/local_cli_coordinator/commander_runner.py`
- Modify: `src/local_cli_coordinator/commander_service.py`
- Modify: `src/local_cli_coordinator/goals.py`
- Modify: `src/local_cli_coordinator/supervisor_commander.py`
- Test: `tests/test_commander_protocol.py`
- Test: `tests/test_commander_runner.py`
- Test: `tests/test_commander_chat.py`
- Test: `tests/test_supervisor_commander.py`

- [ ] Upgrade the single authoritative Commander schema to version 2. Remove
  duplicated schema/parser definitions or make one module delegate to the
  other.
- [ ] Add required fields:

```python
intent: Literal["conversation", "status_question", "task_request"]
user_reply: str
progress_summary: str
```

- [ ] Enforce semantic validation: non-task intents require `tasks == []`.
- [ ] Update the Commander prompt contract:
  answer the latest user message directly in `user_reply`; keep orchestration
  memory in `progress_summary`; create tasks only when the user explicitly asks
  for work or a concrete action.
- [ ] Persist `user_reply` as the assistant message. Persist
  `progress_summary` only in goal/run state.
- [ ] Replace `_format_commander_reply` so it never prepends `Commander:` or
  appends raw rejection reasons.
- [ ] Publish admission diagnostics in structured `commander.completed`
  payloads, not normal `chat.message` text.
- [ ] Preserve initial-plan and replenishment behavior with deterministic
  trigger-specific prompt instructions.
- [ ] Run:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_commander_protocol tests.test_commander_runner \
  tests.test_commander_chat tests.test_supervisor_commander -v
```

- [ ] Commit as `feat: separate Commander user replies from orchestration state`.

**Gate B:** Gemini supplies greetings, punctuation, status questions, ambiguous
requests, duplicate task requests, and explicit task requests. Any ordinary
chat that creates a task is a blocker.

## Task 3: Human Task Outcome Messages

**Owner:** Grok

**Files:**
- Modify: `src/local_cli_coordinator/commander_service.py`
- Modify: `src/local_cli_coordinator/supervisor_commander.py`
- Modify: `ui-tui/src/eventReducer.ts`
- Modify: `ui-tui/src/components/ActivityBlock.tsx`
- Test: `tests/test_supervisor_commander.py`
- Test: `ui-tui/src/__tests__/eventReducer.test.ts`

- [ ] When low-risk tasks are admitted, publish a structured task summary with
  task ID, title, state, goal, verification commands, and assigned/eligible
  agent when known.
- [ ] Keep policy rejection reasons in an expandable diagnostic activity.
- [ ] For duplicate proposals, visible text should say:

```text
没有创建重复任务；已有同类任务 <task-id> 正在 <state>。
```

  It must not expose database/policy vocabulary.
- [ ] Add one visible completion sentence after `user_reply`, not an internal
  pipe-separated summary.
- [ ] Run focused Python and Vitest tests.
- [ ] Commit as `feat: present Commander task outcomes in operator language`.

## Task 4: Line-Aware Transcript and Unknown Slash Handling

**Owner:** Grok

**Files:**
- Create: `ui-tui/src/textLayout.ts`
- Modify: `ui-tui/src/components/Message.tsx`
- Modify: `ui-tui/src/components/ActivityBlock.tsx`
- Modify: `ui-tui/src/components/Transcript.tsx`
- Modify: `ui-tui/src/submitDecision.ts`
- Modify: `ui-tui/src/slash.ts`
- Test: `ui-tui/src/__tests__/layout.test.tsx`
- Test: `ui-tui/src/__tests__/submitDecision.test.ts`
- Test: `tests/test_tui_pty.py`

- [ ] Move wrapping into a shared pure utility that treats explicit newlines
  and wide Unicode consistently.
- [ ] Give message and activity blocks pure line estimators using the same
  wrapping utility as rendering.
- [ ] Select the newest transcript suffix whose estimated line total fits the
  viewport. If one item exceeds the viewport, keep only its newest visible
  lines.
- [ ] Set the transcript box to an explicit height with hidden overflow.
- [ ] Parse any input beginning with `/` as a command. Unknown commands produce
  local `Unknown command: /x. Use /help.` and never call `chat.send`.
- [ ] Add PTY assertions at 40, 80, and 120 columns. Footer and composer remain
  visible and each logical line appears once.
- [ ] Build the bundle and commit generated bundle changes with source.
- [ ] Commit as `fix: stabilize TUI transcript layout and slash routing`.

**Gate C:** Gemini reviews CJK width, embedded newlines, one oversized item,
resize, replay, expanded activity, and footer/composer overlap.

## Task 5: Deterministic Fixtures and Documentation

**Owner:** Claude Code

**Files:**
- Modify: `tests/fixtures/fake_supervisor.py`
- Modify: `tests/test_tui_pty.py`
- Modify: `docs/tui.md`
- Modify: `docs/troubleshooting.md`
- Create: `docs/superpowers/handoffs/2026-06-25-phase5-2-acceptance.md`

- [ ] Update fake Supervisor responses/events to schema v2.
- [ ] Add simple PTY fixtures for natural reply, task outcome, unknown slash,
  and incompatible runtime message. Do not change production code.
- [ ] Document `coordinator supervisor restart`.
- [ ] Document the distinction between chat, slash commands, and explicit task
  requests.
- [ ] Record exact focused gate output.
- [ ] Commit as `test: cover Phase 5.2 TUI conversation flows`, then
  `docs: document trusted runtime and conversational Commander`.

## Task 6: Integration and Real Smoke

**Owner:** Grok

- [ ] Rebuild `src/local_cli_coordinator/tui_bundle/`.
- [ ] Run all TypeScript gates:

```bash
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
```

- [ ] Run isolated Python gates:

```bash
XDG_CONFIG_HOME=/private/tmp/coordinator-phase52/config \
XDG_DATA_HOME=/private/tmp/coordinator-phase52/data \
XDG_STATE_HOME=/private/tmp/coordinator-phase52/state \
PYTHONWARNINGS=error::ResourceWarning \
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

- [ ] Run PTY/E2E and wheel tests.
- [ ] Run `git diff --check`.
- [ ] Perform a clean-install smoke in
  `/Users/xiafan/polymarket-crypto-threshold` after one explicit legacy-process
  cleanup. Verify one serving PID and these inputs:

```text
/help
你好
？？？
如何启动？
创建一个只读任务，运行 uv run ruff check src/ tests/ 并报告结果。
/tasks
/task <new-id>
/quit
```

- [ ] Commit the acceptance report.

## Final Gates

- **Gate D, Gemini:** complete adversarial review; PASS/FAIL with reproductions.
- **Gate E, Codex:** independently rerun focused gates, full suites, clean wheel
  install, process identity check, and the real TUI smoke.

No merge recommendation is allowed before Gate E.
