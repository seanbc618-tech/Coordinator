from io import StringIO
from pathlib import Path
import unittest

from local_cli_coordinator.reporting import (
    ConsoleReporter,
    ExecutionContext,
    ExecutionEvent,
    NullReporter,
    NULL_REPORTER,
    Reporter,
)


class ExecutionEventTests(unittest.TestCase):
    def test_event_is_frozen(self) -> None:
        event = ExecutionEvent(kind="started", stage="worker")
        with self.assertRaises(AttributeError):
            event.kind = "stopped"  # type: ignore[misc]

    def test_event_defaults(self) -> None:
        event = ExecutionEvent(kind="heartbeat", stage="worker")
        self.assertEqual(event.actor, "")
        self.assertEqual(event.task_id, "")
        self.assertEqual(event.command, "")
        self.assertIsNone(event.cwd)
        self.assertEqual(event.text, "")
        self.assertEqual(event.elapsed_seconds, 0.0)
        self.assertIsNone(event.exit_code)
        self.assertFalse(event.timed_out)
        self.assertIsNone(event.log_path)


class ExecutionContextTests(unittest.TestCase):
    def test_context_is_frozen(self) -> None:
        ctx = ExecutionContext(stage="worker")
        with self.assertRaises(AttributeError):
            ctx.stage = "verify"  # type: ignore[misc]

    def test_context_defaults(self) -> None:
        ctx = ExecutionContext(stage="verify")
        self.assertEqual(ctx.actor, "")
        self.assertEqual(ctx.task_id, "")
        self.assertIsNone(ctx.log_path)


class NullReporterTests(unittest.TestCase):
    def test_null_reporter_accepts_any_event(self) -> None:
        reporter = NullReporter()
        reporter.emit(ExecutionEvent(kind="heartbeat", stage="worker", elapsed_seconds=15))
        reporter.emit(ExecutionEvent(kind="stdout", stage="verify", actor="pytest", text="pass\n"))

    def test_null_reporter_singleton(self) -> None:
        self.assertIsInstance(NULL_REPORTER, NullReporter)


class ReporterProtocolTests(unittest.TestCase):
    def test_null_reporter_satisfies_protocol(self) -> None:
        reporter: Reporter = NullReporter()
        reporter.emit(ExecutionEvent(kind="started", stage="worker"))

    def test_console_reporter_satisfies_protocol(self) -> None:
        reporter: Reporter = ConsoleReporter(stream=StringIO())
        reporter.emit(ExecutionEvent(kind="started", stage="worker"))


class ConsoleReporterTests(unittest.TestCase):
    def test_renders_started_event_with_exact_command(self) -> None:
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
        self.assertIn("[10:58:01] worker", text)
        self.assertIn("task-123", text)
        self.assertIn("cwd=/tmp/worktree", text)
        self.assertIn("$ claude --print secret=value", text)

    def test_renders_stdout_with_actor_label(self) -> None:
        output = StringIO()
        reporter = ConsoleReporter(stream=output, timestamp_fn=lambda: "10:58:02")
        reporter.emit(ExecutionEvent(kind="stdout", stage="verify", actor="pytest", text="one\n"))
        self.assertIn("[pytest:stdout] one", output.getvalue())

    def test_renders_stderr_with_actor_label(self) -> None:
        output = StringIO()
        reporter = ConsoleReporter(stream=output, timestamp_fn=lambda: "10:58:03")
        reporter.emit(ExecutionEvent(kind="stderr", stage="verify", actor="pytest", text="two\n"))
        self.assertIn("[pytest:stderr] two", output.getvalue())

    def test_renders_heartbeat(self) -> None:
        output = StringIO()
        reporter = ConsoleReporter(stream=output, timestamp_fn=lambda: "10:58:16")
        reporter.emit(ExecutionEvent(kind="heartbeat", stage="worker", elapsed_seconds=15.0))
        text = output.getvalue()
        self.assertIn("[10:58:16] worker", text)
        self.assertIn("15.0s", text)

    def test_renders_completed_event(self) -> None:
        output = StringIO()
        reporter = ConsoleReporter(stream=output, timestamp_fn=lambda: "10:59:03")
        reporter.emit(ExecutionEvent(
            kind="completed",
            stage="worker",
            exit_code=0,
            elapsed_seconds=62.0,
            log_path=Path("runs/worker.log"),
        ))
        text = output.getvalue()
        self.assertIn("exit=0", text)
        self.assertIn("62.0s", text)
        self.assertIn("runs/worker.log", text)

    def test_renders_timed_out_event(self) -> None:
        output = StringIO()
        reporter = ConsoleReporter(stream=output, timestamp_fn=lambda: "11:00:00")
        reporter.emit(ExecutionEvent(
            kind="completed",
            stage="worker",
            exit_code=124,
            timed_out=True,
            elapsed_seconds=30.0,
        ))
        text = output.getvalue()
        self.assertIn("timed out", text)
        self.assertIn("exit=124", text)

    def test_immediate_flush(self) -> None:
        class FlushTracking(StringIO):
            flush_count = 0
            def flush(self) -> None:
                FlushTracking.flush_count += 1
                super().flush()

        output = FlushTracking()
        reporter = ConsoleReporter(stream=output)
        reporter.emit(ExecutionEvent(kind="stdout", stage="worker", actor="claude", text="line\n"))
        self.assertGreaterEqual(FlushTracking.flush_count, 1)

    def test_oserror_does_not_escape(self) -> None:
        class BrokenStream:
            def write(self, s: str) -> int:
                raise OSError("broken pipe")
            def flush(self) -> None:
                pass

        reporter = ConsoleReporter(stream=BrokenStream())
        # Must not raise
        reporter.emit(ExecutionEvent(kind="stdout", stage="worker", text="data\n"))

    def test_valueerror_does_not_escape(self) -> None:
        class BrokenStream:
            def write(self, s: str) -> int:
                raise ValueError("encoding")
            def flush(self) -> None:
                pass

        reporter = ConsoleReporter(stream=BrokenStream())
        # Must not raise
        reporter.emit(ExecutionEvent(kind="stdout", stage="worker", text="data\n"))

    def test_partial_lines_buffered_until_newline(self) -> None:
        output = StringIO()
        reporter = ConsoleReporter(stream=output, timestamp_fn=lambda: "12:00:00")
        reporter.emit(ExecutionEvent(kind="stdout", stage="worker", actor="claude", text="partial"))
        self.assertEqual(output.getvalue(), "")
        reporter.emit(ExecutionEvent(kind="stdout", stage="worker", actor="claude", text=" done\n"))
        self.assertIn("[claude:stdout] partial done", output.getvalue())

    def test_flush_on_completed_event(self) -> None:
        output = StringIO()
        reporter = ConsoleReporter(stream=output, timestamp_fn=lambda: "12:00:00")
        reporter.emit(ExecutionEvent(kind="stdout", stage="worker", actor="claude", text="partial"))
        reporter.emit(ExecutionEvent(kind="completed", stage="worker", exit_code=0, elapsed_seconds=1.0))
        self.assertIn("partial", output.getvalue())


if __name__ == "__main__":
    unittest.main()
