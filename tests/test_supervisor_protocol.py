import json
import unittest

from local_cli_coordinator.supervisor_protocol import (
    PROTOCOL_VERSION,
    EventEnvelope,
    ProtocolError,
    RequestEnvelope,
    ResponseEnvelope,
    assert_event_cursors_monotonic,
    decode_envelope,
    encode_envelope,
)


class SupervisorProtocolTests(unittest.TestCase):
    def test_request_round_trip(self) -> None:
        request = RequestEnvelope(
            protocol_version=1,
            request_id="req-1",
            project_id="project-a",
            method="project.status",
            params={},
        )
        self.assertEqual(decode_envelope(encode_envelope(request)), request)

    def test_response_round_trip(self) -> None:
        response = ResponseEnvelope(
            protocol_version=1,
            request_id="req-2",
            ok=True,
            result={"status": "running"},
            error=None,
        )
        self.assertEqual(decode_envelope(encode_envelope(response)), response)

    def test_event_round_trip(self) -> None:
        event = EventEnvelope(
            protocol_version=1,
            project_id="project-a",
            cursor=3,
            event_type="task.stage",
            payload={"stage": "verify"},
        )
        self.assertEqual(decode_envelope(encode_envelope(event)), event)

    def test_unknown_protocol_version_is_rejected(self) -> None:
        payload = {
            "type": "request",
            "protocol_version": 99,
            "request_id": "req-1",
            "project_id": None,
            "method": "system.ping",
            "params": {},
        }
        with self.assertRaisesRegex(ProtocolError, "unsupported protocol_version"):
            decode_envelope(json.dumps(payload))

    def test_missing_request_id_is_rejected(self) -> None:
        payload = {
            "type": "request",
            "protocol_version": PROTOCOL_VERSION,
            "project_id": None,
            "method": "system.ping",
            "params": {},
        }
        with self.assertRaisesRegex(ProtocolError, "request_id"):
            decode_envelope(json.dumps(payload))

    def test_blank_request_id_is_rejected(self) -> None:
        request = RequestEnvelope(
            protocol_version=1,
            request_id="   ",
            project_id=None,
            method="system.ping",
            params={},
        )
        with self.assertRaisesRegex(ProtocolError, "request_id"):
            encode_envelope(request)

    def test_project_method_requires_project_id(self) -> None:
        payload = {
            "type": "request",
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "req-1",
            "project_id": None,
            "method": "project.status",
            "params": {},
        }
        with self.assertRaisesRegex(ProtocolError, "project_id"):
            decode_envelope(json.dumps(payload))

    def test_malformed_json_is_rejected(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "invalid JSON"):
            decode_envelope("not-json")

    def test_unknown_top_level_keys_are_rejected(self) -> None:
        payload = {
            "type": "request",
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "req-1",
            "project_id": None,
            "method": "system.ping",
            "params": {},
            "surprise": True,
        }
        with self.assertRaisesRegex(ProtocolError, "unknown fields"):
            decode_envelope(json.dumps(payload))

    def test_oversized_messages_are_rejected(self) -> None:
        huge = "x" * (1024 * 1024)
        with self.assertRaisesRegex(ProtocolError, "message too large"):
            decode_envelope(json.dumps({"type": "request", "blob": huge}))

    def test_event_cursor_must_be_non_negative(self) -> None:
        payload = {
            "type": "event",
            "protocol_version": PROTOCOL_VERSION,
            "project_id": "project-a",
            "cursor": -1,
            "event_type": "task.stage",
            "payload": {},
        }
        with self.assertRaisesRegex(ProtocolError, "cursor"):
            decode_envelope(json.dumps(payload))

    def test_event_cursors_must_increase_monotonically(self) -> None:
        events = [
            EventEnvelope(
                protocol_version=1,
                project_id="project-a",
                cursor=1,
                event_type="task.created",
                payload={},
            ),
            EventEnvelope(
                protocol_version=1,
                project_id="project-a",
                cursor=2,
                event_type="task.stage",
                payload={},
            ),
        ]
        assert_event_cursors_monotonic(events)

        out_of_order = [
            events[0],
            EventEnvelope(
                protocol_version=1,
                project_id="project-a",
                cursor=1,
                event_type="task.stage",
                payload={},
            ),
        ]
        with self.assertRaisesRegex(ProtocolError, "monotonic"):
            assert_event_cursors_monotonic(out_of_order)

    def test_event_cursors_are_checked_per_project(self) -> None:
        events = [
            EventEnvelope(
                protocol_version=1,
                project_id="project-a",
                cursor=2,
                event_type="task.created",
                payload={},
            ),
            EventEnvelope(
                protocol_version=1,
                project_id="project-b",
                cursor=1,
                event_type="task.created",
                payload={},
            ),
        ]
        assert_event_cursors_monotonic(events)


if __name__ == "__main__":
    unittest.main()