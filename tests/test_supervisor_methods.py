"""Tests for multi-client Supervisor methods."""

import tempfile
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.config import (
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
)
from local_cli_coordinator.db import connect, init_db, create_task
from local_cli_coordinator.supervisor_methods import SupervisorMethods
from local_cli_coordinator.supervisor_protocol import RequestEnvelope


def _request(method: str, project_id: str | None = None, **params) -> RequestEnvelope:
    return RequestEnvelope(
        protocol_version=1,
        request_id="req-1",
        project_id=project_id,
        method=method,
        params=params,
    )


class SupervisorMethodsTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(self.db_path)
        init_db(self.conn)
        self.methods = SupervisorMethods()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_project_status(self) -> None:
        create_task(
            self.conn, title="t", repo="r", source_path="x",
            priority="normal", capabilities=["code"], goal="g",
            acceptance_criteria=["a"], verification_commands=[],
            project_id="proj-a",
        )
        resp = self.methods.handle(
            self.conn, _request("project.status", project_id="proj-a")
        )
        self.assertTrue(resp.ok)
        self.assertIn("counts", resp.result)

    def test_project_status_unknown(self) -> None:
        resp = self.methods.handle(
            self.conn, _request("project.status", project_id="nonexistent")
        )
        self.assertFalse(resp.ok)
        self.assertIn("not found", resp.error)

    def test_chat_send_requires_registered_project(self) -> None:
        methods = SupervisorMethods(
            config=CoordinatorConfig(
                agents={},
                repos={},
                policy=PolicyConfig(
                    require_single_repo=False,
                    require_acceptance_criteria=False,
                    require_verification_commands=False,
                    require_handoff_summary=False,
                    max_files_touched=10,
                    max_expected_minutes=60,
                    max_attempts=3,
                    split_if_touches_multiple_subsystems=False,
                    split_if_research_and_code_are_mixed=False,
                ),
                daemon_policy=DaemonPolicyConfig(),
            )
        )
        resp = methods.handle(
            self.conn,
            _request("chat.send", project_id="proj-a", text="hello"),
        )
        self.assertFalse(resp.ok)
        self.assertIn("not registered", resp.error)

    def test_project_pause_resume(self) -> None:
        resp_pause = self.methods.handle(
            self.conn, _request("project.pause", project_id="proj-a")
        )
        self.assertTrue(resp_pause.ok)

        resp_resume = self.methods.handle(
            self.conn, _request("project.resume", project_id="proj-a")
        )
        self.assertTrue(resp_resume.ok)

    def test_project_stop(self) -> None:
        resp = self.methods.handle(
            self.conn, _request("project.stop", project_id="proj-a")
        )
        self.assertTrue(resp.ok)

    def test_unknown_method(self) -> None:
        resp = self.methods.handle(
            self.conn, _request("unknown.method", project_id="proj-a")
        )
        self.assertFalse(resp.ok)
        self.assertIn("unsupported", resp.error)

    def test_events_subscribe(self) -> None:
        resp = self.methods.handle(
            self.conn, _request("events.subscribe", project_id="proj-a")
        )
        self.assertTrue(resp.ok)
        self.assertIn("subscription_id", resp.result)

    def test_events_replay(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("events.replay", project_id="proj-a", after=0),
        )
        self.assertTrue(resp.ok)
        self.assertIn("events", resp.result)
