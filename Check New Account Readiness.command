#!/bin/zsh
# Проверяет, готова ли текущая macOS-учётка к параллельной Codex-разработке Krab.
# Проверка не требует секретов и не меняет файлы.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "== Krab account readiness =="
python3 "$ROOT_DIR/scripts/sync_codex_dev_layer.py" --check-only

echo
echo "-- Git branch/status --"
git status --short --branch

echo
echo "-- Codex MCP list --"
if command -v codex >/dev/null 2>&1; then
  codex mcp list || true
else
  echo "codex CLI не найден в PATH"
fi

if [[ -t 0 ]]; then
  read -r "?Нажми Enter для выхода..."
fi
