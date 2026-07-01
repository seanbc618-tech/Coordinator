"""Load declarative local extension manifests without executing plugin code."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .extension_manifest import ExtensionManifestError, load_manifest_file
from .runtime_paths import RuntimePaths

EXTENSION_STATUSES = frozenset({"enabled", "disabled", "invalid"})
_MANIFEST_SUFFIXES = (".json", ".toml", ".tml")


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _extension_id(name: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-")
    return f"ext-{slug or uuid.uuid4().hex[:8]}"


def _upsert_extension_record(
    conn: sqlite3.Connection,
    *,
    extension_id: str,
    name: str,
    version: str,
    manifest_path: str,
    status: str,
    capabilities: list[str],
    commit: bool = False,
) -> dict[str, Any]:
    now = _iso_now()
    existing = conn.execute(
        "select id from extension_manifests where manifest_path = ?",
        (manifest_path,),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            insert into extension_manifests(
                id, name, version, manifest_path, status, capabilities_json,
                created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                extension_id,
                name,
                version,
                manifest_path,
                status,
                json.dumps(capabilities, ensure_ascii=False),
                now,
                now,
            ),
        )
    else:
        extension_id = str(existing["id"])
        conn.execute(
            """
            update extension_manifests
            set name = ?, version = ?, status = ?, capabilities_json = ?, updated_at = ?
            where id = ?
            """,
            (
                name,
                version,
                status,
                json.dumps(capabilities, ensure_ascii=False),
                now,
                extension_id,
            ),
        )
    if commit:
        conn.commit()
    return {
        "id": extension_id,
        "name": name,
        "version": version,
        "manifest_path": manifest_path,
        "status": status,
        "capabilities": capabilities,
    }


def discover_manifest_paths(extensions_dir: Path) -> list[Path]:
    if not extensions_dir.is_dir():
        return []
    manifests: list[Path] = []
    for child in sorted(extensions_dir.iterdir()):
        if child.is_file() and child.suffix.lower() in _MANIFEST_SUFFIXES:
            manifests.append(child)
        elif child.is_dir():
            for candidate in sorted(child.iterdir()):
                if candidate.is_file() and candidate.suffix.lower() in _MANIFEST_SUFFIXES:
                    manifests.append(candidate)
    return manifests


def load_extensions(
    conn: sqlite3.Connection,
    paths: RuntimePaths,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    paths.extensions_dir.mkdir(parents=True, exist_ok=True)
    enabled: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []

    for manifest_path in discover_manifest_paths(paths.extensions_dir):
        try:
            validated = load_manifest_file(manifest_path)
        except (ExtensionManifestError, OSError, json.JSONDecodeError, ValueError) as exc:
            record = _upsert_extension_record(
                conn,
                extension_id=_extension_id(manifest_path.stem),
                name=manifest_path.stem,
                version="0.0.0",
                manifest_path=str(manifest_path),
                status="invalid",
                capabilities=[],
            )
            record["error"] = str(exc)
            invalid.append(record)
            continue

        record = _upsert_extension_record(
            conn,
            extension_id=_extension_id(validated["name"]),
            name=validated["name"],
            version=validated["version"],
            manifest_path=str(manifest_path),
            status="enabled",
            capabilities=validated["capabilities"],
        )
        record["description"] = validated["description"]
        record["slash_commands"] = validated["slash_commands"]
        record["agent_adapters"] = validated["agent_adapters"]
        enabled.append(record)

    if commit:
        conn.commit()

    return {
        "extensions_dir": str(paths.extensions_dir),
        "extensions": enabled,
        "enabled": enabled,
        "invalid": invalid,
        "count": len(enabled),
    }


def list_extensions(
    conn: sqlite3.Connection,
    paths: RuntimePaths,
    *,
    reload: bool = True,
) -> dict[str, Any]:
    if reload:
        return load_extensions(conn, paths, commit=True)

    rows = conn.execute(
        """
        select id, name, version, manifest_path, status, capabilities_json
        from extension_manifests
        order by name
        """
    ).fetchall()
    extensions = [
        {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "version": str(row["version"]),
            "manifest_path": str(row["manifest_path"]),
            "status": str(row["status"]),
            "capabilities": json.loads(str(row["capabilities_json"] or "[]")),
        }
        for row in rows
    ]
    return {
        "extensions_dir": str(paths.extensions_dir),
        "extensions": extensions,
        "count": len(extensions),
    }