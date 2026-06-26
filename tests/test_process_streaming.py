import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from local_cli_coordinator.process import run_command
from local_cli_coordinator.reporting import ExecutionContext, ExecutionEvent


class RecordingReporter:
    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    def emit(self, event: ExecutionEvent) -> None:
        self.events.append(event)


class ProcessStreamingTests(unittest.TestCase):
    def test_stdout_arrives_before_process_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reporter = RecordingReporter()
            first_seen = threading.Event()
            original_emit = reporter.emit

            def emit(event: ExecutionEvent) -> None:
                if event.kind == "stdout" and event.text == "first\n":
                    first_seen.set()
                original_emit(event)

            reporter.emit = emit  # type: ignore[method-assign]
            result_holder: list[object] = []

            def run() -> None:
                result_holder.append(
                    run_command(
                        [
                            sys.executable,
                            "-c",
                            "import time; print('first', flush=True); time.sleep(0.2); print('second', flush=True)",
                        ],
                        cwd=Path(tmp),
                        reporter=reporter,
                        context=ExecutionContext(stage="worker", actor="fake", task_id="task-1"),
                        heartbeat_seconds=0.05,
                    )
                )

            thread = threading.Thread(target=run)
            thread.start()
            self.assertTrue(first_seen.wait(timeout=2.0), "first stdout did not arrive early")
            thread.join(timeout=5.0)
            self.assertEqual(len(result_holder), 1)
            self.assertEqual(getattr(result_holder[0], "stdout"), "first\nsecond\n")

    def test_incremental_stdout_stderr_heartbeat_and_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reporter = RecordingReporter()
            result = run_command(
                [
                    sys.executable,
                    "-c",
                    "import time; print('first', flush=True); time.sleep(0.2); print('second', flush=True)",
                ],
                cwd=Path(tmp),
                reporter=reporter,
                context=ExecutionContext(stage="worker", actor="fake", task_id="task-1"),
                heartbeat_seconds=0.05,
            )
            self.assertEqual(result.stdout, "first\nsecond\n")
            self.assertEqual(
                [e.text for e in reporter.events if e.kind == "stdout"],
                ["first\n", "second\n"],
            )
            self.assertTrue(any(e.kind == "heartbeat" for e in reporter.events))

    def test_stderr_is_streamed_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reporter = RecordingReporter()
            result = run_command(
                [sys.executable, "-c", "import sys; sys.stderr.write('warn\\n'); sys.stderr.flush()"],
                cwd=Path(tmp),
                reporter=reporter,
                context=ExecutionContext(stage="verify", actor="pytest"),
            )
            self.assertEqual(result.stderr, "warn\n")
            self.assertEqual(
                [e.text for e in reporter.events if e.kind == "stderr"],
                ["warn\n"],
            )

    def test_partial_line_flushed_at_eof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reporter = RecordingReporter()
            stdout_lines: list[str] = []

            def sink(text: str) -> None:
                stdout_lines.append(text)

            result = run_command(
                [sys.executable, "-c", "import sys; sys.stdout.write('partial'); sys.stdout.flush()"],
                cwd=Path(tmp),
                reporter=reporter,
                context=ExecutionContext(stage="worker", actor="fake"),
                stdout_sink=sink,
            )
            self.assertEqual(result.stdout, "partial")
            self.assertEqual(stdout_lines, ["partial"])
            self.assertEqual(
                [e.text for e in reporter.events if e.kind == "stdout"],
                ["partial"],
            )

    def test_started_event_includes_exact_command_and_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reporter = RecordingReporter()
            cwd = Path(tmp)
            run_command(
                [sys.executable, "-c", "print('ok')"],
                cwd=cwd,
                reporter=reporter,
                context=ExecutionContext(stage="worker", actor="agent"),
            )
            started = [e for e in reporter.events if e.kind == "started"]
            self.assertEqual(len(started), 1)
            self.assertEqual(started[0].cwd, cwd)
            self.assertIn(sys.executable, started[0].command)
            self.assertIn("ok", started[0].command)

    def test_completed_event_includes_exit_code_and_elapsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reporter = RecordingReporter()
            run_command(
                [sys.executable, "-c", "print('done')"],
                cwd=Path(tmp),
                reporter=reporter,
                context=ExecutionContext(stage="worker", actor="fake"),
            )
            completed = [e for e in reporter.events if e.kind == "completed"]
            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0].exit_code, 0)
            self.assertGreaterEqual(completed[0].elapsed_seconds, 0.0)

    def test_timeout_event_emitted_before_kill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reporter = RecordingReporter()
            result = run_command(
                [sys.executable, "-c", "import time; print('slow', flush=True); time.sleep(30)"],
                cwd=Path(tmp),
                reporter=reporter,
                context=ExecutionContext(stage="worker", actor="fake"),
                timeout_seconds=0.2,
            )
            self.assertTrue(result.timed_out)
            self.assertEqual(result.returncode, 124)
            kinds = [e.kind for e in reporter.events]
            self.assertIn("timeout", kinds)
            self.assertLess(kinds.index("timeout"), kinds.index("completed"))

    def test_sink_oserror_terminates_child_and_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reporter = RecordingReporter()

            def failing_sink(_text: str) -> None:
                raise OSError("disk full")

            with self.assertRaises(OSError):
                run_command(
                    [
                        sys.executable,
                        "-c",
                        "import time; print('tick', flush=True); time.sleep(30)",
                    ],
                    cwd=Path(tmp),
                    reporter=reporter,
                    context=ExecutionContext(stage="worker", actor="fake"),
                    stdout_sink=failing_sink,
                )
            self.assertTrue(any(e.kind == "error" for e in reporter.events))

    @unittest.skipUnless(os.name == "posix", "process groups require POSIX")
    def test_keyboard_interrupt_emits_event_kills_child_and_reraises(self) -> None:
        import selectors
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            reporter = RecordingReporter()
            marker = Path(tmp) / "child.pid"
            script = (
                "import os, time; from pathlib import Path; "
                f"Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)"
            )
            child_pid: int | None = None
            original_select = selectors.DefaultSelector.select

            def interrupting_select(self, timeout=None):  # type: ignore[no-untyped-def]
                if marker.exists():
                    raise KeyboardInterrupt()
                return original_select(self, timeout)

            with patch.object(selectors.DefaultSelector, "select", interrupting_select):
                with self.assertRaises(KeyboardInterrupt):
                    run_command(
                        [sys.executable, "-c", script],
                        cwd=Path(tmp),
                        reporter=reporter,
                        context=ExecutionContext(stage="worker", actor="fake"),
                    )

            self.assertTrue(marker.exists())
            child_pid = int(marker.read_text())
            self.assertTrue(any(e.kind == "interrupted" for e in reporter.events))
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)

    def test_durable_sink_receives_same_output_as_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reporter = RecordingReporter()
            stdout_chunks: list[str] = []
            stderr_chunks: list[str] = []

            result = run_command(
                [
                    sys.executable,
                    "-c",
                    "import sys; print('out'); sys.stderr.write('err\\n')",
                ],
                cwd=Path(tmp),
                reporter=reporter,
                context=ExecutionContext(stage="worker", actor="fake"),
                stdout_sink=stdout_chunks.append,
                stderr_sink=stderr_chunks.append,
            )
            self.assertEqual(result.stdout, "out\n")
            self.assertEqual(result.stderr, "err\n")
            self.assertEqual("".join(stdout_chunks), "out\n")
            self.assertEqual("".join(stderr_chunks), "err\n")


    @unittest.skipUnless(os.name == "posix", "process groups require POSIX")
    def test_keyboard_interrupt_kills_grandchild_process_tree(self) -> None:
        """Ctrl+C kills both child and grandchild via process group."""
        import selectors
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            reporter = RecordingReporter()
            child_marker = Path(tmp) / "child.pid"
            grandchild_marker = Path(tmp) / "grandchild.pid"
            # Child spawns a grandchild, prints to trigger select, then both sleep
            gc_script = (
                "import os, time; from pathlib import Path; "
                f"Path({str(grandchild_marker)!r}).write_text(str(os.getpid())); "
                "time.sleep(30)"
            )
            script = (
                "import subprocess, sys, os, time; from pathlib import Path; "
                f"Path({str(child_marker)!r}).write_text(str(os.getpid())); "
                f"subprocess.Popen([sys.executable, '-c', {gc_script!r}]); "
                "print('child-ready', flush=True); "
                "time.sleep(30)"
            )
            original_select = selectors.DefaultSelector.select

            def interrupting_select(self, timeout=None):  # type: ignore[no-untyped-def]
                # Wait until both child and grandchild are running
                if child_marker.exists() and grandchild_marker.exists():
                    raise KeyboardInterrupt()
                return original_select(self, timeout)

            with patch.object(selectors.DefaultSelector, "select", interrupting_select):
                with self.assertRaises(KeyboardInterrupt):
                    run_command(
                        [sys.executable, "-c", script],
                        cwd=Path(tmp),
                        reporter=reporter,
                        context=ExecutionContext(stage="worker", actor="fake"),
                    )

            self.assertTrue(child_marker.exists())
            self.assertTrue(grandchild_marker.exists())
            child_pid = int(child_marker.read_text())
            grandchild_pid = int(grandchild_marker.read_text())
            self.assertTrue(any(e.kind == "interrupted" for e in reporter.events))
            # Both processes must be dead
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)
            with self.assertRaises(ProcessLookupError):
                os.kill(grandchild_pid, 0)


if __name__ == "__main__":
    unittest.main()