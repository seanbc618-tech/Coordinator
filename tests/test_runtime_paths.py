"""Tests for global Coordinator runtime paths."""

import stat
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from local_cli_coordinator.runtime_paths import RuntimePaths, resolve_runtime_paths


class RuntimePathTests(TestCase):
    def test_xdg_overrides_are_respected(self) -> None:
        env = {
            "XDG_CONFIG_HOME": "/tmp/cfg",
            "XDG_DATA_HOME": "/tmp/data",
            "XDG_STATE_HOME": "/tmp/state",
        }
        with patch.dict("os.environ", env, clear=True):
            paths = resolve_runtime_paths()
        self.assertEqual(paths.config_dir, Path("/tmp/cfg/coordinator"))
        self.assertEqual(paths.database, Path("/tmp/data/coordinator/coordinator.db"))
        self.assertEqual(paths.socket, Path("/tmp/state/coordinator/coordinator.sock"))

    def test_create_makes_private_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            paths = RuntimePaths(base / "config", base / "data", base / "state")
            paths.create()
            for directory in (paths.config_dir, paths.data_dir, paths.state_dir):
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)

    def test_coordinator_home_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch.dict("os.environ", {"COORDINATOR_HOME": str(home)}, clear=True):
                paths = resolve_runtime_paths()
            self.assertEqual(paths.config_dir, home / "config")
            self.assertEqual(paths.data_dir, home / "data")
            self.assertEqual(paths.state_dir, home / "state")

    def test_default_paths_use_home(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            paths = resolve_runtime_paths()
        self.assertIn(".config/coordinator", str(paths.config_dir))
        self.assertIn(".local/share/coordinator", str(paths.data_dir))
        self.assertIn(".local/state/coordinator", str(paths.state_dir))

    def test_lock_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            paths = RuntimePaths(base / "config", base / "data", base / "state")
            self.assertEqual(paths.lock, base / "state" / "supervisor.lock")

    def test_frozen(self) -> None:
        paths = RuntimePaths(Path("/a"), Path("/b"), Path("/c"))
        with self.assertRaises(AttributeError):
            paths.config_dir = Path("/d")  # type: ignore[misc]
