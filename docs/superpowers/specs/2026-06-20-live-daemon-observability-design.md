# Live Daemon Observability Design

## Objective

Make Coordinator's foreground CLI show the complete execution pipeline in real
time. Users must be able to see which agent or tool is running, the exact
command, live stdout and stderr, elapsed time, completion status, and the log
path without opening a second terminal.

This feature changes observability only. It must not change task admission,
agent selection, worktree isolation, verification, review, Git behavior,
timeouts, leases, or rollback semantics.

## User Experience

`coordinator daemon` and `coordinator daemon --once` enable live output by
default. `--quiet` restores the existing summary-only behavior.

Every pipeline operation reports:

- timestamp and stage;
- task ID and agent ID when applicable;
- current working directory;
- the complete command exactly as executed, without redaction;
- live stdout and stderr with source labels;
- a heartbeat every 15 seconds while no output is received;
- exit code, elapsed time, timeout state, and durable log path.

The complete pipeline is visible: discovery, Commander, worker, verification,
spec review, quality review, and Git commit, push, or merge operations.

Example:

```text
[10:58:01] cycle      started
[10:58:01] commander  cwd=/repo
[10:58:01] commander  $ codex exec --model gpt-5.5 ...
[10:58:16] commander  running - 15s
[codex:stdout] Inspecting roadmap and completed tasks...
[10:59:03] commander  completed - exit=0 - 62s - log=runs/...

[10:59:03] task       task-123 - Add probability engine
[10:59:03] worker     $ claude --print ...
[claude:stdout] Reading existing implementation...
[10:59:18] worker     running - 15s
[11:01:42] worker     completed - exit=0 - 159s - log=runs/...

[11:01:42] verify     $ uv run pytest -q
[pytest:stdout] 72 passed
[11:03:01] git        $ git commit ...
[11:03:02] task       done
```

The user has explicitly chosen complete, unredacted command display. Future
commands containing credentials will therefore be visible in terminal output
and terminal recordings.

## Architecture

### Process Streaming

Replace the blocking `Popen.communicate()` success path with a pipe-draining
loop that reads stdout and stderr incrementally. The process runner tees each
chunk to three destinations:

1. an in-memory buffer used to preserve the existing `ProcessResult` contract;
2. the durable stage log;
3. an optional Reporter used by the foreground CLI.

The runner continues to use separate stdout and stderr pipes. Each displayed
line includes its source so interleaved output remains understandable. Partial
lines are buffered until a newline or process completion. Decoding continues
to use replacement semantics for invalid bytes.

The implementation must avoid deadlocks when one pipe is busy and the other is
idle. Use selectors on POSIX and a small, isolated fallback suitable for the
project's supported Python environments. Streaming must remain non-interactive;
this design does not introduce a PTY or change how child CLIs detect stdin.

### Reporter Boundary

Introduce a small Reporter interface rather than printing from engine and
process modules directly. It accepts structured events for:

- cycle and stage start;
- exact command and cwd;
- stdout or stderr output;
- heartbeat;
- stage completion or failure;
- task state changes and Git actions.

The CLI creates either a console reporter or a no-op reporter for `--quiet` and
passes it through daemon, Commander, worker, verifier, reviewer, and Git
boundaries. Existing library callers and tests default to the no-op reporter.

Stage metadata includes task ID, agent ID, command category, log path, and
elapsed time. The console renderer owns formatting; execution modules only
emit structured events.

### Durable Logs

Streaming appends output to the same task and Commander log artifacts already
used by Coordinator. A command must not be logged twice. Existing result
objects still expose complete stdout and stderr after the process exits so
parsers and current callers continue to work.

The live terminal is a view of execution, not the source of record. Closing a
terminal does not erase output already persisted to disk.

### Git Visibility

Git operations emit the same start, command, output, and completion events as
agents and verification commands. Their behavior remains unchanged: commands
still run in isolated worktrees, failed operations preserve the worktree, and
no cleanup or rollback is triggered merely by display failures.

## Heartbeats

A running command emits a heartbeat every 15 seconds when no output has been
displayed during that interval. A heartbeat contains the stage, agent or tool,
task ID when available, and total elapsed time.

Normal output resets the silence timer. Heartbeats are terminal events only
and are not copied into raw agent output or parsed Commander responses. They
may be recorded in the Coordinator event stream for diagnostics.

## Failure And Interruption Handling

- Non-zero exits show the final buffered output, exit code, elapsed time, and
  log path before existing failure handling runs.
- Timeouts emit a timeout event before terminating the existing process group.
- `Ctrl+C` emits an interruption event, terminates the active child process
  group, releases task leases and the daemon lock, and marks an active
  Commander run as `interrupted`.
- A restart continues to detect and supersede orphaned Commander runs.
- If terminal rendering fails, command execution and durable logging continue.
- If durable logging fails, the failure is visible and recorded; it must not be
  silently converted into task success.
- Reporter failures never alter a child process return code.

## CLI Contract

```text
coordinator daemon [--once] [--force-lock] [--quiet]
```

Live output is the default. `--quiet` suppresses command, stream, heartbeat,
and stage events while preserving the final daemon summary and all durable
logs.

No separate TUI is included in this scope. The structured Reporter boundary
allows a future TUI or JSONL reporter without changing process execution.

## Testing

Focused tests must cover:

- incremental stdout and stderr forwarding;
- partial-line handling and final buffer flushing;
- exact command and cwd events;
- simultaneous terminal and durable-log output without duplication;
- 15-second heartbeat behavior with an injectable clock;
- default live mode and `--quiet` mode;
- non-zero exits and timeout reporting;
- `Ctrl+C` child cleanup, lease release, lock release, and Commander
  interruption state;
- reporter or terminal failure isolation;
- unchanged `ProcessResult` stdout, stderr, return code, and timeout semantics;
- all pipeline stages, including Git operations, using the Reporter contract.

The complete existing test suite and `git diff --check` must pass. Integration
verification must run a short fake agent that emits delayed stdout and stderr,
proving that output appears before process completion and is also present in
the durable log.

## Acceptance Criteria

1. A foreground daemon visibly reports every pipeline stage and exact command.
2. Agent and tool output appears while the child process is still running.
3. Silent commands produce a heartbeat at least every 15 seconds.
4. The same output remains available in durable Coordinator logs.
5. `--quiet` preserves the old concise experience.
6. Interruptions do not leave active leases, locks, child processes, or
   Commander runs marked `running`.
7. Existing task, Git, timeout, review, and rollback behavior is unchanged.
