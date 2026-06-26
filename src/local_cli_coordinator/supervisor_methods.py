"""Multi-client Supervisor method registry.

Handles project-scoped requests: status, chat, pause/resume/stop,
event subscribe/replay. Delegates pause/stop state to the supervisor's
shared sets. Event subscribers receive live events via the shared broker.
"""

from __future__ import annotations

import dataclasses
import queue
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable
import sqlite3

from .commander_service import confirm_goal, create_and_preview_goal
from .config import CoordinatorConfig, RepoConfig
from .db import (
    project_get_task_detail,
    project_list_tasks,
    project_task_counts,
    task_latest_attempt,
    task_latest_event,
    task_list_artifacts_for_project,
)
from .goal_sessions import (
    GoalSessionError,
    fork_project_goal,
    format_goal_session_error,
    list_project_goal_candidates,
    resume_project_goal,
)
from .goals import active_goal_for_project, get_latest_commander_run
from .projects import ProjectDraft, get_project, inspect_project, register_project
from .runtime_paths import RuntimePaths
from .supervisor_commander import handle_chat_send
from .supervisor_events import EventBroker
from .supervisor_process import supervisor_log_path
from .supervisor_protocol import (
    PROTOCOL_VERSION,
    RequestEnvelope,
    ResponseEnvelope,
)

DEFAULT_ALLOW_PUSH = False
DEFAULT_MERGE_POLICY = "no_push"
DEFAULT_REVIEW_POLICY = "full_review"
DEFAULT_MAX_TASKS_PER_DAY = 24
DEFAULT_MAX_TASK_RUNTIME_SECONDS = 1800


def _match_repo_config(
    draft: ProjectDraft,
    config: CoordinatorConfig | None,
) -> RepoConfig | None:
    if config is None:
        return None
    canonical = draft.canonical_path.resolve()
    for repo in config.repos.values():
        if repo.path.resolve() == canonical:
            return repo
    return None


def resolve_inspect_policy(
    draft: ProjectDraft,
    *,
    config: CoordinatorConfig | None = None,
) -> dict[str, Any]:
    """Resolve effective onboarding policy for a repository inspection."""
    repo = _match_repo_config(draft, config)
    policy = config.policy if config is not None else None

    if repo is not None:
        return {
            "verify_commands": list(repo.verify_commands),
            "allow_push": repo.allow_push,
            "merge_policy": repo.merge_policy,
            "review_policy": repo.review_policy,
            "max_tasks_per_day": (
                policy.max_tasks_per_day
                if policy is not None
                else DEFAULT_MAX_TASKS_PER_DAY
            ),
            "max_task_runtime_seconds": (
                policy.max_task_runtime_seconds
                if policy is not None
                else DEFAULT_MAX_TASK_RUNTIME_SECONDS
            ),
        }

    return {
        "verify_commands": list(draft.verify_commands),
        "allow_push": DEFAULT_ALLOW_PUSH,
        "merge_policy": DEFAULT_MERGE_POLICY,
        "review_policy": DEFAULT_REVIEW_POLICY,
        "max_tasks_per_day": (
            policy.max_tasks_per_day
            if policy is not None
            else DEFAULT_MAX_TASKS_PER_DAY
        ),
        "max_task_runtime_seconds": (
            policy.max_task_runtime_seconds
            if policy is not None
            else DEFAULT_MAX_TASK_RUNTIME_SECONDS
        ),
    }


def _git_root_identity(repo_root: Path) -> str | None:
    """Return a stable identity for a repository based on its root commit."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--max-parents=0", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None


def _find_moved_project(
    conn: sqlite3.Connection,
    draft: ProjectDraft,
) -> sqlite3.Row | None:
    identity = _git_root_identity(draft.canonical_path)
    if identity is None:
        return None

    canonical = str(draft.canonical_path)
    for row in conn.execute("select id, canonical_path from projects"):
        if row["canonical_path"] == canonical:
            continue
        stored_identity = _git_root_identity(Path(row["canonical_path"]))
        if stored_identity == identity:
            return row
    return None


class SupervisorMethods:
    """Registry of Supervisor request handlers.

    The EventBroker must be shared with the supervisor loop so that
    events published during ticks are visible to subscribers.
    Pause/stop state is delegated to shared sets owned by the supervisor.
    """

    def __init__(
        self,
        broker: EventBroker | None = None,
        *,
        config: CoordinatorConfig | None = None,
        paths: RuntimePaths | None = None,
    ) -> None:
        self._broker = broker or EventBroker()
        self._config = config
        self._paths = paths
        self._paused: set[str] = set()
        self._stopped: set[str] = set()
        self._live_queues: dict[str, queue.Queue] = {}
        self._subscriptions: dict[str, int] = {}  # sub_id → broker token
        self._handlers: dict[str, Callable] = {
            "project.status": self._handle_project_status,
            "project.goal": self._handle_project_goal,
            "project.goals": self._handle_project_goals,
            "project.goal.resume": self._handle_project_goal_resume,
            "project.goal.fork": self._handle_project_goal_fork,
            "project.tasks": self._handle_project_tasks,
            "project.task": self._handle_project_task,
            "project.logs": self._handle_project_logs,
            "project.inspect": self._handle_project_inspect,
            "project.register": self._handle_project_register,
            "chat.send": self._handle_chat_send,
            "project.pause": self._handle_project_pause,
            "project.resume": self._handle_project_resume,
            "project.stop": self._handle_project_stop,
            "events.subscribe": self._handle_events_subscribe,
            "events.replay": self._handle_events_replay,
        }

    def set_paused_ref(self, paused: set[str]) -> None:
        """Set reference to supervisor's paused set."""
        self._paused = paused

    def set_stopped_ref(self, stopped: set[str]) -> None:
        """Set reference to supervisor's stopped set."""
        self._stopped = stopped

    @property
    def broker(self) -> EventBroker:
        return self._broker

    def handle(
        self,
        conn: sqlite3.Connection,
        request: RequestEnvelope,
    ) -> ResponseEnvelope:
        """Dispatch a request to the appropriate handler."""
        handler = self._handlers.get(request.method)
        if handler is None:
            return ResponseEnvelope(
                protocol_version=PROTOCOL_VERSION,
                request_id=request.request_id,
                ok=False,
                result=None,
                error=f"unsupported method {request.method!r}",
            )
        return handler(conn, request)

    def _inspect_result(
        self,
        draft: ProjectDraft,
        *,
        registered: bool = False,
        project_id: str | None = None,
        path_changed: bool = False,
        stored_canonical_path: str | None = None,
    ) -> dict[str, Any]:
        policy = resolve_inspect_policy(draft, config=self._config)
        result: dict[str, Any] = {
            "canonical_path": str(draft.canonical_path),
            "repo_id": draft.repo_id,
            "default_branch": draft.default_branch,
            "branch_prefix": draft.branch_prefix,
            "verify_commands": policy["verify_commands"],
            "allow_push": policy["allow_push"],
            "merge_policy": policy["merge_policy"],
            "review_policy": policy["review_policy"],
            "max_tasks_per_day": policy["max_tasks_per_day"],
            "max_task_runtime_seconds": policy["max_task_runtime_seconds"],
            "registered": registered,
            "path_changed": path_changed,
        }
        if project_id is not None:
            result["project_id"] = project_id
        if stored_canonical_path is not None:
            result["stored_canonical_path"] = stored_canonical_path
        return result

    def _handle_project_inspect(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        """Return an inspected project draft without writing to the registry."""
        path = request.params.get("path")
        if not isinstance(path, str) or not path.strip():
            return self._error(request, "path is required")

        try:
            draft = inspect_project(Path(path))
        except ValueError as exc:
            return self._error(request, str(exc))

        canonical = str(draft.canonical_path)
        row = conn.execute(
            "select id, canonical_path from projects where canonical_path = ?",
            (canonical,),
        ).fetchone()
        if row is not None:
            return self._ok(
                request,
                self._inspect_result(
                    draft,
                    registered=True,
                    project_id=row["id"],
                    path_changed=False,
                ),
            )

        moved = _find_moved_project(conn, draft)
        if moved is not None:
            return self._ok(
                request,
                self._inspect_result(
                    draft,
                    registered=False,
                    project_id=moved["id"],
                    path_changed=True,
                    stored_canonical_path=moved["canonical_path"],
                ),
            )

        return self._ok(request, self._inspect_result(draft))

    def _handle_project_register(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        """Register a project after explicit confirmation."""
        if not request.params.get("confirmed"):
            return self._error(request, "project registration requires confirmation")

        path = request.params.get("path")
        if not isinstance(path, str) or not path.strip():
            return self._error(request, "path is required")

        try:
            fresh = inspect_project(Path(path))
        except ValueError as exc:
            return self._error(request, str(exc))

        for field in ("canonical_path", "repo_id", "default_branch", "branch_prefix"):
            submitted = request.params.get(field)
            if submitted is None:
                return self._error(request, f"{field} is required")
            fresh_value = (
                str(fresh.canonical_path)
                if field == "canonical_path"
                else getattr(fresh, field)
            )
            if submitted != fresh_value:
                return self._error(request, f"draft field mismatch: {field}")

        submitted_verify = request.params.get("verify_commands", [])
        if not isinstance(submitted_verify, list):
            return self._error(request, "verify_commands must be a list")
        expected_verify = resolve_inspect_policy(fresh, config=self._config)["verify_commands"]
        if expected_verify != submitted_verify:
            return self._error(request, "draft field mismatch: verify_commands")

        effective = dataclasses.replace(
            fresh,
            verify_commands=tuple(str(cmd) for cmd in expected_verify),
        )

        existing = _find_moved_project(conn, fresh)
        if existing is not None:
            conn.execute(
                """
                update projects
                set canonical_path = ?, default_branch = ?, branch_prefix = ?,
                    verify_commands = ?, updated_at = current_timestamp
                where id = ?
                """,
                (
                    str(effective.canonical_path),
                    effective.default_branch,
                    effective.branch_prefix,
                    "\n".join(effective.verify_commands),
                    existing["id"],
                ),
            )
            conn.commit()
            return self._ok(request, {"project_id": existing["id"]})

        project_id = register_project(conn, effective, confirmed=True)
        return self._ok(request, {"project_id": project_id})

    def _handle_project_status(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        project_id = request.project_id
        if not project_id:
            return self._error(request, "project_id is required")
        if get_project(conn, project_id) is None:
            return self._error(request, f"project {project_id!r} not registered")

        counts = project_task_counts(conn, project_id=project_id)
        goal = active_goal_for_project(conn, project_id)
        goal_summary = None
        if goal is not None:
            goal_summary = {
                "id": goal["id"],
                "status": goal["status"],
                "title": goal["title"],
                "progress_summary": goal["progress_summary"],
            }
        return self._ok(
            request,
            {
                "counts": counts,
                "paused": project_id in self._paused,
                "stopped": project_id in self._stopped,
                "goal": goal_summary,
            },
        )

    def _handle_project_goals(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        project_id = request.project_id
        if not project_id:
            return self._error(request, "project_id is required")
        if get_project(conn, project_id) is None:
            return self._error(request, f"project {project_id!r} not registered")

        candidates = list_project_goal_candidates(conn, project_id)
        return self._ok(request, {"candidates": candidates})

    def _handle_project_goal_resume(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        project_id = request.project_id
        if not project_id:
            return self._error(request, "project_id is required")
        if get_project(conn, project_id) is None:
            return self._error(request, f"project {project_id!r} not registered")

        goal_id = request.params.get("goal_id")
        try:
            goal_id_int = int(goal_id)
        except (TypeError, ValueError):
            return self._error(request, "goal_id is required")

        try:
            goal = resume_project_goal(conn, project_id, goal_id_int)
        except GoalSessionError as exc:
            return self._error(request, format_goal_session_error(exc))

        return self._ok(
            request,
            {
                "goal_id": goal["id"],
                "status": goal["status"],
                "title": goal["title"],
            },
        )

    def _handle_project_goal_fork(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        project_id = request.project_id
        if not project_id:
            return self._error(request, "project_id is required")
        if get_project(conn, project_id) is None:
            return self._error(request, f"project {project_id!r} not registered")

        source_goal_id = request.params.get("goal_id")
        instruction = request.params.get("instruction", "")
        if not isinstance(instruction, str):
            instruction = ""
        try:
            source_goal_id_int = int(source_goal_id)
        except (TypeError, ValueError):
            return self._error(request, "goal_id is required")

        try:
            new_goal_id = fork_project_goal(
                conn,
                project_id,
                source_goal_id_int,
                instruction,
            )
        except GoalSessionError as exc:
            return self._error(request, format_goal_session_error(exc))

        return self._ok(request, {"goal_id": new_goal_id, "status": "draft"})

    def _handle_project_goal(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        project_id = request.project_id
        if not project_id:
            return self._error(request, "project_id is required")
        if self._config is None:
            return self._error(request, "coordinator config not loaded")

        project = get_project(conn, project_id)
        if project is None:
            return self._error(request, f"project {project_id!r} not registered")

        args = request.params.get("args", "")
        if not isinstance(args, str):
            args = ""
        objective = args.strip()
        project_root = Path(project["canonical_path"])

        if objective == "":
            goal = active_goal_for_project(conn, project_id)
            if goal is None:
                return self._ok(request, {"goal": None, "status": "no goal"})
            return self._ok(
                request,
                {
                    "goal": {
                        "id": goal["id"],
                        "status": goal["status"],
                        "title": goal["title"],
                        "objective": goal["objective"],
                        "progress_summary": goal["progress_summary"],
                    },
                    "status": goal["status"],
                },
            )

        if objective == "confirm":
            message = confirm_goal(
                conn,
                self._config,
                project_root,
                project_id=project_id,
            )
            if "activated" in message:
                return self._ok(request, {"message": message, "status": "active"})
            return self._error(request, message)

        preview = create_and_preview_goal(
            conn,
            self._config,
            project_root,
            objective,
            project_id=project_id,
        )
        if preview.error:
            return self._error(request, preview.error)
        return self._ok(
            request,
            {
                "goal_id": preview.goal_id,
                "status": "draft",
                "progress_summary": preview.progress_summary,
                "proposals": len(preview.proposals),
            },
        )

    def _handle_project_tasks(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        project_id = request.project_id
        if not project_id:
            return self._error(request, "project_id is required")
        if get_project(conn, project_id) is None:
            return self._error(request, f"project {project_id!r} not registered")

        tasks = []
        for row in project_list_tasks(conn, project_id=project_id)[:20]:
            latest = task_latest_event(conn, row["id"])
            tasks.append({
                "id": row["id"],
                "title": row["title"],
                "state": row["state"],
                "repo": row["repo"],
                "priority": row["priority"],
                "goal": row["goal"],
                "latest_note": latest["note"] if latest else None,
            })
        return self._ok(request, {"tasks": tasks})

    def _handle_project_task(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        project_id = request.project_id
        if not project_id:
            return self._error(request, "project_id is required")
        if get_project(conn, project_id) is None:
            return self._error(request, f"project {project_id!r} not registered")

        args = request.params.get("args", "")
        if not isinstance(args, str):
            args = ""
        task_id = args.strip()
        if not task_id:
            return self._error(request, "task id is required")

        row = project_get_task_detail(conn, project_id=project_id, task_id=task_id)
        if row is None:
            return self._error(
                request,
                f"task {task_id!r} not found in project {project_id!r}",
            )

        latest = task_latest_event(conn, task_id)
        attempt = task_latest_attempt(conn, task_id)
        artifacts = task_list_artifacts_for_project(
            conn, project_id=project_id, task_id=task_id
        )
        verification_commands = [
            line for line in row["verification_commands"].splitlines() if line
        ]
        return self._ok(
            request,
            {
                "task": {
                    "id": row["id"],
                    "title": row["title"],
                    "state": row["state"],
                    "repo": row["repo"],
                    "priority": row["priority"],
                    "capabilities": row["capabilities"],
                    "goal": row["goal"],
                    "acceptance_criteria": row["acceptance_criteria"],
                    "verification_commands": verification_commands,
                    "branch": row["branch"],
                    "worktree_path": row["worktree_path"],
                    "source_path": row["source_path"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                },
                "latest_event": (
                    {
                        "old_state": latest["old_state"],
                        "new_state": latest["new_state"],
                        "note": latest["note"],
                        "created_at": latest["created_at"],
                    }
                    if latest
                    else None
                ),
                "latest_attempt": (
                    {
                        "agent_id": attempt["agent_id"],
                        "exit_code": attempt["exit_code"],
                        "result_class": attempt["result_class"],
                        "result_reason": attempt["result_reason"],
                        "log_path": attempt["log_path"],
                        "completed_at": attempt["ended_at"],
                    }
                    if attempt
                    else None
                ),
                "artifacts": [
                    {"kind": art["kind"], "path": art["path"]} for art in artifacts
                ],
            },
        )

    def _handle_project_logs(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        project_id = request.project_id
        if not project_id:
            return self._error(request, "project_id is required")
        if self._paths is None:
            return self._error(request, "runtime paths not configured")
        if get_project(conn, project_id) is None:
            return self._error(request, f"project {project_id!r} not registered")

        log_path = supervisor_log_path(self._paths)
        log_tail = ""
        if log_path.is_file():
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]

        commander_run = None
        goal = active_goal_for_project(conn, project_id)
        if goal is not None:
            run = get_latest_commander_run(conn, goal["id"])
            if run is not None:
                commander_run = {
                    "id": run["id"],
                    "status": run["status"],
                    "trigger": run["trigger"],
                    "progress_summary": run["progress_summary"],
                    "error": run["error"],
                }

        return self._ok(
            request,
            {"log_tail": log_tail, "commander_run": commander_run},
        )

    def _handle_chat_send(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        project_id = request.project_id
        if not project_id:
            return self._error(request, "project_id is required")

        text = request.params.get("text", "")
        if not isinstance(text, str) or not text.strip():
            return self._error(request, "text is required")

        if self._config is None:
            return self._error(request, "coordinator config not loaded")

        project = get_project(conn, project_id)
        if project is None:
            return self._error(request, f"project {project_id!r} not registered")

        return handle_chat_send(
            conn,
            self._broker,
            self._config,
            Path(project["canonical_path"]),
            request,
            project_id=project_id,
            text=text.strip(),
        )

    def _handle_project_pause(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        self._paused.add(request.project_id)
        return self._ok(request, {"paused": True})

    def _handle_project_resume(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        self._paused.discard(request.project_id)
        return self._ok(request, {"paused": False})

    def _handle_project_stop(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        """Stop a project: add to stopped set, remove from paused."""
        self._stopped.add(request.project_id)
        self._paused.discard(request.project_id)
        return self._ok(request, {"stopped": True})

    def _handle_events_subscribe(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        """Subscribe to live events for a project.

        Registers a broker callback that pushes events to a queue.
        Returns the subscription ID and replayed events.
        """
        project_id = request.project_id
        after = request.params.get("after", 0)

        sub_id = str(uuid.uuid4())[:8]
        event_queue: queue.Queue = queue.Queue(maxsize=256)
        self._live_queues[sub_id] = event_queue

        # Register a real subscriber that pushes to the queue
        def _on_event(envelope):
            try:
                event_queue.put_nowait(envelope)
            except queue.Full:
                pass

        token = self._broker.subscribe(project_id, _on_event)
        self._subscriptions[sub_id] = token

        # Replay existing events
        events = self._broker.replay(conn, project_id, after=after)

        return self._ok(request, {
            "subscription_id": sub_id,
            "project_id": project_id,
            "replayed": [
                {"cursor": e.cursor, "type": e.event_type, "payload": e.payload}
                for e in events
            ],
        })

    def unsubscribe(self, sub_id: str) -> None:
        """Remove a subscription and clean up resources."""
        token = self._subscriptions.pop(sub_id, None)
        if token is not None:
            self._broker.unsubscribe(token)
        self._live_queues.pop(sub_id, None)

    def poll_live_events(self, sub_id: str) -> list[dict]:
        """Poll for live events from a subscription."""
        q = self._live_queues.get(sub_id)
        if q is None:
            return []
        events = []
        try:
            while True:
                env = q.get_nowait()
                events.append({
                    "project_id": env.project_id,
                    "cursor": env.cursor,
                    "type": env.event_type,
                    "payload": env.payload,
                })
        except queue.Empty:
            pass
        return events

    def _handle_events_replay(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        after = request.params.get("after", 0)
        limit = request.params.get("limit", 1000)
        events = self._broker.replay(
            conn, request.project_id, after=after, limit=limit
        )
        return self._ok(request, {
            "events": [
                {"cursor": e.cursor, "type": e.event_type, "payload": e.payload}
                for e in events
            ]
        })

    @staticmethod
    def _ok(request: RequestEnvelope, result: dict[str, Any]) -> ResponseEnvelope:
        return ResponseEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id=request.request_id,
            ok=True,
            result=result,
            error=None,
        )

    @staticmethod
    def _error(request: RequestEnvelope, error: str) -> ResponseEnvelope:
        return ResponseEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id=request.request_id,
            ok=False,
            result=None,
            error=error,
        )
