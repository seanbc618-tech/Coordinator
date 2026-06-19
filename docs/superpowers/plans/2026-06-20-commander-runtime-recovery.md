# Commander Runtime Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the accepted Commander feature work with the installed Codex CLI and fail visibly and safely.

**Architecture:** Keep the existing runner, service, and CLI boundaries. Improve runner diagnostics, enforce preview success in the service, and route chat messages through a service operation that reuses the runner and admission gate.

**Tech Stack:** Python 3, SQLite, unittest, Codex CLI, TOML.

---

### Task 1: Runtime compatibility and diagnostics

**Files:** `config/agents.toml`, `src/local_cli_coordinator/commander_runner.py`, `tests/test_commander_runner.py`

- [ ] Add a test proving non-zero stderr appears in the result error and artifact.
- [ ] Run the test and observe the expected failure.
- [ ] Preserve combined stdout/stderr diagnostics and remove unsupported Codex flags.
- [ ] Run `tests.test_commander_runner` and confirm it passes.

### Task 2: Preview confirmation gate

**Files:** `src/local_cli_coordinator/commander_service.py`, `tests/test_goal_cli.py`

- [ ] Add a test proving a failed latest preview cannot activate a draft.
- [ ] Run the test and observe the draft incorrectly activates.
- [ ] Require a succeeded preview with parsed output before activation.
- [ ] Run `tests.test_goal_cli` and confirm it passes.

### Task 3: Functional chat

**Files:** `src/local_cli_coordinator/commander_service.py`, `src/local_cli_coordinator/cli.py`, `tests/test_commander_chat.py`

- [ ] Add a test proving ordinary chat text is persisted and invokes Commander.
- [ ] Run the test and observe the placeholder response.
- [ ] Add a service operation that stores the message, runs Commander, admits safe proposals, and returns a visible summary.
- [ ] Run the chat and Commander tests and confirm they pass.

### Task 4: Verification and recovery

**Files:** Runtime state only; no additional production files.

- [ ] Run the full unit test suite.
- [ ] Run `doctor` and `git diff --check`.
- [ ] Abandon the poisoned paused goal and create a fresh real preview.
- [ ] Confirm it, run one daemon cycle, and verify tasks are admitted.
- [ ] Commit the scoped fix with its tests and documentation.
