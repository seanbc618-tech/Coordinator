"""Tests for the no-argument Coordinator TUI launcher."""

from __future__ import annotations

import io
import os
import shutil
from contextlib import contextmanager
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.projects import ProjectDraft, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.supervisor_process import EnsureSupervisorResult
from local_cli_coordinator.tui_bundle import LocatedTuiBundle, TuiBundleManifest
from tests.helpers import SRC, init_git_repo

ONBOARDING_PROJECT_ID = "__onboarding__"


def _manifest() -> TuiBundleManifest:
    return TuiBundleManifest(
        protocol_version=1,
        build_hash="abc123",
        bundle="entry.js",
        source_map="entry.js.map",
        built_at="2026-01-01T00:00:00.000Z",
    )


def _located_bundle() -> LocatedTuiBundle:
    return LocatedTuiBundle(manifest=_manifest())


class TuiLauncherTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name) / "home"
        self.repo = Path(self._tmpdir.name) / "repo"
        init_git_repo(self.repo)
        self.subdir = self.repo / "src" / "pkg"
        self.subdir.mkdir(parents=True)
        self.paths = RuntimePaths(
            config_dir=self.home / "config",
            data_dir=self.home / "data",
            state_dir=self.home / "state",
        )
        self.paths.create()
        self._old_home = os.environ.get("COORDINATOR_HOME")
        os.environ["COORDINATOR_HOME"] = str(self.home)

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("COORDINATOR_HOME", None)
        else:
            os.environ["COORDINATOR_HOME"] = self._old_home
        self._tmpdir.cleanup()


class ResolveGitRootTests(TuiLauncherTestBase):
    def test_resolve_from_repository_root(self) -> None:
        from local_cli_coordinator.tui_launcher import resolve_git_root

        self.assertEqual(resolve_git_root(self.repo), self.repo.resolve())

    def test_resolve_from_subdirectory_without_changing_global_cwd(self) -> None:
        from local_cli_coordinator.tui_launcher import resolve_git_root

        original = Path.cwd()
        with tempfile.TemporaryDirectory() as outside:
            os.chdir(outside)
            resolved = resolve_git_root(self.subdir)
            cwd_while_outside = Path.cwd()
            os.chdir(original)
            self.assertEqual(resolved, self.repo.resolve())
            self.assertEqual(cwd_while_outside, Path(outside).resolve())


class OutsideGitTests(unittest.TestCase):
    def test_launch_exits_2_with_concise_error(self) -> None:
        from local_cli_coordinator.tui_launcher import launch_tui

        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside"
            outside.mkdir()
            with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                code = launch_tui(start_path=outside)
            self.assertEqual(code, 2)
            self.assertIn("git", stderr.getvalue().lower())


class BuildTuiArgvTests(TuiLauncherTestBase):
    def test_registered_project_uses_project_id(self) -> None:
        from local_cli_coordinator.tui_launcher import build_tui_argv

        conn = connect(self.paths.database)
        init_db(conn)
        project_id = register_project(
            conn,
            ProjectDraft(canonical_path=self.repo.resolve(), repo_id="demo/repo"),
            confirmed=True,
        )
        conn.close()

        argv = build_tui_argv(
            paths=self.paths,
            bundle_path=Path("/bundle/entry.js"),
            git_root=self.repo.resolve(),
            node_executable="node",
        )
        self.assertEqual(
            argv,
            [
                "node",
                "/bundle/entry.js",
                str(self.paths.socket),
                project_id,
            ],
        )

    def test_unregistered_project_uses_onboarding_placeholder_and_canonical_path(
        self,
    ) -> None:
        from local_cli_coordinator.tui_launcher import build_tui_argv

        conn = connect(self.paths.database)
        init_db(conn)
        conn.close()

        argv = build_tui_argv(
            paths=self.paths,
            bundle_path=Path("/bundle/entry.js"),
            git_root=self.repo.resolve(),
            node_executable="node",
        )
        self.assertEqual(
            argv,
            [
                "node",
                "/bundle/entry.js",
                str(self.paths.socket),
                ONBOARDING_PROJECT_ID,
                str(self.repo.resolve()),
            ],
        )


class LaunchTuiIntegrationTests(TuiLauncherTestBase):
    def _patch_launch_dependencies(
        self,
        *,
        ensure_result: EnsureSupervisorResult | None = None,
        node_executable: str | None = "node",
        wait_returncode: int = 0,
    ) -> tuple[mock.MagicMock, mock.MagicMock]:
        if ensure_result is None:
            ensure_result = EnsureSupervisorResult(attached=True, started=False, pid=4242)

        process_mock = mock.MagicMock()
        process_mock.poll.return_value = None
        process_mock.wait.return_value = wait_returncode

        located = _located_bundle()

        @contextmanager
        def fake_as_file():
            yield Path("/bundle/entry.js")

        located_mock = mock.Mock(spec=LocatedTuiBundle)
        located_mock.manifest = located.manifest
        located_mock.as_file = fake_as_file

        which_patch = mock.patch(
            "local_cli_coordinator.tui_launcher.shutil.which",
            return_value=node_executable,
        )
        ensure_patch = mock.patch(
            "local_cli_coordinator.tui_launcher.ensure_supervisor",
            return_value=ensure_result,
        )
        bundle_patch = mock.patch(
            "local_cli_coordinator.tui_launcher.locate_tui_bundle",
            return_value=located_mock,
        )
        spawn_patch = mock.patch(
            "local_cli_coordinator.tui_launcher._spawn_tui_process",
            return_value=process_mock,
        )
        which_patch.start()
        ensure_mock = ensure_patch.start()
        bundle_patch.start()
        spawn_mock = spawn_patch.start()
        self.addCleanup(which_patch.stop)
        self.addCleanup(ensure_patch.stop)
        self.addCleanup(bundle_patch.stop)
        self.addCleanup(spawn_patch.stop)
        return spawn_mock, ensure_mock

    def test_launch_from_repository_root_invokes_node_with_expected_argv(self) -> None:
        from local_cli_coordinator.tui_launcher import launch_tui

        spawn_mock, ensure_mock = self._patch_launch_dependencies()
        code = launch_tui(start_path=self.repo)
        self.assertEqual(code, 0)
        ensure_mock.assert_called_once()
        argv = spawn_mock.call_args.args[0]
        self.assertEqual(argv[0], "node")
        self.assertEqual(argv[2], str(self.paths.socket))
        self.assertEqual(argv[3], ONBOARDING_PROJECT_ID)
        self.assertEqual(argv[4], str(self.repo.resolve()))

    def test_launch_from_subdirectory_resolves_canonical_root(self) -> None:
        from local_cli_coordinator.tui_launcher import launch_tui

        spawn_mock, _ = self._patch_launch_dependencies()
        launch_tui(start_path=self.subdir)
        argv = spawn_mock.call_args.args[0]
        self.assertEqual(argv[4], str(self.repo.resolve()))

    def test_launch_propagates_tui_exit_code(self) -> None:
        from local_cli_coordinator.tui_launcher import launch_tui

        self._patch_launch_dependencies(wait_returncode=17)
        self.assertEqual(launch_tui(start_path=self.repo), 17)

    def test_spawn_tui_process_inherits_stdio(self) -> None:
        from local_cli_coordinator.tui_launcher import _spawn_tui_process

        with mock.patch(
            "local_cli_coordinator.tui_launcher.subprocess.Popen",
        ) as popen_mock:
            _spawn_tui_process(["node", "entry.js", "/sock", "proj-1"])
        kwargs = popen_mock.call_args.kwargs
        self.assertIsNone(kwargs["stdin"])
        self.assertIsNone(kwargs["stdout"])
        self.assertIsNone(kwargs["stderr"])


    def test_existing_supervisor_is_reused(self) -> None:
        from local_cli_coordinator.tui_launcher import launch_tui

        _, ensure_mock = self._patch_launch_dependencies(
            ensure_result=EnsureSupervisorResult(attached=True, started=False, pid=99),
        )
        launch_tui(start_path=self.repo)
        result = ensure_mock.return_value
        self.assertTrue(result.attached)
        self.assertFalse(result.started)

    def test_absent_supervisor_is_started(self) -> None:
        from local_cli_coordinator.tui_launcher import launch_tui

        _, ensure_mock = self._patch_launch_dependencies(
            ensure_result=EnsureSupervisorResult(attached=False, started=True, pid=100),
        )
        launch_tui(start_path=self.repo)
        result = ensure_mock.return_value
        self.assertFalse(result.attached)
        self.assertTrue(result.started)


class MissingNodeTests(TuiLauncherTestBase):
    def test_missing_node_prints_error_and_exits_nonzero(self) -> None:
        from local_cli_coordinator.tui_launcher import launch_tui

        import io

        with mock.patch(
            "local_cli_coordinator.tui_launcher.shutil.which",
            return_value=None,
        ):
            with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                code = launch_tui(start_path=self.repo)
        self.assertNotEqual(code, 0)
        self.assertIn("node", stderr.getvalue().lower())


class SignalForwardingTests(unittest.TestCase):
    def test_terminal_signals_forward_to_child_process(self) -> None:
        from local_cli_coordinator.tui_launcher import (
            FORWARDED_SIGNALS,
            _run_tui_with_signal_forwarding,
        )

        child = mock.MagicMock()
        child.poll = mock.Mock(return_value=None)
        child.wait.return_value = 0

        with mock.patch(
            "local_cli_coordinator.tui_launcher.signal.signal",
        ) as signal_signal:
            signal_signal.side_effect = lambda _sig, _handler: lambda *_args: None
            code = _run_tui_with_signal_forwarding(child)
            install_handlers = [
                call.args[1]
                for call in signal_signal.call_args_list[: len(FORWARDED_SIGNALS)]
            ]

        self.assertEqual(code, 0)
        self.assertEqual(len(install_handlers), len(FORWARDED_SIGNALS))
        for sig, handler in zip(FORWARDED_SIGNALS, install_handlers, strict=True):
            handler(sig, None)
            child.send_signal.assert_any_call(sig)


@unittest.skipUnless(shutil.which("node"), "node not available in PATH")
class NodeAvailabilitySmokeTest(TuiLauncherTestBase):
    def test_node_is_available_for_real_launch_path(self) -> None:
        from local_cli_coordinator.tui_launcher import find_node_executable

        self.assertEqual(find_node_executable(), shutil.which("node"))


if __name__ == "__main__":
    unittest.main()