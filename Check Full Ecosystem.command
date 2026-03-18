#!/bin/bash
# -----------------------------------------------------------------------------
# Быстрая проверка состояния полного стека Krab/OpenClaw/Voice/Ear.
# -----------------------------------------------------------------------------

set -euo pipefail

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
AG_ROOT="$(cd "$DIR/.." && pwd)"
EAR_DIR="${KRAB_EAR_DIR:-$AG_ROOT/Krab Ear}"
EAR_WATCHDOG="$DIR/scripts/krab_ear_watchdog.py"
if [ -x "$DIR/.venv/bin/python" ]; then
  PY_BIN="$DIR/.venv/bin/python"
else
  PY_BIN="$(command -v python3 || true)"
fi

check_url() {
  local name="$1"
  local url="$2"
  local body
  if body="$(curl -sS -m 2 "$url" 2>/dev/null)"; then
    echo "✅ $name: $url"
    echo "$body" | sed -e 's/^/   /'
  else
    echo "❌ $name: $url (недоступен)"
  fi
}

check_krab_ear_ipc() {
  if [ -z "${PY_BIN:-}" ] || [ ! -f "$EAR_WATCHDOG" ]; then
    echo "⚠️ Krab Ear IPC: пропущено (нет python или watchdog-скрипта)"
    return 0
  fi
  local out
  if out="$("$PY_BIN" "$EAR_WATCHDOG" --probe --ear-dir "$EAR_DIR" 2>/dev/null)"; then
    echo "✅ Krab Ear IPC: $out"
  else
    echo "❌ Krab Ear IPC: backend не отвечает"
  fi
}

echo "=== Krab Ecosystem Health Check ==="
echo "Время: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

check_url "Krab Web Panel" "http://127.0.0.1:8080/api/health/lite"
check_url "OpenClaw Gateway" "http://127.0.0.1:18789/health"
check_url "Krab Voice Gateway" "http://127.0.0.1:8090/health"
check_krab_ear_ipc
check_url "Krab Ear Backend (HTTP fallback)" "http://127.0.0.1:5005/health"

echo ""
echo "Готово."
read -p "Нажми Enter для закрытия окна..."
