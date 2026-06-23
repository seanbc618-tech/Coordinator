"""Ensure root migrations/ mirror stays byte-identical to package authority."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "src" / "local_cli_coordinator" / "migrations"
MIRROR = ROOT / "migrations"


class MigrationMirrorSyncTests(unittest.TestCase):
    def test_migration_mirror_matches_authoritative(self) -> None:
        auth = {p.name: p.read_bytes() for p in AUTH.glob("*.sql")}
        mir = {p.name: p.read_bytes() for p in MIRROR.glob("*.sql")}
        self.assertEqual(set(auth), set(mir))
        for name, body in auth.items():
            self.assertEqual(body, mir[name], name)


if __name__ == "__main__":
    unittest.main()