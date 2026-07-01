"""One-time local approval tokens: generate, hash, verify, consume, expire."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .approval_channels import (
    ApprovalRequest,
    get_approval_request,
    insert_approval_request,
    record_audit_event,
    update_approval_request_status,
)

DEFAULT_TOKEN_TTL_HOURS = 24
TOKEN_PREFIX = "coord-appr-"
_DUMMY_TOKEN_HASH = hashlib.sha256(b"coord-appr-constant-time-dummy").hexdigest()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_raw_token() -> str:
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(24)}"


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def token_hint(raw_token: str) -> str:
    return raw_token[-4:]


def is_approval_token(value: str) -> bool:
    text = value.strip()
    return text.startswith(TOKEN_PREFIX) and len(text) >= 20


def _token_hash_matches(stored_hash: str, raw_token: str) -> bool:
    return hmac.compare_digest(hash_token(raw_token), stored_hash)


def _default_expiry() -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=DEFAULT_TOKEN_TTL_HOURS)
    ).isoformat()


def create_approval_token(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    action_method: str,
    action_params: Mapping[str, Any],
    operator_item_id: str | None = None,
    expires_at: str | None = None,
    commit: bool = False,
) -> tuple[str, ApprovalRequest]:
    raw = generate_raw_token()
    request = insert_approval_request(
        conn,
        project_id=project_id,
        operator_item_id=operator_item_id,
        action_method=action_method,
        action_params=dict(action_params),
        token_hash=hash_token(raw),
        token_hint=token_hint(raw),
        expires_at=expires_at or _default_expiry(),
        commit=commit,
    )
    return raw, request


def _row_to_request(row: sqlite3.Row) -> ApprovalRequest:
    from .approval_channels import _row_to_request

    return _row_to_request(row)


def _lookup_by_presented_token(
    conn: sqlite3.Connection,
    *,
    raw_token: str,
    project_id: str | None = None,
) -> ApprovalRequest | None:
    computed = hash_token(raw_token)
    if project_id is None:
        row = conn.execute(
            "select * from approval_requests where token_hash = ?",
            (computed,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            select * from approval_requests
            where token_hash = ? and project_id = ?
            """,
            (computed, project_id),
        ).fetchone()
    stored_hash = str(row["token_hash"]) if row is not None else _DUMMY_TOKEN_HASH
    if not hmac.compare_digest(computed, stored_hash):
        return None
    if row is None:
        return None
    return _row_to_request(row)


def _maybe_expire_request(
    conn: sqlite3.Connection, request: ApprovalRequest
) -> ApprovalRequest:
    expires = datetime.fromisoformat(request.expires_at)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expires and request.status == "pending":
        return update_approval_request_status(
            conn,
            request_id=request.id,
            status="expired",
            audit_event="expired",
            commit=False,
        )
    return request


def verify_approval_token(
    conn: sqlite3.Connection, *, raw_token: str, project_id: str
) -> ApprovalRequest:
    request = _lookup_by_presented_token(
        conn, raw_token=raw_token, project_id=project_id
    )
    if request is None:
        raise ValueError("unknown approval token")
    request = _maybe_expire_request(conn, request)
    if request.status != "pending":
        raise ValueError(f"approval token not pending: {request.status}")
    return request


def _atomic_transition_token(
    conn: sqlite3.Connection,
    *,
    raw_token: str,
    project_id: str,
    new_status: str,
    decided_by: str = "",
    audit_event: str | None = None,
    audit_data: Mapping[str, Any] | None = None,
) -> ApprovalRequest:
    computed = hash_token(raw_token)
    probe = conn.execute(
        """
        select token_hash from approval_requests
        where token_hash = ? and project_id = ?
        """,
        (computed, project_id),
    ).fetchone()
    stored_hash = str(probe["token_hash"]) if probe is not None else _DUMMY_TOKEN_HASH
    if not hmac.compare_digest(computed, stored_hash):
        raise ValueError("unknown approval token")

    now = _iso_now()
    cursor = conn.execute(
        """
        update approval_requests
        set status = ?, decided_at = ?, decided_by = ?
        where token_hash = ? and project_id = ? and status = 'pending'
          and expires_at > ?
        """,
        (new_status, now, decided_by, computed, project_id, now),
    )
    if cursor.rowcount != 1:
        request = _lookup_by_presented_token(
            conn, raw_token=raw_token, project_id=project_id
        )
        if request is None:
            raise ValueError("unknown approval token")
        request = _maybe_expire_request(conn, request)
        if request.status != "pending":
            raise ValueError(f"approval token not pending: {request.status}")
        raise ValueError("approval token consume race or unavailable")

    row = conn.execute(
        """
        select * from approval_requests
        where token_hash = ? and project_id = ?
        """,
        (computed, project_id),
    ).fetchone()
    assert row is not None
    request = _row_to_request(row)
    if audit_event:
        record_audit_event(
            conn,
            approval_request_id=request.id,
            project_id=project_id,
            event_type=audit_event,
            data=dict(audit_data or {}),
            commit=False,
        )
    return request


def consume_approval_token(
    conn: sqlite3.Connection,
    *,
    raw_token: str,
    project_id: str,
    decided_by: str = "",
    commit: bool = False,
) -> ApprovalRequest:
    request = _atomic_transition_token(
        conn,
        raw_token=raw_token,
        project_id=project_id,
        new_status="consumed",
        decided_by=decided_by,
        audit_event="consumed",
    )
    if commit:
        conn.commit()
    return request


def reject_approval_token_atomic(
    conn: sqlite3.Connection,
    *,
    raw_token: str,
    project_id: str,
    decided_by: str = "",
    commit: bool = False,
) -> ApprovalRequest:
    request = _atomic_transition_token(
        conn,
        raw_token=raw_token,
        project_id=project_id,
        new_status="rejected",
        decided_by=decided_by,
        audit_event="rejected",
    )
    if commit:
        conn.commit()
    return request


def claim_approval_token_for_action(
    conn: sqlite3.Connection,
    *,
    raw_token: str,
    project_id: str,
    decided_by: str = "",
) -> ApprovalRequest:
    """Atomically claim a pending token before routing an approval action."""
    return _atomic_transition_token(
        conn,
        raw_token=raw_token,
        project_id=project_id,
        new_status="consumed",
        decided_by=decided_by,
        audit_event=None,
    )


def expire_stale_approval_requests(
    conn: sqlite3.Connection, *, project_id: str | None = None, commit: bool = False
) -> list[str]:
    now = _iso_now()
    if project_id is None:
        rows = conn.execute(
            """
            select id, project_id from approval_requests
            where status = 'pending' and expires_at < ?
            """,
            (now,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select id, project_id from approval_requests
            where project_id = ? and status = 'pending' and expires_at < ?
            """,
            (project_id, now),
        ).fetchall()
    expired_ids: list[str] = []
    for row in rows:
        update_approval_request_status(
            conn,
            request_id=str(row["id"]),
            status="expired",
            audit_event="expired",
            commit=False,
        )
        expired_ids.append(str(row["id"]))
    if commit:
        conn.commit()
    return expired_ids


def public_request_view(request: ApprovalRequest) -> dict[str, Any]:
    return {
        "id": request.id,
        "project_id": request.project_id,
        "operator_item_id": request.operator_item_id,
        "action_method": request.action_method,
        "action_params": request.action_params,
        "status": request.status,
        "token_hint": request.token_hint,
        "expires_at": request.expires_at,
        "created_at": request.created_at,
        "decided_at": request.decided_at,
        "decided_by": request.decided_by,
    }


def get_approval_request_by_token(
    conn: sqlite3.Connection, *, raw_token: str
) -> ApprovalRequest | None:
    return _lookup_by_presented_token(conn, raw_token=raw_token)