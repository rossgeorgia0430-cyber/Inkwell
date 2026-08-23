#!/bin/bash
# 薄封装：本机 macOS 安装 Inkwell
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
  PY="$(command -v python3.12)"
else
  PY="${PYTHON:-python3}"
fi
exec "$PY" "$ROOT/scripts/install_macos.py"
