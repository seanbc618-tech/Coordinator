# Live Daemon Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Stream every Coordinator pipeline command and its output to the foreground terminal while preserving durable logs and existing execution semantics.

**Architecture:** Add a structured Reporter boundary and teach the shared process runner to drain stdout and stderr incrementally, emit heartbeats, and tee output to caller-provided durable sinks. Thread one reporter through Commander, workers, verification, reviews, Git, and daemon state transitions; retain a no-op reporter for library callers and expose daemon --quiet.

**Tech Stack:** Python 3.13 standard library (subprocess, selectors, dataclasses, time, typing), SQLite, unittest, existing Coordinator CLI abstractions.

---

## File Map

- Create src/local_cli_coordinator/reporting.py for event types and renderers.
- Modify src/local_cli_coordinator/process.py for incremental pipe draining.
- Modify agent.py, verify.py, review.py, planner.py, and commander_runner.py for stream tee integration.
- Modify discovery.py and connectors.py so command-backed discovery is visible.
- Modify gitops.py and engine.py for Git and stage visibility.
- Modify cli.py for default live mode and --quiet.
- Create tests/test_reporting.py and tests/test_process_streaming.py.
- Extend focused integration tests and README.md.

### Task 1: Structured Reporter And Console Rendering

**Files:**
- Create: src/local_cli_coordinator/reporting.py
- Create: tests/test_reporting.py

- [ ] **Step 1: Write failing rendering tests**

~~~python
from io import StringIO
from pathlib import Path

from local_cli_coordinator.reporting import ConsoleReporter, ExecutionEvent, NullReporter


def test_console_reporter_renders_exact_command() -> None:
    output = StringIO()
    reporter = ConsoleReporter(stream=output, timestamp_fn=lambda: "10:58:01")
    reporter.emit(ExecutionEvent(
        kind="started",
        stage="worker",
        command="claude --print secret=value",
        cwd=Path("/tmp/worktree"),
        task_id="task-123",
        actor="claude_worker",
    ))
    text = output.getvalue()
    assert "[10:58:01] worker" in text
    assert "task-123" in text
    assert "cwd=/tmp/worktree" in text
    assert "$ claude --print secret=value" in text


def test_console_reporter_labels_streams() -> None:
    output = StringIO()
    reporter = ConsoleReporter(stream=output, timestamp_fn=lambda: "10:58:02")
    reporter.emit(ExecutionEvent(kind="stdout", stage="verify", actor="pytest", text="one\n"))
    reporter.emit(ExecutionEvent(kind="stderr", stage="verify", actor="pytest", text="two\n"))
    assert "[pytest:stdout] one" in output.getvalue()
    assert "[pytest:stderr] two" in output.getvalue()


def test_null_reporter_accepts_events() -> None:
    NullReporter().emit(ExecutionEvent(kind="heartbeat", stage="worker", elapsed_seconds=15))
~~~

- [ ] **Step 2: Run the tests and verify red**

Run: PYTHONPATH=src python3 -m unittest tests.test_reporting -v

Expected: FAIL with ModuleNotFoundError for local_cli_coordinator.reporting.

- [ ] **Step 3: Implement the public event boundary**

Create immutable ExecutionEvent fields: kind, stage, actor, task_id, command, cwd, text, elapsed_seconds, exit_code, timed_out, and log_path. Define Reporter as a Protocol, NullReporter.emit as a no-op, and ConsoleReporter.emit as an immediate write-and-flush renderer. ConsoleReporter must print the full command without redaction and isolate OSError/ValueError so terminal failures do not alter execution.

~~~python
@dataclass(frozen=True)
class ExecutionEvent:
    kind: str
    stage: str
    actor: str = ""
    task_id: str = ""
    command: str = ""
    cwd: Path | None = None
    text: str = ""
    elapsed_seconds: float = 0.0
    exit_code: int | None = None
    timed_out: bool = False
    log_path: Path | None = None


@dataclass(frozen=True)
class ExecutionContext:
    stage: str
    actor: str = ""
    task_id: str = ""
    log_path: Path | None = None


class Reporter(Protocol):
    def emit(self, event: ExecutionEvent) -> None: ...


NULL_REPORTER: Reporter = NullReporter()
~~~

- [ ] **Step 4: Run focused tests**

Run: PYTHONPATH=src python3 -m unittest tests.test_reporting -v

Expected: all tests PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/local_cli_coordinator/reporting.py tests/test_reporting.py
git commit -m "feat: add execution reporter boundary"
~~~

### Task 2: Incremental Process Streaming And Heartbeats

**Files:**
- Modify: src/local_cli_coordinator/process.py
- Create: tests/test_process_streaming.py
- Modify: tests/test_command_timeouts.py

- [ ] **Step 1: Write failing streaming tests**

Create a RecordingReporter and run a child that prints "first", sleeps, then prints "second". Start run_command in a thread and assert the first event arrives before the thread exits. Add separate stderr, partial-line flush, silent heartbeat, timeout, and KeyboardInterrupt cleanup cases.

Add a sink-failure case whose stdout sink raises OSError. Assert the child process is reaped, an error event is emitted, and OSError reaches the caller so the owning stage cannot report success without a durable log.

~~~python
result = run_command(
    [sys.executable, "-c",
     "import time; print('first', flush=True); time.sleep(.2); print('second', flush=True)"],
    cwd=Path(tmp),
    reporter=reporter,
    context=ExecutionContext(stage="worker", actor="fake", task_id="task-1"),
    heartbeat_seconds=0.05,
)
assert result.stdout == "first\nsecond\n"
assert [e.text for e in reporter.events if e.kind == "stdout"] == ["first\n", "second\n"]
assert any(e.kind == "heartbeat" for e in reporter.events)
~~~

- [ ] **Step 2: Run tests and verify signature failures**

Run: PYTHONPATH=src python3 -m unittest tests.test_process_streaming tests.test_command_timeouts -v

Expected: FAIL because ExecutionContext and streaming parameters do not exist.

- [ ] **Step 3: Implement selector-based pipe draining**

Add keyword-only reporter, context, stdout_sink, stderr_sink, heartbeat_seconds, and monotonic_fn parameters while preserving current defaults and ProcessResult.

~~~python
def run_command(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
    reporter: Reporter = NULL_REPORTER,
    context: ExecutionContext | None = None,
    stdout_sink: Callable[[str], None] | None = None,
    stderr_sink: Callable[[str], None] | None = None,
    heartbeat_seconds: float = 15.0,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> ProcessResult:
~~~

Register both pipes with selectors.DefaultSelector. Read bytes using os.read, retain complete buffers for ProcessResult, emit complete lines immediately, and flush partial lines at EOF. Poll using the smaller of remaining timeout and remaining heartbeat. Normal output resets the heartbeat silence timer.

- [ ] **Step 4: Preserve timeout and interruption behavior**

On timeout, emit timeout, kill the existing process group, drain output, and return 124. On KeyboardInterrupt, emit interrupted, kill and reap the child group, close pipes, then re-raise. Do not swallow other BaseException values.

- [ ] **Step 5: Run focused tests**

Run: PYTHONPATH=src python3 -m unittest tests.test_process_streaming tests.test_command_timeouts -v

Expected: all tests PASS and no child process survives.

- [ ] **Step 6: Commit**

~~~bash
git add src/local_cli_coordinator/process.py tests/test_process_streaming.py tests/test_command_timeouts.py
git commit -m "feat: stream child process output"
~~~

### Task 3: Worker, Verification, Review, And Planner Tee

**Files:**
- Modify: src/local_cli_coordinator/agent.py
- Modify: src/local_cli_coordinator/verify.py
- Modify: src/local_cli_coordinator/review.py
- Modify: src/local_cli_coordinator/planner.py
- Modify: src/local_cli_coordinator/discovery.py
- Modify: src/local_cli_coordinator/connectors.py
- Modify: tests/test_agent.py
- Modify: tests/test_verify.py
- Modify: tests/test_spec_review.py
- Modify: tests/test_quality_review.py
- Modify: tests/test_llm_planner_hook.py
- Modify: tests/test_command_discovery.py
- Modify: tests/test_connectors.py

- [ ] **Step 1: Add failing propagation and tee tests**

Patch run_command at each boundary and assert it receives the same Reporter plus the correct stage. Run a real delayed fake agent and assert stdout/stderr appear in agent.log exactly once. Run two verification commands and assert headers and outputs appear once in verifier.log.

Add command-discovery and connector assertions for exact command, cwd, stage discovery, stdout, stderr, and exit code. Preserve shell command semantics by rendering the configured string through the platform shell explicitly rather than silently changing tokenization.

- [ ] **Step 2: Verify red**

Run: PYTHONPATH=src python3 -m unittest tests.test_agent tests.test_verify tests.test_spec_review tests.test_quality_review tests.test_llm_planner_hook tests.test_command_discovery tests.test_connectors -v

Expected: FAIL on missing reporter parameters.

- [ ] **Step 3: Add optional Reporter parameters**

Use these signatures and forward their contexts to run_command:

~~~python
def run_agent(
    ...,
    timeout_seconds: float | None = None,
    *,
    reporter: Reporter = NULL_REPORTER,
    stage: str = "worker",
    task_id: str = "",
) -> AgentRunResult:

def run_verification(
    ...,
    timeout_seconds: float | None = None,
    *,
    reporter: Reporter = NULL_REPORTER,
    task_id: str = "",
) -> VerificationResult:
~~~

Open durable logs before child start, write command and timeout metadata, then pass callbacks that append and flush labelled stdout/stderr records. Remove post-process output rewrites to prevent duplication. Use stages worker, verify, spec_review, quality_review, and planner.

Replace subprocess.run in command discovery and connectors with run_command using stage discovery and the shared Reporter. Continue passing the configured command to the shell as [os.environ.get("SHELL", "/bin/sh"), "-lc", command] so pipes, quoting, and environment expansion remain compatible.

- [ ] **Step 4: Run focused tests**

Run: PYTHONPATH=src python3 -m unittest tests.test_agent tests.test_verify tests.test_spec_review tests.test_quality_review tests.test_llm_planner_hook tests.test_command_discovery tests.test_connectors -v

Expected: all tests PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/local_cli_coordinator/agent.py src/local_cli_coordinator/verify.py src/local_cli_coordinator/review.py src/local_cli_coordinator/planner.py src/local_cli_coordinator/discovery.py src/local_cli_coordinator/connectors.py tests/test_agent.py tests/test_verify.py tests/test_spec_review.py tests/test_quality_review.py tests/test_llm_planner_hook.py tests/test_command_discovery.py tests/test_connectors.py
git commit -m "feat: stream worker and evaluator stages"
~~~

### Task 4: Commander Streaming And Interruption Recovery

**Files:**
- Modify: src/local_cli_coordinator/commander_runner.py
- Modify: src/local_cli_coordinator/commander_service.py
- Modify: tests/test_commander_runner.py
- Modify: tests/test_commander_failures.py

- [ ] **Step 1: Write failing Commander tests**

Assert run_commander accepts Reporter and emits stage commander, actor ID, exact rendered Codex command, repo cwd, and stdout. Raise KeyboardInterrupt from run_command and assert commander_runs records status interrupted, completed_at, and error "interrupted by operator".

- [ ] **Step 2: Verify red**

Run: PYTHONPATH=src python3 -m unittest tests.test_commander_runner tests.test_commander_failures -v

Expected: FAIL because Reporter is unsupported and interrupted runs remain running.

- [ ] **Step 3: Preserve raw JSON while streaming**

Thread Reporter through preview, chat, replenishment, and run_commander. Create the final run directory before execution. Append stdout only to raw.txt and stderr to stderr.log; parse raw.txt after successful exit. Display labels must never be written into raw JSON.

- [ ] **Step 4: Finalize interruption**

~~~python
try:
    result = run_command(...)
except KeyboardInterrupt:
    finish_commander_run(
        conn,
        run_id,
        status="interrupted",
        error="interrupted by operator",
        duration_seconds=time.monotonic() - started_at,
    )
    raise
~~~

Retain restart-time orphan cleanup as a second defense.

- [ ] **Step 5: Run Commander tests**

Run: PYTHONPATH=src python3 -m unittest tests.test_commander_runner tests.test_commander_failures tests.test_commander_replenishment -v

Expected: all tests PASS.

- [ ] **Step 6: Commit**

~~~bash
git add src/local_cli_coordinator/commander_runner.py src/local_cli_coordinator/commander_service.py tests/test_commander_runner.py tests/test_commander_failures.py
git commit -m "feat: stream Commander execution"
~~~

### Task 5: Daemon Events And Live/Quiet Modes

**Files:**
- Modify: src/local_cli_coordinator/engine.py
- Modify: src/local_cli_coordinator/cli.py
- Modify: tests/test_daemon_loop.py
- Modify: tests/test_cli_commands.py
- Modify: tests/test_engine.py

- [ ] **Step 1: Write failing CLI and propagation tests**

Assert daemon accepts --quiet. Assert _cmd_daemon creates ConsoleReporter by default and NullReporter when quiet. Patch Commander, worker, verifier, and reviewers and assert one Reporter reaches every boundary. Assert task start, worktree, state, and completion events carry the task ID.

- [ ] **Step 2: Verify red**

Run: PYTHONPATH=src python3 -m unittest tests.test_daemon_loop tests.test_cli_commands tests.test_engine -v

Expected: FAIL with argparse rejecting --quiet and missing Reporter propagation.

- [ ] **Step 3: Thread Reporter through engine**

Add reporter: Reporter = NULL_REPORTER to run_daemon_cycle, run_continuous_daemon, run_one_ready_task, and _process_task. Emit cycle/task/worktree/state events alongside existing transitions. Do not create a second display state machine.

- [ ] **Step 4: Select CLI mode**

~~~python
daemon.add_argument("--quiet", action="store_true")
reporter: Reporter = NullReporter() if args.quiet else ConsoleReporter()
~~~

Pass Reporter to once and continuous execution. Both modes retain the final summary.

- [ ] **Step 5: Run focused tests**

Run: PYTHONPATH=src python3 -m unittest tests.test_daemon_loop tests.test_cli_commands tests.test_engine -v

Expected: all tests PASS.

- [ ] **Step 6: Commit**

~~~bash
git add src/local_cli_coordinator/engine.py src/local_cli_coordinator/cli.py tests/test_daemon_loop.py tests/test_cli_commands.py tests/test_engine.py
git commit -m "feat: show live daemon stages"
~~~

### Task 6: Git Command Visibility

**Files:**
- Modify: src/local_cli_coordinator/gitops.py
- Modify: src/local_cli_coordinator/engine.py
- Modify: tests/test_gitops.py
- Modify: tests/test_push_merge.py

- [ ] **Step 1: Write failing Git Reporter tests**

For create_worktree, commit_all, push_branch, and merge_branch_to_default, pass a RecordingReporter and assert every exact git command, cwd, stdout/stderr, and exit code is emitted. Include a failed command and assert its worktree remains.

- [ ] **Step 2: Verify red**

Run: PYTHONPATH=src python3 -m unittest tests.test_gitops tests.test_push_merge -v

Expected: FAIL because Git helpers reject Reporter.

- [ ] **Step 3: Route Git through run_command**

Add keyword-only reporter, task_id, and actor="git" to git(). Call run_command(["git", *args], cwd=...) and adapt ProcessResult to the existing CompletedProcess text shape so require_success remains unchanged. Forward Reporter from engine Git calls. Read-only maintenance callers retain the null default.

- [ ] **Step 4: Run Git regressions**

Run: PYTHONPATH=src python3 -m unittest tests.test_gitops tests.test_push_merge tests.test_engine -v

Expected: all tests PASS and failed operations preserve worktrees.

- [ ] **Step 5: Commit**

~~~bash
git add src/local_cli_coordinator/gitops.py src/local_cli_coordinator/engine.py tests/test_gitops.py tests/test_push_merge.py
git commit -m "feat: report Git pipeline commands"
~~~

### Task 7: End-To-End Cleanup, Documentation, And Verification

**Files:**
- Modify: tests/test_loop_e2e.py
- Modify: tests/test_command_timeouts.py
- Modify: README.md

- [ ] **Step 1: Add delayed-output end-to-end coverage**

Configure a fake worker that emits delayed stdout/stderr. Run one cycle with ConsoleReporter(StringIO); assert output is observed before completion, final task state is unchanged, and the durable log contains each line exactly once.

- [ ] **Step 2: Add Ctrl+C cleanup coverage**

Run a long child under _cmd_daemon, deliver SIGINT, and assert: child PID gone, lock absent, active leases zero, daemon ledger ended_at populated, and active Commander run interrupted. Mark process-group coverage POSIX-only.

- [ ] **Step 3: Run end-to-end tests**

Run: PYTHONPATH=src python3 -m unittest tests.test_loop_e2e tests.test_command_timeouts -v

Expected: all tests PASS.

- [ ] **Step 4: Document operation**

Add these README commands and state that commands are unredacted and display does not change rollback behavior:

~~~bash
# Live commands, output, heartbeats, and transitions
PYTHONPATH=src python3 -m local_cli_coordinator daemon

# Durable logs with final summary only
PYTHONPATH=src python3 -m local_cli_coordinator daemon --quiet
~~~

- [ ] **Step 5: Run the complete gate**

~~~bash
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m local_cli_coordinator doctor
git diff --check
~~~

Expected: all tests PASS, doctor has no configuration error, and diff check is silent.

- [ ] **Step 6: Perform manual smoke tests**

Run a temporary fake agent via daemon --once. Verify command, cwd, stdout, stderr, heartbeat, completion, and log path appear before the summary. Repeat with --quiet and verify only the summary appears while logs remain complete.

- [ ] **Step 7: Commit**

~~~bash
git add tests/test_loop_e2e.py tests/test_command_timeouts.py README.md
git commit -m "docs: verify live daemon observability"
~~~

## Delivery Gate

Review all seven commits and verify:

- commands are not redacted;
- no PTY or interactive stdin behavior was introduced;
- output appears before child completion;
- durable logs contain each output exactly once;
- --quiet changes display only;
- timeout, Ctrl+C, lease, lock, and Commander interruption behavior is correct;
- worktree, commit, push, merge, and rollback semantics are unchanged.
