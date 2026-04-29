#!/bin/zsh
# Проверяет ветку, dirty tree и доступность shared repo перед параллельной работой.
# Нужен, чтобы USER2/USER3 не начинали работу на старом или конфликтующем checkout.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "== Krab repo drift check =="
echo "user=$(whoami)"
echo "repo=$ROOT_DIR"
echo

git status --short --branch
echo
git log --oneline -5

echo
echo "-- Shared repo candidate --"
if [[ -d "/Users/Shared/Antigravity_AGENTS/Краб/.git" ]]; then
  git -C "/Users/Shared/Antigravity_AGENTS/Краб" status --short --branch | head -80
else
  echo "/Users/Shared/Antigravity_AGENTS/Краб не является git checkout"
fi

if [[ -t 0 ]]; then
  read -r "?Нажми Enter для выхода..."
fi
