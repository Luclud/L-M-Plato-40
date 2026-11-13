#!/usr/bin/env bash
# Usage: scripts/unpack_data.sh path/to/Hackathon.zip
# Unpacks the organiser archive into data/quickstart and data/dev.
set -euo pipefail
ZIP="${1:?path to Hackathon.zip}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
unzip -q -o "$ZIP" -d "$TMP"
mkdir -p "$ROOT/data"
unzip -q -o "$TMP/PLATO40_v2_quickstart.zip" -d "$ROOT/data/_qs"
unzip -q -o "$TMP/PLATO40_v2_dev.zip" -d "$ROOT/data/_dev"
rm -rf "$ROOT/data/quickstart" "$ROOT/data/dev"
mv "$ROOT/data/_qs/PLATO40_v2_quickstart" "$ROOT/data/quickstart"
mv "$ROOT/data/_dev/PLATO40_v2_dev" "$ROOT/data/dev"
rmdir "$ROOT/data/_qs" "$ROOT/data/_dev"
cp "$TMP"/PLATO40_v2_holdout_encrypted.7z "$ROOT/data/" 2>/dev/null || true
rm -rf "$TMP"
echo "data/quickstart and data/dev ready"
