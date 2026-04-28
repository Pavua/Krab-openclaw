#!/bin/zsh
# Готовит текущую macOS-учётку к code-only/dev-admin Codex-сессии Krab.
# Это безопасный режим для USER2/USER3: runtime ownership и ~/.openclaw не трогаются.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "== Prepare Krab account session =="
echo "user=$(whoami)"
echo "repo=$ROOT_DIR"
echo

python3 "$ROOT_DIR/scripts/sync_codex_dev_layer.py" --profile dev-tools
echo
python3 "$ROOT_DIR/scripts/sync_codex_dev_layer.py" --check-only

echo
echo "Следующий шаг: открой Codex из этой учётки и выполни codex login, если auth_json отсутствует или устарел."
if [[ -t 0 ]]; then
  read -r "?Нажми Enter для выхода..."
fi
