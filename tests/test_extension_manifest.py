"""Phase 20 extension manifest validation and loader tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.extension_loader import load_extensions
from local_cli_coordinator.extension_manifest import (
    ExtensionManifestError,
    load_manifest_file,
    validate_manifest,
)
from local_cli_coordinator.runtime_paths import RuntimePaths


class ExtensionManifestTests(unittest.TestCase):
    def test_valid_manifest_passes_validation(self) -> None:
        payload = validate_manifest(
            {
                "name": "demo-extension",
                "version": "1.0.0",
                "description": "Declarative demo",
                "slash_commands": [
                    {"name": "/demo", "description": "Demo command"},
                ],
                "agent_adapters": [
                    {
                        "id": "demo-agent",
                        "display_name": "Demo Agent",
                        "capabilities": ["code"],
                    }
                ],
            }
        )
        self.assertEqual(payload["name"], "demo-extension")
        self.assertEqual(payload["capabilities"], ["code"])

    def test_rejects_code_execution_fields(self) -> None:
        with self.assertRaises(ExtensionManifestError) as ctx:
            validate_manifest(
                {
                    "name": "bad",
                    "version": "1.0.0",
                    "agent_adapters": [
                        {"id": "x", "command": "python evil.py"},
                    ],
                }
            )
        self.assertIn("unsupported", str(ctx.exception).lower())

    def test_rejects_policy_bypass_keys(self) -> None:
        with self.assertRaises(ExtensionManifestError):
            validate_manifest(
                {
                    "name": "bad",
                    "version": "1.0.0",
                    "permissions": {"allow_push": True},
                }
            )

    def test_load_manifest_file_reads_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.json"
            path.write_text(
                json.dumps(
                    {
                        "name": "file-demo",
                        "version": "0.1.0",
                        "slash_commands": [],
                    }
                ),
                encoding="utf-8",
            )
            payload = load_manifest_file(path)
            self.assertEqual(payload["name"], "file-demo")


class ExtensionLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.paths = RuntimePaths(
            self.tmp / "config",
            self.tmp / "data",
            self.tmp / "state",
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_loader_registers_valid_manifest_without_execution(self) -> None:
        manifest = self.paths.extensions_dir / "demo.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "name": "safe-demo",
                    "version": "1.0.0",
                    "slash_commands": [
                        {"name": "/safe", "description": "Read-only metadata"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = load_extensions(self.conn, self.paths)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["enabled"][0]["name"], "safe-demo")
        row = self.conn.execute(
            "select status from extension_manifests where name = 'safe-demo'"
        ).fetchone()
        self.assertEqual(row["status"], "enabled")

    def test_loader_marks_invalid_manifest(self) -> None:
        manifest = self.paths.extensions_dir / "bad.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "name": "bad",
                    "version": "1.0.0",
                    "script": "import os",
                }
            ),
            encoding="utf-8",
        )
        result = load_extensions(self.conn, self.paths)
        self.assertEqual(result["count"], 0)
        self.assertEqual(len(result["invalid"]), 1)
        row = self.conn.execute(
            "select status from extension_manifests where manifest_path = ?",
            (str(manifest),),
        ).fetchone()
        self.assertEqual(row["status"], "invalid")


if __name__ == "__main__":
    unittest.main()