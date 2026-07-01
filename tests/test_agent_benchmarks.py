"""Phase 16 red tests: local fixture agent benchmarks.

Owner: Grok (Phase 16 Task 0)
Expected before implementation: agent_benchmarks module missing.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.runtime_paths import RuntimePaths


class AgentBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.paths = RuntimePaths(
            self.tmp / "config", self.tmp / "data", self.tmp / "state"
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_benchmark_module_import(self) -> None:
        from local_cli_coordinator.agent_benchmarks import (
            BenchmarkError,
            run_agent_benchmark,
        )

        self.assertTrue(callable(run_agent_benchmark))
        self.assertTrue(issubclass(BenchmarkError, ValueError))

    def test_run_agent_benchmark_uses_local_fixture_only(self) -> None:
        from local_cli_coordinator.agent_benchmarks import run_agent_benchmark

        with patch("subprocess.run") as mocked_run:
            result = run_agent_benchmark(
                self.conn,
                agent_id="worker",
                benchmark_name="worker_smoke",
                agent_command="true",
            )
            mocked_run.assert_not_called()
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.score, 1.0)
        row = self.conn.execute(
            "select * from agent_benchmark_runs where id = ?",
            (result.run_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["fixture_name"], "success.json")

    def test_blocked_provider_command_rejected(self) -> None:
        from local_cli_coordinator.agent_benchmarks import (
            BenchmarkError,
            assert_benchmark_safe_command,
        )

        with self.assertRaises(BenchmarkError):
            assert_benchmark_safe_command("grok --prompt-file x")

    def test_failure_fixture_records_fail_status(self) -> None:
        from local_cli_coordinator.agent_benchmarks import run_agent_benchmark

        result = run_agent_benchmark(
            self.conn,
            agent_id="worker",
            benchmark_name="worker_failure",
            agent_command="true",
        )
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.score, 0.0)


if __name__ == "__main__":
    unittest.main()