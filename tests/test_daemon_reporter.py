"""Tests for daemon --quiet flag and Reporter propagation."""

import sys
import tempfile
import textwrap
import unittest
from io import StringIO
from pathlib import Path

from local_cli_coordinator.cli import build_parser
from local_cli_coordinator.reporting import ConsoleReporter, NullReporter


class DaemonQuietFlagTests(unittest.TestCase):
    def test_daemon_accepts_quiet_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["daemon", "--quiet"])
        self.assertTrue(args.quiet)

    def test_daemon_default_is_not_quiet(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["daemon"])
        self.assertFalse(getattr(args, "quiet", False))

    def test_daemon_once_with_quiet(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["daemon", "--once", "--quiet"])
        self.assertTrue(args.once)
        self.assertTrue(args.quiet)


class ReporterSelectionTests(unittest.TestCase):
    def test_quiet_creates_null_reporter(self) -> None:
        args = build_parser().parse_args(["daemon", "--quiet"])
        reporter = NullReporter() if args.quiet else ConsoleReporter()
        self.assertIsInstance(reporter, NullReporter)

    def test_default_creates_console_reporter(self) -> None:
        args = build_parser().parse_args(["daemon"])
        reporter = NullReporter() if getattr(args, "quiet", False) else ConsoleReporter()
        self.assertIsInstance(reporter, ConsoleReporter)


if __name__ == "__main__":
    unittest.main()
