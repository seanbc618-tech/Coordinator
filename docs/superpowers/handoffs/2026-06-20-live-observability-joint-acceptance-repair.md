# Live Daemon Observability Joint Acceptance Repair Prompt

Status: REJECTED after joint acceptance
Branch: external/live-daemon-observability
Reviewed range: c35ec36..fbf6b0f
Fresh verification: 404/404 unittest tests passed; doctor completed; git diff check clean.

Passing tests are not sufficient because the manual smoke test and call-chain review found missing required behavior.

## P1: Daemon Does Not Forward Reporter To Commander

Evidence:

- src/local_cli_coordinator/engine.py calls maybe_replenish_goal(conn, config, root) without reporter.
- maybe_replenish_goal supports reporter and forwards it to run_commander, but receives NULL_REPORTER.
- The longest foreground stage, Codex Commander replenishment, therefore remains silent.

Required fix:

    replenishment = maybe_replenish_goal(conn, config, root, reporter=reporter)

Add an integration test that calls run_daemon_cycle with a recording reporter, forces Commander replenishment, and proves Commander started/stdout/heartbeat/completed events reach the same reporter.

Also audit initial goal and chat Commander calls. Foreground goal/chat commands must either use ConsoleReporter or explicitly document why they are outside scope.

## P1: ConsoleReporter Silently Drops Required Events

Evidence:

ConsoleReporter._render handles only:

- started
- stdout
- stderr
- heartbeat
- completed

It ignores cycle_started, task_started, timeout, interrupted, error, worktree/state events, and generic stage events. The engine emits cycle_started and task_started, but the user sees nothing.

Manual reproduction emitted cycle_started, task_started, and timeout before/after a fake command. None appeared. Only process started/output/heartbeat/completed appeared.

Required fix:

- Render cycle and task events.
- Render timeout, interrupted, and error prominently.
- Render actor/agent ID and task ID on started, heartbeat, and completion events.
- Render the literal completion status, not only an exit code.
- Provide a generic fallback for unknown structured event kinds so newly added events cannot disappear silently.
- Continue printing exact commands without redaction.
- Continue isolating terminal OSError/ValueError.

Add focused tests asserting visible text for every event kind and metadata field.

## P1: Discovery Reporter Is Not Propagated

Evidence:

- run_daemon_cycle calls run_discovery_phase without reporter.
- run_discovery_phase calls run_configured_discovery without reporter.
- run_configured_discovery calls discover_from_command without reporter.
- discover_from_command supports Reporter, but receives NULL_REPORTER.

Required fix:

Thread one Reporter through:

    run_daemon_cycle
      -> run_discovery_phase
      -> run_configured_discovery
      -> discover_from_command

Add a command-discovery daemon integration test proving the exact command, stdout, stderr, and completion events are visible from the foreground daemon reporter.

## P2: Ctrl+C Test Does Not Prove Process-Group Cleanup

Evidence:

run_command sets:

    start_new_session = os.name == "posix" and timeout_seconds is not None

The KeyboardInterrupt test starts only one child and uses no timeout. It proves the direct child dies, but not that a grandchild/process tree dies. Without a new session, killpg(process.pid) can fail and fall back to killing only the leader.

Required fix:

- Start a new process session for all POSIX child commands that Coordinator owns, not only commands with a timeout; or implement an equivalent process-group guarantee.
- Add a POSIX test where the child starts a long-lived grandchild, then trigger KeyboardInterrupt and assert both PIDs are gone.
- Preserve existing timeout behavior and Windows fallback.

## Required Acceptance Commands

After fixes:

    PYTHONPATH=src python3 -m unittest tests.test_reporting tests.test_daemon_reporter tests.test_process_streaming tests.test_command_discovery -v
    PYTHONPATH=src python3 -m unittest discover -s tests
    PYTHONPATH=src python3 -m local_cli_coordinator doctor
    git diff --check
    git status --short

## Required Manual Smoke Test

Run a local fake daemon cycle without external agents. Before child completion, the terminal must visibly show:

- cycle start;
- task start and task ID;
- actor/agent ID;
- exact unredacted command and cwd;
- stdout and stderr;
- heartbeat with actor and task ID;
- timeout/interruption/error when injected;
- explicit completion, exit code, elapsed time, and log path.

Run the same cycle with --quiet and verify only the final summary appears while durable logs retain output exactly once.

## Delivery

Commit fixes as small, reviewable commits on external/live-daemon-observability. Do not rewrite the seven accepted historical commits. Report new commit SHAs, focused/full test counts, manual smoke output, and clean git status.
