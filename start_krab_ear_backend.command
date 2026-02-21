#!/bin/zsh
# -----------------------------------------------------------------------------
# Каноничный one-click старт Krab Ear backend (внешний проект Krab Ear).
# -----------------------------------------------------------------------------

set -euo pipefail

EAR_ROOT="/Users/pablito/Antigravity_AGENTS/Krab Ear"
STARTER="$EAR_ROOT/Start Krab Ear.command"

if [ ! -x "$STARTER" ]; then
  echo "❌ Не найден стартовый файл Krab Ear: $STARTER"
  exit 1
fi

echo "🚀 Запускаю каноничный Krab Ear backend..."
exec "$STARTER"
