"""Project-scoped @file context parsing, validation, and manifest helpers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

MAX_CONTEXT_FILES = 16
MAX_CONTEXT_FILE_BYTES = 128 * 1024
MAX_CONTEXT_TOTAL_BYTES = 512 * 1024

CONTEXT_ERROR_CODES = frozenset(
    {
        "context_missing",
        "context_outside_repo",
        "context_binary",
        "context_too_large",
    }
)


class ContextFileError(Exception):
    """Raised when a referenced context file fails validation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ContextFile:
    path: str
    size: int
    sha256: str
    content: str


def _repo_relative_path(repo_root: Path, resolved: Path) -> str:
    return resolved.relative_to(repo_root.resolve()).as_posix()


def _manifest_entry(context_file: ContextFile) -> dict[str, object]:
    return {
        "path": context_file.path,
        "size": context_file.size,
        "sha256": context_file.sha256,
        "content_type": "text/plain",
    }


def manifest_from_context_files(
    context_files: list[ContextFile],
) -> list[dict[str, object]]:
    return [_manifest_entry(item) for item in context_files]


def public_metadata_from_context_files(
    context_files: list[ContextFile],
) -> list[dict[str, object]]:
    return [
        {
            "path": item.path,
            "size": item.size,
            "sha256": item.sha256,
        }
        for item in context_files
    ]


def format_user_message(text: str, context_files: list[ContextFile]) -> str:
    if not context_files:
        return text
    paths = ", ".join(item.path for item in context_files)
    return f"{text}\n\n[context files: {paths}]"


def append_file_context_to_prompt(base_prompt: str, context_files: list[ContextFile]) -> str:
    if not context_files:
        return base_prompt
    sections = [base_prompt.rstrip(), "", "## Operator file context"]
    for item in context_files:
        sections.extend(
            [
                f"--- BEGIN FILE: {item.path} sha256={item.sha256} ---",
                item.content,
                f"--- END FILE: {item.path} ---",
            ]
        )
    return "\n".join(sections)


def render_redacted_prompt(base_prompt: str, context_files: list[ContextFile]) -> str:
    if not context_files:
        return base_prompt
    import json

    manifest = manifest_from_context_files(context_files)
    return (
        f"{base_prompt.rstrip()}\n\n"
        "## Context manifest\n"
        f"{json.dumps(manifest, indent=2)}"
    )


def load_context_files(
    repo_root: Path,
    cwd: Path,
    tokens: list[str],
) -> list[ContextFile]:
    """Resolve and validate referenced files within a registered repository."""
    if len(tokens) > MAX_CONTEXT_FILES:
        raise ContextFileError(
            "context_too_large",
            f"context file count exceeds limit of {MAX_CONTEXT_FILES}",
        )

    repo_root_resolved = repo_root.resolve()
    seen: set[Path] = set()
    loaded: list[ContextFile] = []
    total_bytes = 0

    for token in tokens:
        raw_path = cwd / token
        try:
            candidate = raw_path.resolve(strict=True)
        except FileNotFoundError as exc:
            unresolved = raw_path.resolve(strict=False)
            if not unresolved.is_relative_to(repo_root_resolved):
                raise ContextFileError(
                    "context_outside_repo",
                    f"context file resolves outside repository: {token}",
                ) from exc
            raise ContextFileError(
                "context_missing",
                f"context file not found: {token}",
            ) from exc

        if not candidate.is_relative_to(repo_root_resolved):
            raise ContextFileError(
                "context_outside_repo",
                f"context file resolves outside repository: {token}",
            )
        if candidate in seen:
            continue
        seen.add(candidate)

        if not candidate.is_file():
            if candidate.exists():
                raise ContextFileError(
                    "context_missing",
                    f"context path is not a file: {token}",
                )
            raise ContextFileError(
                "context_missing",
                f"context file not found: {token}",
            )

        raw = candidate.read_bytes()
        if b"\x00" in raw:
            raise ContextFileError(
                "context_binary",
                f"context file contains binary data: {token}",
            )
        if len(raw) > MAX_CONTEXT_FILE_BYTES:
            raise ContextFileError(
                "context_too_large",
                f"context file exceeds {MAX_CONTEXT_FILE_BYTES // 1024} KiB limit: {token}",
            )
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContextFileError(
                "context_binary",
                f"context file is not valid UTF-8: {token}",
            ) from exc

        total_bytes += len(raw)
        if total_bytes >= MAX_CONTEXT_TOTAL_BYTES:
            raise ContextFileError(
                "context_too_large",
                f"context files exceed {MAX_CONTEXT_TOTAL_BYTES // 1024} KiB aggregate limit",
            )

        loaded.append(
            ContextFile(
                path=_repo_relative_path(repo_root_resolved, candidate),
                size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                content=content,
            )
        )

    return loaded


def format_context_error(exc: ContextFileError) -> str:
    return f"{exc.code}: {exc}"


def parse_context_error_message(error: str | None) -> tuple[str, str]:
    message = error or "supervisor request failed"
    if ":" in message:
        code, _, remainder = message.partition(":")
        code = code.strip()
        if code in CONTEXT_ERROR_CODES:
            return code, remainder.strip() or message
    return "supervisor_error", message


def load_context_files_from_params(
    repo_root: Path,
    params: list[dict[str, object]],
) -> list[ContextFile]:
    """Validate client-supplied context file references at the Supervisor."""
    tokens: list[str] = []
    for entry in params:
        path = entry.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ContextFileError(
                "context_missing",
                "context file path is required",
            )
        tokens.append(path)
    return load_context_files(repo_root, repo_root, tokens)