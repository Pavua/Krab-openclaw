#!/bin/zsh
# -----------------------------------------------------------------------------
# One-click статус Krab Ear backend (внешний проект Krab Ear).
# -----------------------------------------------------------------------------

set -euo pipefail

EAR_ROOT="/Users/pablito/Antigravity_AGENTS/Krab Ear"
PATTERN="KrabEarAgent --project-root $EAR_ROOT"

echo "📍 Krab Ear backend root: $EAR_ROOT"

if [ ! -d "$EAR_ROOT" ]; then
  echo "❌ Папка Krab Ear не найдена."
  exit 1
fi

pids="$(pgrep -f "$PATTERN" 2>/dev/null || true)"
if [ -z "$pids" ]; then
  echo "❌ Статус: DOWN (процесс не найден)"
else
  echo "✅ Статус: UP"
  echo "PID(s): $pids"
fi

echo
echo "ℹ️ Каноничные кнопки:"
echo "  - Старт backend: ./start_krab_ear_backend.command"
echo "  - Стоп backend:  ./stop_krab_ear_backend.command"
echo "  - FILE mode:     ./krab_ear.command (ручной транскрипт файла)"
