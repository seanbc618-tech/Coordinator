"""Tests for supervisor administrative CLI commands."""

import tempfile
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.cli import build_parser


class SupervisorParserTest(TestCase):
    """Verify supervisor subcommands parse correctly."""

    def test_supervisor_start_foreground(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["supervisor", "start", "--foreground"])
        self.assertEqual(args.command, "supervisor")
        self.assertEqual(args.supervisor_command, "start")
        self.assertTrue(args.foreground)

    def test_supervisor_status(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["supervisor", "status"])
        self.assertEqual(args.command, "supervisor")
        self.assertEqual(args.supervisor_command, "status")

    def test_supervisor_stop(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["supervisor", "stop"])
        self.assertEqual(args.supervisor_command, "stop")

    def test_project_inspect(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["project", "inspect", "/tmp/repo"])
        self.assertEqual(args.command, "project")
        self.assertEqual(args.project_command, "inspect")
        self.assertEqual(args.path, "/tmp/repo")

    def test_project_add_yes(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["project", "add", "/tmp/repo", "--yes"])
        self.assertEqual(args.command, "project")
        self.assertEqual(args.project_command, "add")
        self.assertEqual(args.path, "/tmp/repo")
        self.assertTrue(args.yes)

    def test_project_add_without_yes(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["project", "add", "/tmp/repo"])
        self.assertFalse(args.yes)

    def test_existing_commands_still_work(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--root", "/tmp/test", "status"])
        self.assertEqual(args.root, "/tmp/test")
        self.assertEqual(args.command, "status")
