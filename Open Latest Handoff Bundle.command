#!/bin/zsh
# Открывает в Finder самую свежую handoff-папку проекта.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

LATEST_DIR="$(python3 - <<'PY'
from pathlib import Path
items = sorted((p for p in Path('artifacts').glob('handoff_*') if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
print(items[0] if items else '')
PY
)"

if [[ -z "$LATEST_DIR" ]]; then
  echo "handoff bundle не найден"
  exit 1
fi

open "$LATEST_DIR"
