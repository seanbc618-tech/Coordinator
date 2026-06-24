import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.commander_policy import (
    admit_commander_response,
    proposal_fingerprint,
    proposal_rejection_reasons,
)
from local_cli_coordinator.commander_protocol import (
    CommanderResponse,
    CommanderTaskProposal,
)
from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    PolicyConfig,
    RepoConfig,
    load_config,
)
from local_cli_coordinator.db import connect, get_task, init_db
from local_cli_coordinator.goals import create_goal, goal_for_task


def _proposal(**overrides) -> CommanderTaskProposal:
    base = {
        "title": "Add roadmap helper",
        "repo": "demo",
        "capabilities": ["code"],
        "goal": "Expose roadmap progress in status output",
        "acceptance_criteria": ["Status shows roadmap section"],
        "verification_commands": [],
        "expected_files": 2,
        "expected_minutes": 20,
        "parent_task_id": None,
        "rationale": "Improves operator visibility",
    }
    base.update(overrides)
    return CommanderTaskProposal(**base)


def _response(*tasks: CommanderTaskProposal) -> CommanderResponse:
    intent = "task_request" if tasks else "conversation"
    return CommanderResponse(
        schema_version=2,
        intent=intent,
        user_reply="I'll create the requested tasks.",
        goal_status="active",
        progress_summary="Ready for first slice",
        tasks=list(tasks),
        stop_reason=None,
    )


def _unsafe_response() -> CommanderResponse:
    return _response(
        _proposal(
            title="Rotate live trading credentials",
            goal="Store API secret for market order execution",
            acceptance_criteria=["Funds transfer script runs"],
            expected_files=10,
            expected_minutes=120,
        )
    )


class CommanderPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = connect(self.root / "coordinator.db")
        init_db(self.conn)

        (self.root / "config").mkdir()
        (self.root / "config" / "agents.toml").write_text(textwrap.dedent("""
            [agents.codex]
            command = "codex exec {prompt_path}"
            capabilities = ["code", "tests"]
            max_concurrency = 1
            role = "worker"
        """).strip())
        (self.root / "config" / "repos.toml").write_text(textwrap.dedent("""
            [repos.demo]
            path = "/tmp/demo"
            default_branch = "main"
            remote = "origin"
            branch_prefix = "coord/"
            allow_push = false
            merge_policy = "no_push"
            verify_commands = ["python -m unittest"]
        """).strip())
        (self.root / "config" / "policy.toml").write_text(textwrap.dedent("""
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
        """).strip())

        self.config = load_config(self.root)
        self.goal_id = create_goal(
            self.conn,
            "Roadmap",
            "Finish roadmap",
            completion_criteria=[],
            constraints=[],
            repo_ids=["demo"],
        )

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_admit_sets_task_and_event_project_id(self) -> None:
        result = admit_commander_response(
            self.conn,
            self.config,
            self.root,
            self.goal_id,
            _response(_proposal()),
            project_id="proj-a",
        )
        self.assertEqual(len(result.accepted_task_ids), 1)
        task_id = result.accepted_task_ids[0]
        task = self.conn.execute(
            "select project_id from tasks where id = ?",
            (task_id,),
        ).fetchone()
        event = self.conn.execute(
            "select project_id from events where task_id = ?",
            (task_id,),
        ).fetchone()
        self.assertEqual(task["project_id"], "proj-a")
        self.assertEqual(event["project_id"], "proj-a")

    def test_admission_inherits_verification_and_links_task(self) -> None:
        result = admit_commander_response(
            self.conn,
            self.config,
            self.root,
            self.goal_id,
            _response(_proposal()),
        )

        self.assertEqual(len(result.accepted_task_ids), 1)
        self.assertEqual(result.rejection_reasons, [])

        task = get_task(self.conn, result.accepted_task_ids[0])
        self.assertEqual(task["verification_commands"], "python -m unittest")
        self.assertEqual(goal_for_task(self.conn, task["id"])["id"], self.goal_id)

    def test_unsafe_proposals_are_rejected(self) -> None:
        result = admit_commander_response(
            self.conn,
            self.config,
            self.root,
            self.goal_id,
            _unsafe_response(),
        )

        self.assertEqual(result.accepted_task_ids, [])
        text = " ".join(result.rejection_reasons)
        self.assertIn("file limit", text)
        self.assertIn("high-risk", text)

    def test_proposal_fingerprint_is_stable(self) -> None:
        proposal = _proposal()
        first = proposal_fingerprint(self.goal_id, proposal)
        second = proposal_fingerprint(self.goal_id, proposal)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_rejects_repo_outside_goal_allowlist(self) -> None:
        reasons = proposal_rejection_reasons(
            self.conn,
            self.config,
            self.goal_id,
            _proposal(repo="other"),
        )
        self.assertTrue(any("goal repo allowlist" in reason for reason in reasons))

    def test_rejects_unconfigured_repo(self) -> None:
        reasons = proposal_rejection_reasons(
            self.conn,
            self.config,
            self.goal_id,
            _proposal(repo="missing"),
        )
        self.assertTrue(any("allowlisted" in reason for reason in reasons))

    def test_rejects_missing_worker_capability(self) -> None:
        reasons = proposal_rejection_reasons(
            self.conn,
            self.config,
            self.goal_id,
            _proposal(capabilities=["research"]),
        )
        self.assertTrue(any("no worker agent supports" in reason for reason in reasons))

    def test_rejects_duplicate_fingerprint(self) -> None:
        proposal = _proposal()
        result = admit_commander_response(
            self.conn,
            self.config,
            self.root,
            self.goal_id,
            _response(proposal),
        )
        self.assertEqual(len(result.accepted_task_ids), 1)

        reasons = proposal_rejection_reasons(
            self.conn,
            self.config,
            self.goal_id,
            proposal,
        )
        self.assertTrue(any("duplicate fingerprint" in reason for reason in reasons))

    def test_rejects_duplicate_title_for_goal(self) -> None:
        first = admit_commander_response(
            self.conn,
            self.config,
            self.root,
            self.goal_id,
            _response(_proposal(title="Unique slice")),
        )
        self.assertEqual(len(first.accepted_task_ids), 1)

        reasons = proposal_rejection_reasons(
            self.conn,
            self.config,
            self.goal_id,
            _proposal(title="Unique slice", goal="Different outcome"),
        )
        self.assertTrue(any("duplicate title" in reason for reason in reasons))

    def test_admission_uses_single_transaction(self) -> None:
        good = _proposal(title="Good task")
        bad = _proposal(
            title="Bad task",
            expected_files=99,
        )
        result = admit_commander_response(
            self.conn,
            self.config,
            self.root,
            self.goal_id,
            _response(good, bad),
        )

        self.assertEqual(len(result.accepted_task_ids), 1)
        self.assertTrue(any("file limit" in reason for reason in result.rejection_reasons))
        self.assertEqual(get_task(self.conn, result.accepted_task_ids[0])["title"], "Good task")