"""Tests for first-run legacy migration integration."""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from local_cli_coordinator.db import connect, init_db, project_list_events, task_counts
from local_cli_coordinator.global_migration import (
    MigrationResult,
    detect_legacy_root,
    format_migration_summary,
    migrate_legacy_root,
    needs_first_run_migration,
    prompt_migration_or_exit,
    _journal_path,
    _read_journal,
)
from local_cli_coordinator.goals import get_goal
from local_cli_coordinator.projects import find_project_by_path
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import ROOT, SRC, init_git_repo


def _copy_legacy_fixture(dest_legacy: Path, *, include_real_db: bool = False) -> None:
    """Populate a temporary legacy root for migration tests."""
    dest_legacy.mkdir(parents=True, exist_ok=True)

    if include_real_db:
        for name in ("coordinator.db", "config", "runs", "tasks", "state"):
            src = ROOT / name
            if not src.exists():
                continue
            dst = dest_legacy / name
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        return

    db = connect(dest_legacy / "coordinator.db")
    init_db(db)
    db.execute(
        "insert into goals(title, objective, status) values (?, ?, ?)",
        ("demo goal", "demo objective", "active"),
    )
    db.execute(
        """
        insert into tasks(
            id, title, repo, state, priority, capabilities, source_path,
            goal, acceptance_criteria, verification_commands, project_id
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "task-demo0001",
            "demo task",
            "demo/repo",
            "ready",
            "normal",
            "code",
            "README.md",
            "demo goal",
            "done",
            "",
            "legacy-default",
        ),
    )
    db.execute(
        "insert into events(task_id, old_state, new_state, note, project_id) "
        "values (?, ?, ?, ?, ?)",
        ("task-demo0001", "inbox", "ready", "imported", "legacy-default"),
    )
    runs_dir = dest_legacy / "runs" / "task-demo0001"
    runs_dir.mkdir(parents=True)
    log_path = runs_dir / "agent.log"
    log_path.write_text("agent output\n", encoding="utf-8")
    db.execute(
        "insert into artifacts(task_id, kind, path, project_id) values (?, ?, ?, ?)",
        (
            "task-demo0001",
            "agent_log",
            str(dest_legacy / "runs" / "task-demo0001" / "agent.log"),
            "legacy-default",
        ),
    )
    db.commit()
    db.close()

    (dest_legacy / "config").mkdir()
    (dest_legacy / "config" / "agents.toml").write_text("[agents]\n", encoding="utf-8")
    (dest_legacy / "tasks").mkdir()


class FirstRunMigrationTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name) / "home"
        self.legacy = Path(self._tmpdir.name) / "legacy"
        _copy_legacy_fixture(self.legacy)
        self.paths = RuntimePaths(
            config_dir=self.home / "config",
            data_dir=self.home / "data",
            state_dir=self.home / "state",
        )
        self._old_home = os.environ.get("COORDINATOR_HOME")
        self._old_legacy = os.environ.get("COORDINATOR_LEGACY_ROOT")
        os.environ["COORDINATOR_HOME"] = str(self.home)
        os.environ.pop("COORDINATOR_LEGACY_ROOT", None)

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("COORDINATOR_HOME", None)
        else:
            os.environ["COORDINATOR_HOME"] = self._old_home
        if self._old_legacy is None:
            os.environ.pop("COORDINATOR_LEGACY_ROOT", None)
        else:
            os.environ["COORDINATOR_LEGACY_ROOT"] = self._old_legacy
        self._tmpdir.cleanup()


class DetectLegacyRootTests(FirstRunMigrationTestBase):
    def test_detects_explicit_legacy_root_env(self) -> None:
        os.environ["COORDINATOR_LEGACY_ROOT"] = str(self.legacy)
        detected = detect_legacy_root(self.paths)
        self.assertEqual(detected, self.legacy.resolve())

    def test_returns_none_when_legacy_env_missing(self) -> None:
        self.assertIsNone(detect_legacy_root(self.paths))

    def test_returns_none_when_legacy_env_points_at_empty_directory(self) -> None:
        empty = Path(self._tmpdir.name) / "empty"
        empty.mkdir()
        os.environ["COORDINATOR_LEGACY_ROOT"] = str(empty)
        self.assertIsNone(detect_legacy_root(self.paths))


class NeedsFirstRunMigrationTests(FirstRunMigrationTestBase):
    def test_true_for_empty_global_state_with_legacy_env(self) -> None:
        os.environ["COORDINATOR_LEGACY_ROOT"] = str(self.legacy)
        self.assertTrue(needs_first_run_migration(self.paths))

    def test_false_when_global_database_exists(self) -> None:
        os.environ["COORDINATOR_LEGACY_ROOT"] = str(self.legacy)
        self.paths.create()
        connect(self.paths.database).close()
        self.assertFalse(needs_first_run_migration(self.paths))

    def test_false_after_marker_written(self) -> None:
        os.environ["COORDINATOR_LEGACY_ROOT"] = str(self.legacy)
        migrate_legacy_root(self.legacy, self.paths)
        self.assertFalse(needs_first_run_migration(self.paths))

    def test_false_without_detectable_legacy_root(self) -> None:
        self.assertFalse(needs_first_run_migration(self.paths))


class MigrationSummaryTests(FirstRunMigrationTestBase):
    def test_summary_lists_detected_legacy_paths(self) -> None:
        summary = format_migration_summary(self.legacy, self.paths)
        self.assertIn(str(self.legacy.resolve()), summary)
        self.assertIn("coordinator.db", summary)
        self.assertIn("config", summary)
        self.assertIn("runs", summary)


class PromptMigrationOrExitTests(FirstRunMigrationTestBase):
    def test_non_interactive_refuses_without_migrating(self) -> None:
        os.environ["COORDINATOR_LEGACY_ROOT"] = str(self.legacy)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            result = prompt_migration_or_exit(
                self.legacy,
                self.paths,
                interactive=False,
            )
        self.assertIsNone(result)
        self.assertFalse(self.paths.database.exists())
        output = stdout.getvalue()
        self.assertIn("Refusing", output)
        self.assertIn("coordinator migrate", output)

    def test_interactive_rejection_leaves_global_state_empty(self) -> None:
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            result = prompt_migration_or_exit(
                self.legacy,
                self.paths,
                interactive=True,
                input_func=lambda _prompt: "n",
            )
        self.assertIsNone(result)
        self.assertFalse(self.paths.database.exists())

    def test_interactive_confirmation_migrates(self) -> None:
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            result = prompt_migration_or_exit(
                self.legacy,
                self.paths,
                interactive=True,
                input_func=lambda _prompt: "yes",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "migrated")
        self.assertTrue(self.paths.database.exists())

    def test_dry_run_summary_printed_before_confirmation(self) -> None:
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            prompt_migration_or_exit(
                self.legacy,
                self.paths,
                interactive=True,
                input_func=lambda _prompt: "no",
            )
        output = stdout.getvalue()
        self.assertIn(str(self.legacy.resolve()), output)
        self.assertIn("Migrate legacy Coordinator state?", output)


class SuccessfulActivationTests(FirstRunMigrationTestBase):
    def test_migration_creates_backup_when_global_state_preexists(self) -> None:
        self.paths.create()
        (self.paths.config_dir / "existing.txt").write_text("keep-me\n", encoding="utf-8")
        connect(self.paths.database).close()

        result = migrate_legacy_root(self.legacy, self.paths)
        self.assertEqual(result.status, "migrated")
        self.assertIsNotNone(result.backup_path)
        self.assertTrue(result.backup_path.exists())

    def test_preserves_original_legacy_source(self) -> None:
        original_db = (self.legacy / "coordinator.db").read_bytes()
        migrate_legacy_root(self.legacy, self.paths)
        self.assertEqual((self.legacy / "coordinator.db").read_bytes(), original_db)


class ArtifactPathRemappingTests(FirstRunMigrationTestBase):
    def test_absolute_artifact_paths_remap_to_global_data_dir(self) -> None:
        migrate_legacy_root(self.legacy, self.paths)

        conn = connect(self.paths.database)
        try:
            row = conn.execute(
                "select path from artifacts where task_id = ?",
                ("task-demo0001",),
            ).fetchone()
            self.assertIsNotNone(row)
            artifact_path = Path(row["path"])
            self.assertTrue(artifact_path.is_absolute())
            self.assertEqual(artifact_path.parent.parent.parent, self.paths.data_dir.resolve())
            self.assertTrue(artifact_path.exists())
        finally:
            conn.close()


class InterruptedRecoveryTests(FirstRunMigrationTestBase):
    def test_resume_after_interrupted_promotion(self) -> None:
        import local_cli_coordinator.global_migration as gm

        existed_before = {
            "config": self.paths.config_dir.exists(),
            "data": self.paths.data_dir.exists(),
            "state": self.paths.state_dir.exists(),
        }
        backup_timestamp = "20260101T000000Z"
        for directory in [self.paths.config_dir, self.paths.data_dir, self.paths.state_dir]:
            gm._staging_dir_for(directory).mkdir(parents=True, exist_ok=True)
        gm._copy_to_per_target_staging(self.legacy, self.paths)
        gm._write_journal(
            self.paths,
            source=str(self.legacy.resolve()),
            backup_path=backup_timestamp,
            existed_before=existed_before,
            completed_steps=[],
            staging_map={},
        )

        result = migrate_legacy_root(self.legacy, self.paths)
        self.assertEqual(result.status, "migrated")
        self.assertFalse(_journal_path(self.paths).exists())
        self.assertTrue(self.paths.database.exists())


class IdempotentLaunchTests(FirstRunMigrationTestBase):
    def test_second_launch_skips_migration_gate(self) -> None:
        os.environ["COORDINATOR_LEGACY_ROOT"] = str(self.legacy)
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            first = prompt_migration_or_exit(
                self.legacy,
                self.paths,
                interactive=True,
                input_func=lambda _prompt: "yes",
            )
        self.assertEqual(first.status, "migrated")
        self.assertFalse(needs_first_run_migration(self.paths))


class RealSchemaVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        if not (ROOT / "coordinator.db").exists():
            self.skipTest("repository coordinator.db fixture unavailable")
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name) / "home"
        self.legacy = Path(self._tmpdir.name) / "legacy"
        _copy_legacy_fixture(self.legacy, include_real_db=True)
        self.paths = RuntimePaths(
            config_dir=self.home / "config",
            data_dir=self.home / "data",
            state_dir=self.home / "state",
        )
        self._old_home = os.environ.get("COORDINATOR_HOME")
        self._old_legacy = os.environ.get("COORDINATOR_LEGACY_ROOT")
        os.environ["COORDINATOR_HOME"] = str(self.home)
        os.environ["COORDINATOR_LEGACY_ROOT"] = str(self.legacy)

        source_conn = connect(ROOT / "coordinator.db")
        self.source_goal_count = source_conn.execute("select count(*) from goals").fetchone()[0]
        self.source_task_count = source_conn.execute("select count(*) from tasks").fetchone()[0]
        self.source_event_count = source_conn.execute("select count(*) from events").fetchone()[0]
        self.sample_goal_id = source_conn.execute("select id from goals order by id limit 1").fetchone()[0]
        self.sample_goal_status = source_conn.execute(
            "select status from goals where id = ?",
            (self.sample_goal_id,),
        ).fetchone()[0]
        self.sample_artifact = dict(
            source_conn.execute("select kind, path from artifacts order by id limit 1").fetchone()
        )
        source_conn.close()

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("COORDINATOR_HOME", None)
        else:
            os.environ["COORDINATOR_HOME"] = self._old_home
        if self._old_legacy is None:
            os.environ.pop("COORDINATOR_LEGACY_ROOT", None)
        else:
            os.environ["COORDINATOR_LEGACY_ROOT"] = self._old_legacy
        self._tmpdir.cleanup()

    def test_migrated_copy_passes_schema_and_data_checks(self) -> None:
        result = migrate_legacy_root(self.legacy, self.paths)
        self.assertEqual(result.status, "migrated")

        conn = connect(self.paths.database)
        try:
            init_db(conn)
            self.assertEqual(
                conn.execute("select count(*) from goals").fetchone()[0],
                self.source_goal_count,
            )
            self.assertEqual(
                conn.execute("select count(*) from tasks").fetchone()[0],
                self.source_task_count,
            )
            self.assertEqual(
                conn.execute("select count(*) from events").fetchone()[0],
                self.source_event_count,
            )
            self.assertEqual(get_goal(conn, self.sample_goal_id)["status"], self.sample_goal_status)
            legacy_conn = connect(self.legacy / "coordinator.db")
            try:
                self.assertEqual(task_counts(conn), task_counts(legacy_conn))
            finally:
                legacy_conn.close()
            events = project_list_events(conn, project_id="legacy-default")
            self.assertGreater(len(events), 0)
            self.assertIsNone(find_project_by_path(conn, ROOT))

            artifact = conn.execute(
                "select kind, path from artifacts order by id limit 1",
            ).fetchone()
            self.assertEqual(artifact["kind"], self.sample_artifact["kind"])
            artifact_path = Path(artifact["path"])
            if not artifact_path.is_absolute():
                artifact_path = self.paths.data_dir / artifact_path
            self.assertTrue(artifact_path.exists())
        finally:
            conn.close()


class LaunchTuiMigrationGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name) / "home"
        self.legacy = Path(self._tmpdir.name) / "legacy"
        self.repo = Path(self._tmpdir.name) / "repo"
        init_git_repo(self.repo)
        _copy_legacy_fixture(self.legacy)
        self._old_home = os.environ.get("COORDINATOR_HOME")
        self._old_legacy = os.environ.get("COORDINATOR_LEGACY_ROOT")
        os.environ["COORDINATOR_HOME"] = str(self.home)
        os.environ["COORDINATOR_LEGACY_ROOT"] = str(self.legacy)

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("COORDINATOR_HOME", None)
        else:
            os.environ["COORDINATOR_HOME"] = self._old_home
        if self._old_legacy is None:
            os.environ.pop("COORDINATOR_LEGACY_ROOT", None)
        else:
            os.environ["COORDINATOR_LEGACY_ROOT"] = self._old_legacy
        self._tmpdir.cleanup()

    def test_launch_refuses_non_interactive_migration(self) -> None:
        from local_cli_coordinator.tui_launcher import launch_tui

        with mock.patch(
            "local_cli_coordinator.tui_launcher.shutil.which",
            return_value="node",
        ):
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    code = launch_tui(start_path=self.repo, interactive=False)
        self.assertEqual(code, 1)
        self.assertIn("Refusing", stdout.getvalue())
        self.assertFalse((self.home / "data" / "coordinator.db").exists())

    def test_launch_migrates_before_supervisor_when_confirmed(self) -> None:
        from local_cli_coordinator.supervisor_process import EnsureSupervisorResult
        from local_cli_coordinator.tui_launcher import launch_tui

        process_mock = mock.MagicMock()
        process_mock.poll.return_value = None
        process_mock.wait.return_value = 0

        located_mock = mock.Mock()
        located_mock.as_file = mock.MagicMock(
            return_value=mock.MagicMock(
                __enter__=mock.Mock(return_value=Path("/bundle/entry.js")),
                __exit__=mock.Mock(return_value=False),
            )
        )

        with mock.patch(
            "local_cli_coordinator.tui_launcher.shutil.which",
            return_value="node",
        ):
            with mock.patch(
                "local_cli_coordinator.tui_launcher.ensure_supervisor",
                return_value=EnsureSupervisorResult(attached=False, started=True, pid=42),
            ) as ensure_mock:
                with mock.patch(
                    "local_cli_coordinator.tui_launcher.locate_tui_bundle",
                    return_value=located_mock,
                ):
                    with mock.patch(
                        "local_cli_coordinator.tui_launcher._spawn_tui_process",
                        return_value=process_mock,
                    ):
                        with mock.patch("sys.stdout", new_callable=io.StringIO):
                            code = launch_tui(
                                start_path=self.repo,
                                interactive=True,
                                input_func=lambda _prompt: "yes",
                            )
        self.assertEqual(code, 0)
        ensure_mock.assert_called_once()
        self.assertTrue((self.home / "data" / "coordinator.db").exists())


if __name__ == "__main__":
    unittest.main()