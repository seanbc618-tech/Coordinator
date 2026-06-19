import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.helpers import init_git_repo, run_cli
from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    DiscoverySourceConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import connect, get_task, init_db, list_tasks
from local_cli_coordinator.discovery import list_findings
from local_cli_coordinator.engine import run_daemon_cycle
from local_cli_coordinator.memory import loop_memory_path


def loop_config(repo_path: Path, *, discovery_command: str) -> CoordinatorConfig:
    pass_review = f'{sys.executable} -c "raise SystemExit(0)"'
    return CoordinatorConfig(
        agents={
            "worker": AgentConfig(
                id="worker",
                command=(
                    f'{sys.executable} -c "from pathlib import Path; '
                    "Path('feature.txt').write_text('done')\""
                ),
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
            "spec": AgentConfig(
                id="spec",
                command=pass_review,
                capabilities=["code"],
                max_concurrency=1,
                role="spec_reviewer",
            ),
            "quality": AgentConfig(
                id="quality",
                command=pass_review,
                capabilities=["code"],
                max_concurrency=1,
                role="quality_reviewer",
            ),
        },
        repos={
            "demo": RepoConfig(
                id="demo",
                path=repo_path,
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=False,
                merge_policy="no_push",
                verify_commands=[
                    f'{sys.executable} -c "from pathlib import Path; '
                    "assert Path('feature.txt').read_text() == 'done'\""
                ],
                review_policy="full_review",
            )
        },
        policy=PolicyConfig(
            require_single_repo=True,
            require_acceptance_criteria=True,
            require_verification_commands=False,
            require_handoff_summary=False,
            max_files_touched=3,
            max_expected_minutes=30,
            max_attempts=3,
            split_if_touches_multiple_subsystems=True,
            split_if_research_and_code_are_mixed=True,
        ),
        daemon_policy=DaemonPolicyConfig(run_discovery_before_tasks=True),
        discovery_sources={
            "e2e": DiscoverySourceConfig(
                id="e2e",
                type="command",
                repos={"demo": True},
                command=discovery_command,
            ),
        },
    )


class LoopE2ETests(unittest.TestCase):
    def test_discovery_planning_worker_verification_review_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)

            emit_script = root / "emit_finding.py"
            emit_script.write_text(textwrap.dedent("""
                import json

                print(json.dumps({
                    "id": "finding-e2e-001",
                    "repo": "demo",
                    "source": "command",
                    "title": "Add feature file",
                    "body": "- Create feature.txt with done.\\n- Verification passes.",
                    "severity": "info",
                    "evidence": "source=e2e",
                    "discovered_at": "2026-06-19T15:00:00Z",
                }))
            """).strip(), encoding="utf-8")

            conn = connect(root / "coordinator.db")
            init_db(conn)
            config = loop_config(repo, discovery_command=f"{sys.executable} {emit_script}")
            try:
                cycle = run_daemon_cycle(conn, config, root)
            finally:
                conn.close()

            self.assertEqual(len(list_findings(root)), 1)
            self.assertGreaterEqual(cycle.planned_tasks, 1)
            self.assertGreaterEqual(cycle.imported_tasks, 1)
            self.assertEqual(cycle.tasks_processed, 1)

            conn = connect(root / "coordinator.db")
            init_db(conn)
            try:
                tasks = list_tasks(conn)
                self.assertEqual(len(tasks), 1)
                task = get_task(conn, tasks[0]["id"])
                self.assertEqual(task["state"], "done")

                artifacts = conn.execute(
                    "select kind from artifacts where task_id = ?",
                    (task["id"],),
                ).fetchall()
                kinds = {row["kind"] for row in artifacts}
                self.assertIn("verifier_log", kinds)
                self.assertIn("spec_review_log", kinds)
                self.assertIn("quality_review_log", kinds)

                run_row = conn.execute(
                    "select tasks_processed from daemon_runs order by id desc limit 1"
                ).fetchone()
            finally:
                conn.close()

            self.assertTrue(loop_memory_path(root).exists())
            memory = loop_memory_path(root).read_text()
            self.assertIn("Add feature file", memory)
            self.assertIn("done", memory)

            if run_row is not None:
                self.assertGreaterEqual(int(run_row["tasks_processed"]), 0)

    def test_daemon_once_cli_completes_imported_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            worker_cmd = (
                f'{sys.executable} -c "from pathlib import Path; '
                "Path('feature.txt').write_text('done')\""
            )
            verify_cmd = (
                f'{sys.executable} -c "from pathlib import Path; '
                "assert Path('feature.txt').read_text() == 'done'\""
            )
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "agents.toml").write_text(textwrap.dedent(f"""
                [agents.worker]
                command = '''{worker_cmd}'''
                capabilities = ["code"]
                max_concurrency = 1
                role = "worker"

                [agents.spec]
                command = '''{sys.executable} -c "raise SystemExit(0)"'''
                capabilities = ["code"]
                max_concurrency = 1
                role = "spec_reviewer"

                [agents.quality]
                command = '''{sys.executable} -c "raise SystemExit(0)"'''
                capabilities = ["code"]
                max_concurrency = 1
                role = "quality_reviewer"
            """).strip())
            (config_dir / "repos.toml").write_text(textwrap.dedent(f"""
                [repos.demo]
                path = "{repo}"
                default_branch = "main"
                remote = "origin"
                branch_prefix = "coord/"
                allow_push = false
                merge_policy = "no_push"
                verify_commands = ['''{verify_cmd}''']
                review_policy = "full_review"
            """).strip())
            (config_dir / "policy.toml").write_text(textwrap.dedent("""
                [task_policy]
                require_single_repo = true
                require_acceptance_criteria = true
                require_verification_commands = false
                require_handoff_summary = false
                max_files_touched = 3
                max_expected_minutes = 30
                max_attempts = 3
                split_if_touches_multiple_subsystems = true
                split_if_research_and_code_are_mixed = true

                [daemon_policy]
                run_discovery_before_tasks = true
            """).strip())

            inbox = root / "tasks" / "inbox"
            inbox.mkdir(parents=True)
            (inbox / "feature.md").write_text(textwrap.dedent("""
                # Task: Add feature file

                repo: demo
                priority: normal
                capabilities: [code]

                ## Goal

                Create feature.txt.

                ## Acceptance Criteria

                - feature.txt contains done
            """).strip())

            result = run_cli("--root", str(root), "daemon", "--once")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("processed task", result.stdout)

            conn = connect(root / "coordinator.db")
            init_db(conn)
            try:
                task = get_task(conn, list_tasks(conn)[0]["id"])
                self.assertEqual(task["state"], "done")
                ledger = conn.execute(
                    "select tasks_processed, stop_reason from daemon_runs order by id desc limit 1"
                ).fetchone()
            finally:
                conn.close()

            self.assertIsNotNone(ledger)
            self.assertEqual(int(ledger["tasks_processed"]), 1)
            self.assertTrue(loop_memory_path(root).exists())