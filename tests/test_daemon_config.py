import tempfile
from pathlib import Path
import unittest

from local_cli_coordinator.config import load_config


class TestDaemonConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_dir = self.root / "config"
        self.config_dir.mkdir()

        # Minimal required files
        (self.config_dir / "agents.toml").write_text("[agents]\n")
        (self.config_dir / "repos.toml").write_text("[repos]\n")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_daemon_config(self):
        (self.config_dir / "policy.toml").write_text("""
[task_policy]
require_single_repo = true
require_acceptance_criteria = true
require_verification_commands = true
require_handoff_summary = true
max_files_touched = 3
max_expected_minutes = 30
max_attempts = 3
split_if_touches_multiple_subsystems = true
split_if_research_and_code_are_mixed = true
""")
        config = load_config(self.root)
        self.assertEqual(config.daemon_policy.loop_interval_seconds, 300)
        self.assertEqual(config.daemon_policy.idle_sleep_seconds, 60)
        self.assertTrue(config.daemon_policy.run_discovery_before_tasks)

    def test_custom_daemon_config(self):
        (self.config_dir / "policy.toml").write_text("""
[task_policy]
require_single_repo = true
require_acceptance_criteria = true
require_verification_commands = true
require_handoff_summary = true
max_files_touched = 3
max_expected_minutes = 30
max_attempts = 3
split_if_touches_multiple_subsystems = true
split_if_research_and_code_are_mixed = true

[daemon_policy]
loop_interval_seconds = 600
idle_sleep_seconds = 120
run_discovery_before_tasks = false
""")
        config = load_config(self.root)
        self.assertEqual(config.daemon_policy.loop_interval_seconds, 600)
        self.assertEqual(config.daemon_policy.idle_sleep_seconds, 120)
        self.assertFalse(config.daemon_policy.run_discovery_before_tasks)

if __name__ == "__main__":
    unittest.main()
