#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="$SCRIPT_DIR/venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "❌ Не найден Python venv: $PYTHON_BIN"
  exit 1
fi

echo "🩹 Применяю hotfix OpenClaw для exec ask=off..."
"$PYTHON_BIN" "$SCRIPT_DIR/scripts/reapply_openclaw_exec_ask_off_hotfix.py"

echo
echo "♻️ Перезапускаю OpenClaw gateway..."
if command -v openclaw >/dev/null 2>&1; then
  openclaw gateway stop >/tmp/openclaw_exec_ask_off_stop.log 2>&1 || true
  sleep 2
  openclaw gateway start >/tmp/openclaw_exec_ask_off_start.log 2>&1
  echo "✅ Gateway перезапущен."
else
  echo "⚠️ openclaw CLI не найден в PATH, перезапусти gateway вручную."
fi

echo
echo "Готово. Теперь проверь новый chat/session в Control UI."
