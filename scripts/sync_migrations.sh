#!/usr/bin/env bash
# Copy authoritative package migrations to the repo-root mirror.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AUTH="$ROOT/src/local_cli_coordinator/migrations"
MIRROR="$ROOT/migrations"
cp "$AUTH"/*.sql "$MIRROR"/
echo "Synced $AUTH -> $MIRROR"