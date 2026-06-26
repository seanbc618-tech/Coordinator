"""Wheel install must ship SQL migrations; init_db must not rely on repo root."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_WHEEL_INIT_DB_SCRIPT = """
import os
import sqlite3
import tempfile
from pathlib import Path

os.environ.pop("PYTHONPATH", None)

from local_cli_coordinator.db import connect, init_db

with tempfile.TemporaryDirectory() as tmp:
    conn = connect(Path(tmp) / "wheel-test.db")
    init_db(conn)
    row = conn.execute(
        "select name from sqlite_master where type='table' and name='tasks'"
    ).fetchone()
    if row is None:
        raise SystemExit("tasks table missing after wheel init_db")
print("ok")
"""


class WheelMigrationsTests(unittest.TestCase):
    def test_wheel_init_db_creates_tasks_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            venv_dir = tmp_path / "coord-wheel-test"

            build = subprocess.run(
                [sys.executable, "-m", "build", "--wheel"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr or build.stdout)

            wheels = sorted((ROOT / "dist").glob("local_cli_coordinator-*.whl"))
            self.assertTrue(wheels, "expected built wheel under dist/")
            wheel = wheels[-1]

            subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                check=True,
            )
            pip = venv_dir / "bin" / "pip"
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            subprocess.run(
                [str(pip), "install", "--force-reinstall", str(wheel)],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            py = venv_dir / "bin" / "python"
            probe = subprocess.run(
                [str(py), "-c", _WHEEL_INIT_DB_SCRIPT],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                probe.returncode,
                0,
                probe.stderr or probe.stdout,
            )
            self.assertIn("ok", probe.stdout)


if __name__ == "__main__":
    unittest.main()