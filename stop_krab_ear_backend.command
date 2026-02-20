#!/bin/zsh
# -----------------------------------------------------------------------------
# One-click остановка Krab Ear backend (внешний проект Krab Ear).
# -----------------------------------------------------------------------------

set -euo pipefail

EAR_ROOT="/Users/pablito/Antigravity_AGENTS/Krab Ear"
PATTERN="KrabEarAgent --project-root $EAR_ROOT"
VENV_PY="$EAR_ROOT/.venv_krab_ear/bin/python"

find_pids() {
  pgrep -f "$PATTERN" 2>/dev/null || true
}

pids="$(find_pids)"
if [ -z "$pids" ]; then
  echo "ℹ️ Krab Ear backend не запущен."
  exit 0
fi

echo "🛑 Останавливаю Krab Ear backend..."

# Мягкая остановка через control notification (если доступно окружение PyObjC).
if [ -x "$VENV_PY" ]; then
  "$VENV_PY" - <<'PY' || true
from Foundation import NSDistributedNotificationCenter
NSDistributedNotificationCenter.defaultCenter().postNotificationName_object_userInfo_deliverImmediately_(
    "com.krabear.agent.control",
    None,
    {"action": "quit"},
    True,
)
PY
fi

for _ in {1..12}; do
  sleep 0.25
  pids="$(find_pids)"
  if [ -z "$pids" ]; then
    echo "✅ Krab Ear backend остановлен (мягкий stop)."
    exit 0
  fi
done

echo "⚠️ Мягкая остановка не сработала, отправляю SIGTERM..."
while IFS= read -r pid; do
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
done <<< "$pids"

for _ in {1..12}; do
  sleep 0.25
  pids="$(find_pids)"
  if [ -z "$pids" ]; then
    echo "✅ Krab Ear backend остановлен (SIGTERM)."
    exit 0
  fi
done

pids="$(find_pids)"
if [ -n "$pids" ]; then
  echo "⚠️ Всё ещё жив: отправляю SIGKILL."
  while IFS= read -r pid; do
    [ -n "$pid" ] && kill -9 "$pid" 2>/dev/null || true
  done <<< "$pids"
fi

echo "✅ Команда stop завершена."
