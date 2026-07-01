"""Phase 15 red tests: onboarding profile presets and persistence contracts.

Owner: Grok (Phase 15 Task 0)
Expected before implementation: onboarding_profiles module and migration 025 missing.
"""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


class OnboardingProfilePresetTests(unittest.TestCase):
    def test_default_preset_is_observe(self) -> None:
        from local_cli_coordinator.onboarding_profiles import DEFAULT_PRESET

        self.assertEqual(DEFAULT_PRESET, "observe")

    def test_validate_detected_profile_rejects_unknown(self) -> None:
        from local_cli_coordinator.onboarding_profiles import validate_detected_profile

        with self.assertRaises(ValueError):
            validate_detected_profile("rust")

    def test_validate_preset_rejects_unknown(self) -> None:
        from local_cli_coordinator.onboarding_profiles import validate_preset

        with self.assertRaises(ValueError):
            validate_preset("autonomous")

    def test_observe_preset_disables_autonomy(self) -> None:
        from local_cli_coordinator.onboarding_profiles import preset_policy_delta

        delta = preset_policy_delta("observe")
        self.assertFalse(delta["autonomy_enabled"])
        self.assertFalse(delta["allow_push"])
        self.assertFalse(delta["allow_task_execution"])

    def test_assist_allows_drafting_without_autonomy(self) -> None:
        from local_cli_coordinator.onboarding_profiles import preset_policy_delta

        delta = preset_policy_delta("assist")
        self.assertFalse(delta["autonomy_enabled"])
        self.assertTrue(delta["allow_chat"])
        self.assertFalse(delta["allow_autonomous_loop"])

    def test_managed_allows_task_execution_with_review(self) -> None:
        from local_cli_coordinator.onboarding_profiles import preset_policy_delta

        delta = preset_policy_delta("managed")
        self.assertFalse(delta["autonomy_enabled"])
        self.assertTrue(delta["allow_task_execution"])
        self.assertTrue(delta["require_human_review_before_push"])

    def test_overnight_requires_explicit_autonomy_flag(self) -> None:
        from local_cli_coordinator.onboarding_profiles import (
            preset_enables_autonomy,
            preset_policy_delta,
        )

        delta = preset_policy_delta("overnight")
        self.assertFalse(preset_enables_autonomy("overnight"))
        self.assertTrue(preset_enables_autonomy("overnight", enable_autonomy=True))
        self.assertTrue(delta["allow_autonomous_loop"])

    def test_delivery_never_auto_enables_merge(self) -> None:
        from local_cli_coordinator.onboarding_profiles import preset_policy_delta

        delta = preset_policy_delta("delivery")
        self.assertFalse(delta["autonomy_enabled"])
        self.assertFalse(delta["auto_merge"])
        self.assertFalse(delta["allow_push_without_confirmation"])


class OnboardingProfilePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        (self.repo / "pyproject.toml").write_text("[project]\nname='demo'\n")
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_profile_run_persists_inspection(self) -> None:
        from local_cli_coordinator.onboarding_profiles import record_profile_run
        from local_cli_coordinator.project_inspector import inspect_project_shape

        inspection = inspect_project_shape(self.repo)
        run_id = record_profile_run(
            self.conn,
            repo_path=str(self.repo.resolve()),
            inspection=inspection,
        )
        self.conn.commit()
        row = self.conn.execute(
            "select * from project_profile_runs where id = ?",
            (run_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["detected_profile"], inspection.detected_profile)
        self.assertEqual(row["recommended_preset"], inspection.recommended_preset)
        self.assertEqual(
            json.loads(row["verify_commands_json"]),
            inspection.verify_commands,
        )

    def test_record_onboarding_run_persists_dry_run(self) -> None:
        from local_cli_coordinator.onboarding_profiles import record_onboarding_run

        run_id = record_onboarding_run(
            self.conn,
            mode="dry_run",
            status="planned",
            profile_name="python",
            preset_name="observe",
            repo_path=str(self.repo.resolve()),
            plan_json={"preset": "observe", "autonomy_enabled": False},
        )
        self.conn.commit()
        row = self.conn.execute(
            "select mode, status, preset_name from onboarding_runs where id = ?",
            (run_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["mode"], "dry_run")
        self.assertEqual(row["status"], "planned")
        self.assertEqual(row["preset_name"], "observe")


if __name__ == "__main__":
    unittest.main()