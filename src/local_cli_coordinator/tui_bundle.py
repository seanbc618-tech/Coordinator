"""Locate and verify the packaged Coordinator TUI JavaScript bundle."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Generator

PROTOCOL_VERSION = 1
BUNDLE_DIR = "tui_bundle"
REBUILD_COMMAND = "npm run build --prefix ui-tui && pip install --force-reinstall ."


class TuiBundleError(RuntimeError):
    """Raised when the packaged TUI bundle is missing or corrupt."""


@dataclass(frozen=True)
class TuiBundleManifest:
    protocol_version: int
    build_hash: str
    bundle: str
    source_map: str | None
    built_at: str


@dataclass(frozen=True)
class LocatedTuiBundle:
    manifest: TuiBundleManifest

    @contextmanager
    def as_file(self) -> Generator[Path, None, None]:
        bundle_resource = _bundle_root().joinpath(self.manifest.bundle)
        with resources.as_file(bundle_resource) as bundle_path:
            yield Path(bundle_path)


def _bundle_root():
    return resources.files("local_cli_coordinator").joinpath(BUNDLE_DIR)


def _raise_bundle_error(message: str, exc: Exception | None = None) -> None:
    detail = f"{message} Rebuild and reinstall with: {REBUILD_COMMAND}"
    if exc is None:
        raise TuiBundleError(detail)
    raise TuiBundleError(detail) from exc


def _read_manifest() -> TuiBundleManifest:
    try:
        manifest_text = _bundle_root().joinpath("manifest.json").read_text(encoding="utf-8")
    except (FileNotFoundError, TypeError, OSError) as exc:
        _raise_bundle_error("TUI bundle manifest is missing.", exc)

    try:
        data = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        _raise_bundle_error("TUI bundle manifest is corrupt.", exc)

    try:
        return TuiBundleManifest(
            protocol_version=int(data["protocol_version"]),
            build_hash=str(data["build_hash"]),
            bundle=str(data["bundle"]),
            source_map=data.get("source_map"),
            built_at=str(data.get("built_at", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        _raise_bundle_error("TUI bundle manifest is corrupt.", exc)


def _verify_bundle(manifest: TuiBundleManifest) -> None:
    if manifest.protocol_version != PROTOCOL_VERSION:
        _raise_bundle_error(
            f"Unsupported TUI protocol version {manifest.protocol_version}."
        )

    try:
        bundle_bytes = _bundle_root().joinpath(manifest.bundle).read_bytes()
    except (FileNotFoundError, TypeError, OSError) as exc:
        _raise_bundle_error("TUI bundle artifact is missing.", exc)

    expected = hashlib.sha256(bundle_bytes).hexdigest()[:16]
    if expected != manifest.build_hash:
        _raise_bundle_error(
            "TUI bundle hash mismatch "
            f"(expected {manifest.build_hash}, got {expected})."
        )


def locate_tui_bundle() -> LocatedTuiBundle:
    """Return verified bundle metadata and an ``as_file`` path context manager."""
    manifest = _read_manifest()
    _verify_bundle(manifest)
    return LocatedTuiBundle(manifest=manifest)