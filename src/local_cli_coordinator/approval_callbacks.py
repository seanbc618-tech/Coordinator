"""Approval callbacks: create requests, approve/reject tokens, route RPCs."""

from __future__ import annotations

import sqlite3
from typing import Any, TYPE_CHECKING

from .approval_channels import (
    ApprovalRequest,
    deliver_approval_request,
    update_approval_request_status,
)
from .approval_tokens import (
    claim_approval_token_for_action,
    create_approval_token,
    expire_stale_approval_requests,
    public_request_view,
    reject_approval_token_atomic,
)
from .operator_inbox import DESTRUCTIVE_METHODS, get_operator_item, upsert_operator_item

if TYPE_CHECKING:
    from .supervisor_methods import SupervisorMethods

POLICY_GATED_METHODS = frozenset({
    "project.task.cancel",
    "project.deliver",
    "project.pr.rebase",
    "project.merge",
})
MERGE_BLOCKED_BY_DEFAULT = frozenset({"project.merge"})

METHOD_PARAM_KEYS = {
    "project.task.approve": "task_id",
    "project.task.retry": "task_id",
    "project.task.cancel": "task_id",
    "project.deliver": "task_id",
    "project.pr.rebase": "delivery_id",
}


def requires_external_approval(action_method: str) -> bool:
    return action_method in DESTRUCTIVE_METHODS | POLICY_GATED_METHODS


def create_approval_from_operator_item(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    operator_item_id: str,
    deliver: bool = False,
    state_dir=None,
    policy=None,
    commit: bool = False,
) -> tuple[str, ApprovalRequest]:
    item = get_operator_item(
        conn, item_id=operator_item_id, project_id=project_id
    )
    if item is None:
        raise ValueError(f"operator item {operator_item_id!r} not found")
    if item.action_method is None:
        raise ValueError("operator item has no action_method")
    raw, request = create_approval_token(
        conn,
        project_id=project_id,
        operator_item_id=item.id,
        action_method=item.action_method,
        action_params=dict(item.action_params),
        commit=False,
    )
    if deliver and state_dir is not None and policy is not None:
        deliver_approval_request(
            conn,
            request_id=request.id,
            project_id=project_id,
            state_dir=state_dir,
            policy=policy,
            commit=False,
        )
    if commit:
        conn.commit()
    return raw, request


def route_approval_action(
    conn: sqlite3.Connection,
    *,
    request: ApprovalRequest,
    methods: SupervisorMethods,
) -> dict[str, Any]:
    if request.action_method in MERGE_BLOCKED_BY_DEFAULT:
        raise ValueError("merge approval blocked by policy")

    from .supervisor_protocol import PROTOCOL_VERSION, RequestEnvelope

    params = dict(request.action_params)
    if request.action_method == "project.pr.rebase":
        params.setdefault("apply", True)
        params.setdefault("confirmed", True)

    envelope = RequestEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id="approval-route",
        project_id=request.project_id,
        method=request.action_method,
        params=params,
    )
    response = methods.handle(conn, envelope)
    if not response.ok:
        update_approval_request_status(
            conn,
            request_id=request.id,
            status="failed",
            audit_event="failed",
            audit_data={"error": response.error},
            commit=False,
        )
        raise ValueError(response.error or "routed method failed")
    return {
        "routed_method": request.action_method,
        "result": response.result,
    }


def approve_approval_token(
    conn: sqlite3.Connection,
    *,
    raw_token: str,
    project_id: str,
    methods: SupervisorMethods,
    decided_by: str = "external",
    commit: bool = False,
) -> dict[str, Any]:
    expire_stale_approval_requests(conn, project_id=project_id, commit=False)
    request = claim_approval_token_for_action(
        conn,
        raw_token=raw_token,
        project_id=project_id,
        decided_by=decided_by,
    )
    routed = route_approval_action(conn, request=request, methods=methods)
    from .approval_channels import record_audit_event

    record_audit_event(
        conn,
        approval_request_id=request.id,
        project_id=project_id,
        event_type="approved",
        data={"routed_method": request.action_method},
        commit=False,
    )
    updated = request
    return {
        "status": updated.status,
        "routed": True,
        "routed_method": routed["routed_method"],
        "result": routed["result"],
        "request": public_request_view(updated),
    }


def reject_approval_token(
    conn: sqlite3.Connection,
    *,
    raw_token: str,
    project_id: str,
    decided_by: str = "external",
    commit: bool = False,
) -> dict[str, Any]:
    updated = reject_approval_token_atomic(
        conn,
        raw_token=raw_token,
        project_id=project_id,
        decided_by=decided_by,
        commit=commit,
    )
    return {
        "status": updated.status,
        "request": public_request_view(updated),
    }


def maybe_create_operator_approval(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    operator_item_id: str,
    commit: bool = False,
) -> tuple[str, ApprovalRequest] | None:
    item = get_operator_item(
        conn, item_id=operator_item_id, project_id=project_id
    )
    if item is None or item.action_method is None:
        return None
    if not requires_external_approval(item.action_method):
        return None
    return create_approval_from_operator_item(
        conn,
        project_id=project_id,
        operator_item_id=operator_item_id,
        commit=commit,
    )


def surface_expired_operator_items(
    conn: sqlite3.Connection, *, project_id: str, commit: bool = False
) -> list[str]:
    expired = expire_stale_approval_requests(
        conn, project_id=project_id, commit=False
    )
    for request_id in expired:
        row = conn.execute(
            "select operator_item_id from approval_requests where id = ?",
            (request_id,),
        ).fetchone()
        if row is None or not row["operator_item_id"]:
            continue
        upsert_operator_item(
            conn,
            project_id=project_id,
            source_type="supervisor",
            source_id=request_id,
            severity="warning",
            title="Approval expired",
            summary="External approval token expired; action not executed.",
            dedupe_key=f"approval-expired:{request_id}",
            action_label="Review",
            action_method=None,
            commit=False,
        )
    if commit:
        conn.commit()
    return expired