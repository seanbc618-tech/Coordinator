import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from local_cli_coordinator.agent import AgentRunResult, run_agent
from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.engine import run_one_ready_task
from local_cli_coordinator.process import _reap_leader, run_command
from local_cli_coordinator.review import ReviewResult, run_quality_review, run_spec_review
from local_cli_coordinator.verify import CommandResult, VerificationResult, run_verification


TIMEOUT_EXIT_CODE = 124


def _agent(command: str, *, role: str = "worker") -> AgentConfig:
    return AgentConfig(
        id=role,
        command=command,
        capabilities=["code"],
        max_concurrency=1,
        role=role,
    )


def _python_command(script: Path) -> str:
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"


def _write_process_tree_script(root: Path, *, detached: bool) -> tuple[Path, Path]:
    marker = root / "child-ready.txt"
    script = root / "process-tree.py"
    child_code = (
        "import os, time; from pathlib import Path; "
        f"Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)"
    )
    script.write_text(
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        f"marker = Path({str(marker)!r})\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}], "
        f"start_new_session={detached!r})\n"
        "deadline = time.monotonic() + 5\n"
        "while not marker.exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "print('partial output', flush=True)\n"
        "time.sleep(30)\n"
    )
    return script, marker


def _process_exited(pid: int, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def _kill_if_running(pid: int | None) -> None:
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    _process_exited(pid)


def _policy(timeout_seconds: int) -> PolicyConfig:
    return PolicyConfig(
        require_single_repo=True,
        require_acceptance_criteria=True,
        require_verification_commands=True,
        require_handoff_summary=True,
        max_files_touched=3,
        max_expected_minutes=30,
        max_attempts=3,
        split_if_touches_multiple_subsystems=True,
        split_if_research_and_code_are_mixed=True,
        max_task_runtime_seconds=timeout_seconds,
    )


class CommandTimeoutTests(unittest.TestCase):
    def test_agent_timeout_is_failure_and_records_timeout_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run"
            worktree.mkdir()
            prompt = root / "prompt.md"
            prompt.write_text("work")
            program = "import time; print('started', flush=True); time.sleep(30)"
            command = f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"

            result = run_agent(
                _agent(command),
                prompt,
                worktree,
                run_dir,
                timeout_seconds=0.2,
            )

            self.assertEqual(result.exit_code, TIMEOUT_EXIT_CODE)
            self.assertTrue(result.timed_out)
            log = result.log_path.read_text()
            self.assertIn("started", log)
            self.assertIn("timed_out: True", log)
            self.assertIn("timeout_seconds: 0.2", log)

    def test_verifier_timeout_is_failure_and_records_timeout_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            worktree.mkdir()
            program = "import time; print('checking', flush=True); time.sleep(30)"
            command = f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"

            result = run_verification(
                [command],
                worktree,
                root / "run",
                timeout_seconds=0.2,
            )

            self.assertFalse(result.passed)
            self.assertTrue(result.timed_out)
            self.assertEqual(result.results[0].exit_code, TIMEOUT_EXIT_CODE)
            self.assertTrue(result.results[0].timed_out)
            log = result.log_path.read_text()
            self.assertIn("checking", log)
            self.assertIn("timed_out: True", log)
            self.assertIn("timeout_seconds: 0.2", log)

    def test_reviewers_forward_timeout_and_expose_timeout_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            diff_path = root / "diff.patch"
            diff_path.write_text("diff")
            verifier_log = root / "verifier.log"
            verifier_log.write_text("passed")
            task = {
                "title": "Review timeout",
                "repo": "demo",
                "goal": "Review the change",
                "acceptance_criteria": "Review completes",
            }
            repo = RepoConfig(
                id="demo",
                path=root,
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=False,
                merge_policy="no_push",
                verify_commands=[],
            )
            agent_result = AgentRunResult(
                agent_id="reviewer",
                command="review",
                exit_code=TIMEOUT_EXIT_CODE,
                log_path=root / "reviewer.log",
                timed_out=True,
            )

            with patch("local_cli_coordinator.review.run_agent", return_value=agent_result) as runner:
                spec = run_spec_review(
                    _agent("review", role="spec_reviewer"),
                    task,
                    ["feature.py"],
                    diff_path,
                    root,
                    run_dir,
                    timeout_seconds=11,
                )
                quality = run_quality_review(
                    _agent("review", role="quality_reviewer"),
                    task,
                    ["feature.py"],
                    diff_path,
                    verifier_log,
                    repo,
                    root,
                    run_dir,
                    timeout_seconds=13,
                )

            self.assertEqual(runner.call_args_list[0].kwargs["timeout_seconds"], 11)
            self.assertEqual(runner.call_args_list[1].kwargs["timeout_seconds"], 13)
            self.assertFalse(spec.passed)
            self.assertTrue(spec.timed_out)
            self.assertFalse(quality.passed)
            self.assertTrue(quality.timed_out)

    def _run_engine_timeout(self, stage: str) -> tuple[str, str, object, str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_path = root / "repo"
            worktree = root / "worktree"
            repo_path.mkdir()
            worktree.mkdir()
            run_dir = root / "runs"
            agents = {"worker": _agent("worker")}
            if stage in {"spec", "quality"}:
                agents["spec"] = _agent("spec", role="spec_reviewer")
            if stage == "quality":
                agents["quality"] = _agent("quality", role="quality_reviewer")
            config = CoordinatorConfig(
                agents=agents,
                repos={
                    "demo": RepoConfig(
                        id="demo",
                        path=repo_path,
                        default_branch="main",
                        remote="origin",
                        branch_prefix="coord/",
                        allow_push=False,
                        merge_policy="no_push",
                        verify_commands=["verify"],
                        review_policy=("tests_only" if stage in {"agent", "verifier"} else "full_review"),
                    )
                },
                policy=_policy(17),
            )
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_task(
                conn,
                title="Timeout task",
                repo="demo",
                source_path="tasks/inbox/timeout.md",
                priority="normal",
                capabilities=["code"],
                goal="Exercise timeout handling.",
                acceptance_criteria=["Timeout is reported."],
                verification_commands=[],
            )
            agent_ok = AgentRunResult("worker", "worker", 0, run_dir / "agent.log")
            agent_timeout = AgentRunResult(
                "worker", "worker", TIMEOUT_EXIT_CODE, run_dir / "agent.log", timed_out=True
            )
            verification_ok = VerificationResult(
                True,
                [CommandResult("verify", 0)],
                run_dir / "verifier.log",
            )
            verification_timeout = VerificationResult(
                False,
                [CommandResult("verify", TIMEOUT_EXIT_CODE, timed_out=True)],
                run_dir / "verifier.log",
                timed_out=True,
            )
            spec_ok = ReviewResult(True, run_dir / "spec.log", run_dir / "spec-prompt.md")
            review_timeout = ReviewResult(
                False,
                run_dir / f"{stage}.log",
                run_dir / f"{stage}-prompt.md",
                timed_out=True,
            )
            agent_result = agent_timeout if stage == "agent" else agent_ok
            verifier_result = verification_timeout if stage == "verifier" else verification_ok
            spec_result = review_timeout if stage == "spec" else spec_ok
            quality_result = review_timeout

            with (
                patch("local_cli_coordinator.engine.create_worktree", return_value=worktree),
                patch("local_cli_coordinator.engine.merge_base", return_value="base-commit"),
                patch("local_cli_coordinator.engine.collect_changed_files", return_value=["feature.py"]),
                patch(
                    "local_cli_coordinator.engine.collect_changed_files_since",
                    return_value=["feature.py"],
                ),
                patch("local_cli_coordinator.engine.diff_patch", return_value="diff"),
                patch("local_cli_coordinator.engine.run_agent", return_value=agent_result) as runner,
                patch("local_cli_coordinator.engine.run_verification", return_value=verifier_result) as verifier,
                patch("local_cli_coordinator.engine.run_spec_review", return_value=spec_result) as spec,
                patch("local_cli_coordinator.engine.run_quality_review", return_value=quality_result) as quality,
            ):
                run_one_ready_task(conn, config, root)
                note = conn.execute(
                    "select note from events where task_id = ? order by id desc limit 1",
                    (task_id,),
                ).fetchone()["note"]
                loop_memory = (root / "state" / "loop_state.md").read_text()
                calls = {
                    "agent": runner,
                    "verifier": verifier,
                    "spec": spec,
                    "quality": quality,
                }
                selected_call = calls[stage]
                timeout_value = selected_call.call_args.kwargs["timeout_seconds"]
                task_state = conn.execute(
                    "select state from tasks where id = ?",
                    (task_id,),
                ).fetchone()["state"]
            conn.close()
            return note, loop_memory, timeout_value, task_state

    def test_engine_records_agent_timeout_event(self) -> None:
        note, loop_memory, timeout_value, state = self._run_engine_timeout("agent")
        self.assertEqual(timeout_value, 17)
        self.assertEqual(state, "failed")
        self.assertIn("agent command timed out", note)
        self.assertIn("next action: inspect agent log and retry", loop_memory)

    def test_engine_records_verifier_timeout_event(self) -> None:
        note, loop_memory, timeout_value, state = self._run_engine_timeout("verifier")
        self.assertEqual(timeout_value, 17)
        self.assertEqual(state, "failed")
        self.assertIn("verification timed out", note)
        self.assertIn("next action: inspect verifier log and retry", loop_memory)

    def test_engine_records_spec_reviewer_timeout_event(self) -> None:
        note, loop_memory, timeout_value, state = self._run_engine_timeout("spec")
        self.assertEqual(timeout_value, 17)
        self.assertEqual(state, "failed")
        self.assertIn("spec review timed out", note)
        self.assertIn("next action: inspect spec reviewer log and retry", loop_memory)
        self.assertNotIn("address spec review feedback", loop_memory)

    def test_engine_records_quality_reviewer_timeout_event(self) -> None:
        note, loop_memory, timeout_value, state = self._run_engine_timeout("quality")
        self.assertEqual(timeout_value, 17)
        self.assertEqual(state, "failed")
        self.assertIn("quality review timed out", note)
        self.assertIn("next action: inspect quality reviewer log and retry", loop_memory)
        self.assertNotIn("address quality review feedback", loop_memory)

    @unittest.skipUnless(os.name == "posix", "process groups require POSIX")
    def test_agent_timeout_kills_started_child_process(self) -> None:
        child_pid = None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            worktree.mkdir()
            script, marker = _write_process_tree_script(root, detached=False)
            prompt = root / "prompt.md"
            prompt.write_text("work")

            try:
                result = run_agent(
                    _agent(_python_command(script)),
                    prompt,
                    worktree,
                    root / "run",
                    timeout_seconds=1.0,
                )
                self.assertTrue(result.timed_out)
                self.assertTrue(marker.exists(), "child did not write its ready marker")
                child_pid = int(marker.read_text())
                self.assertTrue(_process_exited(child_pid))
            finally:
                if child_pid is None and marker.exists():
                    child_pid = int(marker.read_text())
                _kill_if_running(child_pid)

    @unittest.skipUnless(os.name == "posix", "process groups require POSIX")
    def test_verifier_timeout_kills_started_child_process(self) -> None:
        child_pid = None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            worktree.mkdir()
            script, marker = _write_process_tree_script(root, detached=False)

            try:
                result = run_verification(
                    [_python_command(script)],
                    worktree,
                    root / "run",
                    timeout_seconds=1.0,
                )
                self.assertTrue(result.timed_out)
                self.assertTrue(marker.exists(), "child did not write its ready marker")
                child_pid = int(marker.read_text())
                self.assertTrue(_process_exited(child_pid))
            finally:
                if child_pid is None and marker.exists():
                    child_pid = int(marker.read_text())
                _kill_if_running(child_pid)

    @unittest.skipUnless(os.name == "posix", "detached descendants require POSIX")
    def test_detached_descendant_holding_pipes_preserves_output_and_returns_bounded(self) -> None:
        detached_pid = None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            worktree.mkdir()
            script, marker = _write_process_tree_script(root, detached=True)
            prompt = root / "prompt.md"
            prompt.write_text("work")
            started = time.monotonic()
            try:
                result = run_agent(
                    _agent(_python_command(script)),
                    prompt,
                    worktree,
                    root / "run",
                    timeout_seconds=0.5,
                )
                elapsed = time.monotonic() - started
                self.assertTrue(marker.exists(), "detached child did not write its ready marker")
                detached_pid = int(marker.read_text())
                self.assertTrue(result.timed_out)
                self.assertLess(elapsed, 1.5)
                self.assertIn("partial output", result.log_path.read_text())
            finally:
                if detached_pid is None and marker.exists():
                    detached_pid = int(marker.read_text())
                if detached_pid is not None:
                    _kill_if_running(detached_pid)

    def test_timeout_output_is_decoded_once_without_crlf_duplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            program = (
                "import sys, time; "
                "sys.stdout.buffer.write(b'A\\r\\n'); "
                "sys.stdout.buffer.flush(); time.sleep(30)"
            )

            result = run_command(
                [sys.executable, "-c", program],
                cwd=root,
                timeout_seconds=0.2,
            )

            self.assertTrue(result.timed_out)
            self.assertEqual(result.stdout, "A\n")

    def test_reap_leader_never_waits_without_a_timeout(self) -> None:
        process = Mock()
        process.wait.side_effect = [
            subprocess.TimeoutExpired("worker", 0.2),
            subprocess.TimeoutExpired("worker", 0.2),
        ]

        _reap_leader(process)

        self.assertEqual(process.wait.call_count, 2)
        self.assertTrue(
            all(call.kwargs.get("timeout") is not None for call in process.wait.call_args_list)
        )

    def test_timeout_none_completes_normally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            worktree.mkdir()
            prompt = root / "prompt.md"
            prompt.write_text("work")
            program = "print('done')"
            command = f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"

            result = run_agent(
                _agent(command), prompt, worktree, root / "run", timeout_seconds=None
            )

            self.assertEqual(result.exit_code, 0)
            self.assertFalse(result.timed_out)

    def test_timeout_zero_fails_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            worktree.mkdir()
            prompt = root / "prompt.md"
            prompt.write_text("work")
            command = f"{shlex.quote(sys.executable)} -c {shlex.quote('import time; time.sleep(30)')}"
            started = time.monotonic()

            result = run_agent(
                _agent(command), prompt, worktree, root / "run", timeout_seconds=0
            )

            self.assertTrue(result.timed_out)
            self.assertEqual(result.exit_code, TIMEOUT_EXIT_CODE)
            self.assertLess(time.monotonic() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
