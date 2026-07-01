"""One-time local approval tokens: generate, hash, verify, consume, expire."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .approval_channels import (
    ApprovalRequest,
    get_approval_request,
    insert_approval_request,
    update_approval_request_status,
)

DEFAULT_TOKEN_TTL_HOURS = 24
TOKEN_PREFIX = "coord-appr-"


def generate_raw_token() -> str:
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(24)}"


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def token_hint(raw_token: str) -> str:
    return raw_token[-4:]


def is_approval_token(value: str) -> bool:
    text = value.strip()
    return text.startswith(TOKEN_PREFIX) and len(text) >= 20


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


def _lookup_by_hash(
    conn: sqlite3.Connection, *, token_hash: str
) -> ApprovalRequest | None:
    row = conn.execute(
        "select * from approval_requests where token_hash = ?", (token_hash,)
    ).fetchone()
    if row is None:
        return None
    from .approval_channels import _row_to_request

    return _row_to_request(row)


def verify_approval_token(
    conn: sqlite3.Connection, *, raw_token: str, project_id: str
) -> ApprovalRequest:
    request = _lookup_by_hash(conn, token_hash=hash_token(raw_token))
    if request is None:
        raise ValueError("unknown approval token")
    if request.project_id != project_id:
        raise ValueError("approval token project mismatch")
    if request.status != "pending":
        raise ValueError(f"approval token not pending: {request.status}")
    expires = datetime.fromisoformat(request.expires_at)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expires:
        update_approval_request_status(
            conn,
            request_id=request.id,
            status="expired",
            audit_event="expired",
            commit=True,
        )
        raise ValueError("approval token expired")
    return request


def consume_approval_token(
    conn: sqlite3.Connection,
    *,
    raw_token: str,
    project_id: str,
    decided_by: str = "",
    commit: bool = False,
) -> ApprovalRequest:
    request = verify_approval_token(
        conn, raw_token=raw_token, project_id=project_id
    )
    return update_approval_request_status(
        conn,
        request_id=request.id,
        status="consumed",
        decided_by=decided_by,
        audit_event="consumed",
        commit=commit,
    )


def expire_stale_approval_requests(
    conn: sqlite3.Connection, *, project_id: str | None = None, commit: bool = False
) -> list[str]:
    now = datetime.now(timezone.utc).isoformat()
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
    return _lookup_by_hash(conn, token_hash=hash_token(raw_token))