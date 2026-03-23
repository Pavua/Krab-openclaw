#!/bin/bash
# -----------------------------------------------------------------------------
# One-click запуск Krab Voice Gateway с per-account runtime-state.
# Нужен для multi-account режима: код Voice Gateway может жить в shared/symlink
# каталоге, но venv/pid/log должны оставаться в текущей macOS-учётке.
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
AG_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GW_DIR="${KRAB_VOICE_GATEWAY_DIR:-$AG_ROOT/Krab Voice Gateway}"
RUNTIME_STATE_DIR="${KRAB_RUNTIME_STATE_DIR:-$HOME/.openclaw/krab_runtime_state}"
VOICE_RUNTIME_DIR="$RUNTIME_STATE_DIR/voice_gateway"
VENV_DIR="$VOICE_RUNTIME_DIR/.venv_krab_voice_gateway"
REQ_FILE="$GW_DIR/requirements.txt"
STAMP_FILE="$VENV_DIR/.requirements.sha256"
LOG_FILE="$VOICE_RUNTIME_DIR/gateway.log"
PID_FILE="$VOICE_RUNTIME_DIR/gateway.pid"
HOST="${KRAB_VOICE_HOST:-127.0.0.1}"
PORT="${KRAB_VOICE_PORT:-8090}"

probe_health() {
  python3 - <<'PY'
import json
import sys
import urllib.error
import urllib.request

url = "http://127.0.0.1:8090/health"
try:
    with urllib.request.urlopen(url, timeout=2.5) as response:
        raw = response.read().decode("utf-8", "replace")
except (urllib.error.URLError, TimeoutError, OSError):
    raise SystemExit(1)

try:
    payload = json.loads(raw)
except Exception:
    payload = {}

ok = bool(payload.get("ok")) or str(payload.get("status", "")).strip().lower() in {"ok", "healthy", "up"}
raise SystemExit(0 if ok else 1)
PY
}

wait_healthy() {
  local timeout_sec="${1:-18}"
  local step=0
  local max_steps=$((timeout_sec * 2))
  while [ "$step" -lt "$max_steps" ]; do
    if probe_health; then
      return 0
    fi
    sleep 0.5
    step=$((step + 1))
  done
  return 1
}

if [ ! -d "$GW_DIR" ]; then
  echo "❌ Voice Gateway не найден: $GW_DIR"
  read -r -p "Нажмите Enter для закрытия..."
  exit 1
fi

if [ ! -f "$REQ_FILE" ]; then
  echo "❌ Не найден requirements.txt Voice Gateway: $REQ_FILE"
  read -r -p "Нажмите Enter для закрытия..."
  exit 1
fi

mkdir -p "$VOICE_RUNTIME_DIR"

if probe_health; then
  echo "✅ Krab Voice Gateway уже отвечает на :$PORT."
  exit 0
fi

if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${OLD_PID:-}" ] && kill -0 "$OLD_PID" >/dev/null 2>&1; then
    echo "ℹ️ Krab Voice Gateway уже запущен (PID $OLD_PID)."
    exit 0
  fi
  rm -f "$PID_FILE" >/dev/null 2>&1 || true
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  /usr/bin/env python3 -m venv "$VENV_DIR"
fi

REQ_HASH="$(shasum -a 256 "$REQ_FILE" | awk '{print $1}')"
INSTALLED_HASH="$(cat "$STAMP_FILE" 2>/dev/null || true)"
if [ "$REQ_HASH" != "$INSTALLED_HASH" ]; then
  "$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
  "$VENV_DIR/bin/python" -m pip install -r "$REQ_FILE" >/dev/null
  printf '%s' "$REQ_HASH" > "$STAMP_FILE"
fi

echo "🎙️ Starting Krab Voice Gateway..."
echo "📂 Directory: $GW_DIR"
echo "🐍 Python: $VENV_DIR/bin/python"
echo "📝 Log: $LOG_FILE"

(
  cd "$GW_DIR"
  export KRAB_VOICE_API_KEY="${KRAB_VOICE_API_KEY:-dummy_voice_key}"
  export KRAB_VOICE_HOST="$HOST"
  export KRAB_VOICE_PORT="$PORT"
  nohup "$VENV_DIR/bin/python" -m uvicorn app.main:app --host "$HOST" --port "$PORT" >> "$LOG_FILE" 2>&1 &
  printf '%s' "$!" > "$PID_FILE"
)

if wait_healthy 18; then
  echo "✅ Krab Voice Gateway слушает порт $PORT и проходит health-check."
else
  echo "⚠️ Krab Voice Gateway не успел стать healthy на :$PORT. Проверь $LOG_FILE"
fi
