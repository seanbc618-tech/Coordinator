from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1024 * 1024

_REQUEST_FIELDS = frozenset({
    "type",
    "protocol_version",
    "request_id",
    "project_id",
    "method",
    "params",
})

_RESPONSE_FIELDS = frozenset({
    "type",
    "protocol_version",
    "request_id",
    "ok",
    "result",
    "error",
})

_EVENT_FIELDS = frozenset({
    "type",
    "protocol_version",
    "project_id",
    "cursor",
    "event_type",
    "payload",
})

_PROJECT_METHOD_PREFIXES = ("project.", "events.", "chat.")


class ProtocolError(ValueError):
    """Raised when a Supervisor protocol envelope is invalid."""


@dataclass(frozen=True)
class RequestEnvelope:
    protocol_version: int
    request_id: str
    project_id: str | None
    method: str
    params: dict[str, Any]
    type: str = "request"


@dataclass(frozen=True)
class ResponseEnvelope:
    protocol_version: int
    request_id: str
    ok: bool
    result: dict[str, Any] | None
    error: str | None
    type: str = "response"


@dataclass(frozen=True)
class EventEnvelope:
    protocol_version: int
    project_id: str
    cursor: int
    event_type: str
    payload: dict[str, Any]
    type: str = "event"


Envelope = RequestEnvelope | ResponseEnvelope | EventEnvelope


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{label} must be an object")
    return value


def _require_non_blank_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{field} must be a non-blank string")
    return value.strip()


def _require_protocol_version(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProtocolError("protocol_version must be an integer")
    if value != PROTOCOL_VERSION:
        raise ProtocolError(
            f"unsupported protocol_version {value!r}; expected {PROTOCOL_VERSION}"
        )
    return value


def _require_params(value: object) -> dict[str, Any]:
    params = _require_mapping(value, "params")
    return params


def _method_requires_project_id(method: str) -> bool:
    return method.startswith(_PROJECT_METHOD_PREFIXES)


def _validate_request_fields(
    data: dict[str, Any],
    *,
    fields: frozenset[str],
    label: str,
) -> None:
    unknown = set(data) - fields
    if unknown:
        raise ProtocolError(
            f"{label} has unknown fields: {', '.join(sorted(unknown))}"
        )
    missing = fields - set(data)
    if missing:
        raise ProtocolError(
            f"{label} missing required fields: {', '.join(sorted(missing))}"
        )


def _validate_request(request: RequestEnvelope) -> None:
    method = _require_non_blank_string(request.method, "method")
    _require_non_blank_string(request.request_id, "request_id")
    _require_protocol_version(request.protocol_version)
    if request.type != "request":
        raise ProtocolError("request type must be 'request'")
    if not isinstance(request.params, dict):
        raise ProtocolError("params must be an object")
    if _method_requires_project_id(method) and request.project_id is None:
        raise ProtocolError(f"project_id is required for method {method!r}")
    if request.project_id is not None and not request.project_id.strip():
        raise ProtocolError("project_id must be a non-blank string when provided")


def _validate_response(response: ResponseEnvelope) -> None:
    _require_non_blank_string(response.request_id, "request_id")
    _require_protocol_version(response.protocol_version)
    if response.type != "response":
        raise ProtocolError("response type must be 'response'")
    if not isinstance(response.ok, bool):
        raise ProtocolError("ok must be a boolean")
    if response.ok:
        if response.result is None or not isinstance(response.result, dict):
            raise ProtocolError("result must be an object for successful responses")
        if response.error is not None:
            raise ProtocolError("error must be null for successful responses")
    else:
        if not isinstance(response.error, str) or not response.error.strip():
            raise ProtocolError("error must be a non-blank string for failed responses")


def _validate_event(event: EventEnvelope) -> None:
    _require_non_blank_string(event.project_id, "project_id")
    _require_non_blank_string(event.event_type, "event_type")
    _require_protocol_version(event.protocol_version)
    if event.type != "event":
        raise ProtocolError("event type must be 'event'")
    if not isinstance(event.cursor, int) or isinstance(event.cursor, bool):
        raise ProtocolError("cursor must be an integer")
    if event.cursor < 0:
        raise ProtocolError("cursor must be non-negative")
    if not isinstance(event.payload, dict):
        raise ProtocolError("payload must be an object")


def encode_envelope(envelope: Envelope) -> str:
    if isinstance(envelope, RequestEnvelope):
        _validate_request(envelope)
        payload = {
            "type": envelope.type,
            "protocol_version": envelope.protocol_version,
            "request_id": envelope.request_id,
            "project_id": envelope.project_id,
            "method": envelope.method,
            "params": envelope.params,
        }
    elif isinstance(envelope, ResponseEnvelope):
        _validate_response(envelope)
        payload = {
            "type": envelope.type,
            "protocol_version": envelope.protocol_version,
            "request_id": envelope.request_id,
            "ok": envelope.ok,
            "result": envelope.result,
            "error": envelope.error,
        }
    else:
        _validate_event(envelope)
        payload = {
            "type": envelope.type,
            "protocol_version": envelope.protocol_version,
            "project_id": envelope.project_id,
            "cursor": envelope.cursor,
            "event_type": envelope.event_type,
            "payload": envelope.payload,
        }

    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message too large")
    return encoded


def decode_envelope(raw: str) -> Envelope:
    if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message too large")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError("invalid JSON") from exc

    data = _require_mapping(payload, "envelope")
    envelope_type = data.get("type")
    if envelope_type == "request":
        _validate_request_fields(data, fields=_REQUEST_FIELDS, label="request")
        request = RequestEnvelope(
            protocol_version=_require_protocol_version(data["protocol_version"]),
            request_id=_require_non_blank_string(data["request_id"], "request_id"),
            project_id=data["project_id"],
            method=_require_non_blank_string(data["method"], "method"),
            params=_require_params(data["params"]),
            type="request",
        )
        _validate_request(request)
        return request

    if envelope_type == "response":
        _validate_request_fields(data, fields=_RESPONSE_FIELDS, label="response")
        response = ResponseEnvelope(
            protocol_version=_require_protocol_version(data["protocol_version"]),
            request_id=_require_non_blank_string(data["request_id"], "request_id"),
            ok=data["ok"],
            result=data["result"],
            error=data["error"],
            type="response",
        )
        _validate_response(response)
        return response

    if envelope_type == "event":
        _validate_request_fields(data, fields=_EVENT_FIELDS, label="event")
        cursor = data["cursor"]
        if not isinstance(cursor, int) or isinstance(cursor, bool):
            raise ProtocolError("cursor must be an integer")
        event = EventEnvelope(
            protocol_version=_require_protocol_version(data["protocol_version"]),
            project_id=_require_non_blank_string(data["project_id"], "project_id"),
            cursor=cursor,
            event_type=_require_non_blank_string(data["event_type"], "event_type"),
            payload=_require_mapping(data["payload"], "payload"),
            type="event",
        )
        _validate_event(event)
        return event

    raise ProtocolError(f"unsupported envelope type {envelope_type!r}")


def assert_event_cursors_monotonic(events: list[EventEnvelope]) -> None:
    last_by_project: dict[str, int] = {}
    for event in events:
        previous = last_by_project.get(event.project_id)
        if previous is not None and event.cursor <= previous:
            raise ProtocolError(
                "event cursors must increase monotonically per project"
            )
        last_by_project[event.project_id] = event.cursor


def assert_event_seq_monotonic(events: list[dict[str, Any]]) -> None:
    """Validate schema-v2 ``seq`` values increase per project."""
    last_by_project: dict[str, int] = {}
    for event in events:
        project_id = event.get("project_id")
        seq = event.get("seq")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ProtocolError("project_id must be a non-blank string")
        if not isinstance(seq, int) or isinstance(seq, bool):
            raise ProtocolError("seq must be an integer")
        previous = last_by_project.get(project_id)
        if previous is not None and seq <= previous:
            raise ProtocolError(
                "event seq must increase monotonically per project"
            )
        last_by_project[project_id] = seq