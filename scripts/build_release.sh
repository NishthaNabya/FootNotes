#!/bin/sh
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/.venv/bin/python" "$ROOT/scripts/package_extension.py"
"$ROOT/.venv/bin/python" "$ROOT/scripts/build_macos_release.py"
