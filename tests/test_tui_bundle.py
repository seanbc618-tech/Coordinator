"""Tests for packaged TUI bundle lookup."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE_DATA_DIR = SRC / "local_cli_coordinator" / "tui_bundle"

REBUILD_COMMAND = "npm run build --prefix ui-tui && pip install --force-reinstall ."


class LocateTuiBundleTest(TestCase):
    def test_locate_returns_manifest_and_bundle_path(self) -> None:
        from local_cli_coordinator.tui_bundle import locate_tui_bundle

        located = locate_tui_bundle()
        self.assertEqual(located.manifest.protocol_version, 1)
        self.assertTrue(located.manifest.build_hash)
        self.assertEqual(located.manifest.bundle, "entry.js")

        with located.as_file() as bundle_path:
            self.assertTrue(bundle_path.is_file())
            content = bundle_path.read_bytes()
            expected = hashlib.sha256(content).hexdigest()[:16]
            self.assertEqual(located.manifest.build_hash, expected)

    def test_missing_manifest_raises_with_rebuild_command(self) -> None:
        from local_cli_coordinator import tui_bundle

        original = tui_bundle._bundle_root

        class MissingManifest:
            def joinpath(self, name: str) -> "MissingManifest":
                return self

            def read_text(self, encoding: str = "utf-8") -> str:
                raise FileNotFoundError("manifest.json")

            def read_bytes(self) -> bytes:
                raise FileNotFoundError(name)

        tui_bundle._bundle_root = lambda: MissingManifest()  # type: ignore[assignment]
        try:
            with self.assertRaises(tui_bundle.TuiBundleError) as ctx:
                tui_bundle.locate_tui_bundle()
            self.assertIn(REBUILD_COMMAND, str(ctx.exception))
        finally:
            tui_bundle._bundle_root = original

    def test_hash_mismatch_raises_with_rebuild_command(self) -> None:
        from local_cli_coordinator import tui_bundle

        original = tui_bundle._bundle_root
        manifest = {
            "protocol_version": 1,
            "build_hash": "deadbeefdeadbeef",
            "bundle": "entry.js",
            "source_map": "entry.js.map",
            "built_at": "2026-01-01T00:00:00.000Z",
        }

        class CorruptBundle:
            def __init__(self, name: str) -> None:
                self.name = name

            def joinpath(self, name: str) -> "CorruptBundle":
                return CorruptBundle(name)

            def read_text(self, encoding: str = "utf-8") -> str:
                if self.name == "manifest.json":
                    return json.dumps(manifest)
                raise FileNotFoundError(self.name)

            def read_bytes(self) -> bytes:
                if self.name == "entry.js":
                    return b"corrupt bundle"
                raise FileNotFoundError(self.name)

        tui_bundle._bundle_root = lambda: CorruptBundle("tui_bundle")  # type: ignore[assignment]
        try:
            with self.assertRaises(tui_bundle.TuiBundleError) as ctx:
                tui_bundle.locate_tui_bundle()
            self.assertIn(REBUILD_COMMAND, str(ctx.exception))
        finally:
            tui_bundle._bundle_root = original

    def test_unsupported_protocol_version_raises(self) -> None:
        from local_cli_coordinator import tui_bundle

        original = tui_bundle._bundle_root
        manifest = {
            "protocol_version": 99,
            "build_hash": "abc",
            "bundle": "entry.js",
            "source_map": "entry.js.map",
            "built_at": "2026-01-01T00:00:00.000Z",
        }

        class BadProtocol:
            def joinpath(self, name: str) -> "BadProtocol":
                return self

            def read_text(self, encoding: str = "utf-8") -> str:
                return json.dumps(manifest)

            def read_bytes(self) -> bytes:
                return b"ignored"

        tui_bundle._bundle_root = lambda: BadProtocol()  # type: ignore[assignment]
        try:
            with self.assertRaises(tui_bundle.TuiBundleError) as ctx:
                tui_bundle.locate_tui_bundle()
            self.assertIn(REBUILD_COMMAND, str(ctx.exception))
        finally:
            tui_bundle._bundle_root = original


class PackageDataTest(TestCase):
    def test_package_data_files_exist(self) -> None:
        required = [
            "entry.js",
            "manifest.json",
            "entry.js.map",
            "THIRD_PARTY_NOTICES.md",
            "source_map_policy.json",
        ]
        for name in required:
            path = PACKAGE_DATA_DIR / name
            self.assertTrue(path.exists(), f"Missing package data file: {path}")

    def test_manifest_matches_bundle_hash(self) -> None:
        manifest = json.loads((PACKAGE_DATA_DIR / "manifest.json").read_text())
        bundle_bytes = (PACKAGE_DATA_DIR / "entry.js").read_bytes()
        expected = hashlib.sha256(bundle_bytes).hexdigest()[:16]
        self.assertEqual(manifest["protocol_version"], 1)
        self.assertEqual(manifest["build_hash"], expected)

    def test_repeated_build_leaves_manifest_unchanged(self) -> None:
        if shutil.which("npm") is None:
            self.skipTest("npm not found in PATH")

        build_cmd = ["npm", "run", "build", "--prefix", str(ROOT / "ui-tui")]
        subprocess.run(build_cmd, cwd=ROOT, check=True)
        subprocess.run(build_cmd, cwd=ROOT, check=True)

        diff = subprocess.run(
            ["git", "diff", "--", "src/local_cli_coordinator/tui_bundle/manifest.json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(diff.stdout, "", diff.stdout or diff.stderr)


class WheelPackagingTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _build_wheel(self) -> Path:
        result = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(self.root)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        wheels = sorted(self.root.glob("*.whl"))
        self.assertEqual(len(wheels), 1, "Expected exactly one wheel")
        return wheels[0]

    def test_wheel_includes_bundle_without_dev_artifacts(self) -> None:
        wheel_path = self._build_wheel()
        with zipfile.ZipFile(wheel_path) as archive:
            names = archive.namelist()

        bundle_members = [name for name in names if "/tui_bundle/" in name]
        self.assertTrue(bundle_members, "Wheel must include tui_bundle package data")

        expected = {
            "local_cli_coordinator/tui_bundle/entry.js",
            "local_cli_coordinator/tui_bundle/manifest.json",
            "local_cli_coordinator/tui_bundle/entry.js.map",
            "local_cli_coordinator/tui_bundle/THIRD_PARTY_NOTICES.md",
            "local_cli_coordinator/tui_bundle/source_map_policy.json",
        }
        self.assertTrue(expected.issubset(set(names)))

        forbidden_fragments = (
            "node_modules",
            "__tests__",
            "hermes",
            ".tsx",
            ".ts",
        )
        for name in names:
            lowered = name.lower()
            for fragment in forbidden_fragments:
                self.assertNotIn(
                    fragment,
                    lowered,
                    f"Wheel must not include {fragment!r}: {name}",
                )

    def test_installed_wheel_locates_bundle_without_source_tree(self) -> None:
        wheel_path = self._build_wheel()
        venv_dir = self.root / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
        )
        python = venv_dir / "bin" / "python"
        pip = venv_dir / "bin" / "pip"
        subprocess.run(
            [str(pip), "install", "--force-reinstall", str(wheel_path)],
            check=True,
        )

        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        script = (
            "from local_cli_coordinator.tui_bundle import locate_tui_bundle\n"
            "located = locate_tui_bundle()\n"
            "assert located.manifest.protocol_version == 1\n"
            "with located.as_file() as path:\n"
            "    assert path.is_file()\n"
            "    print(located.manifest.build_hash)\n"
        )
        result = subprocess.run(
            [str(python), "-c", script],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(result.stdout.strip())