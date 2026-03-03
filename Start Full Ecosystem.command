#!/bin/bash
# -----------------------------------------------------------------------------
# Полный one-click запуск экосистемы:
# 1) Krab Voice Gateway
# 2) Krab Ear Agent (backend + native runtime)
# 3) Krab/OpenClaw (через штатный new start_krab.command)
# -----------------------------------------------------------------------------

set -euo pipefail

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
AG_ROOT="$(cd "$DIR/.." && pwd)"

VOICE_DIR="${KRAB_VOICE_GATEWAY_DIR:-$AG_ROOT/Krab Voice Gateway}"
VOICE_START="$VOICE_DIR/scripts/start_gateway.command"

EAR_DIR="${KRAB_EAR_DIR:-$AG_ROOT/Krab Ear}"
EAR_START="$EAR_DIR/scripts/start_agent.command"
EAR_RUNTIME="$EAR_DIR/native/runtime/KrabEarAgent"
EAR_WATCHDOG="$DIR/scripts/krab_ear_watchdog.py"
EAR_WATCHDOG_LOG="$DIR/logs/krab_ear_watchdog.log"
EAR_WATCHDOG_PID="$DIR/logs/krab_ear_watchdog.pid"
if [ -x "$DIR/.venv/bin/python" ]; then
  PY_BIN="$DIR/.venv/bin/python"
else
  PY_BIN="$(command -v python3 || true)"
fi

mkdir -p "$DIR/logs"
EAR_LOG="$DIR/logs/krab_ear_start.log"

echo "🧩 Запуск полной экосистемы Krab..."
echo "📂 Krab dir: $DIR"
echo "📂 Voice dir: $VOICE_DIR"
echo "📂 Ear dir: $EAR_DIR"

if [ -x "$VOICE_START" ]; then
  echo "🎙️ Запускаю Krab Voice Gateway..."
  "$VOICE_START" || true
else
  echo "⚠️ Не найден start скрипт Voice Gateway: $VOICE_START"
fi

if [ -x "$EAR_START" ]; then
  if [ -x "$EAR_RUNTIME" ] && pgrep -f "$EAR_RUNTIME --project-root $EAR_DIR" >/dev/null 2>&1; then
    echo "🦻 Krab Ear уже запущен."
  else
    echo "🦻 Запускаю Krab Ear Agent..."
    nohup "$EAR_START" --launched-by-launchd > "$EAR_LOG" 2>&1 &
    sleep 1
    if [ -x "$EAR_RUNTIME" ] && pgrep -f "$EAR_RUNTIME --project-root $EAR_DIR" >/dev/null 2>&1; then
      echo "✅ Krab Ear Agent запущен."
    else
      echo "⚠️ Krab Ear пока не подтвердил запуск. Лог: $EAR_LOG"
    fi
  fi
else
  echo "⚠️ Не найден start скрипт Krab Ear: $EAR_START"
fi

# Watchdog Krab Ear: автоматически поднимает агент обратно,
# если backend отвалился (например, при memory pressure).
if [ -f "$EAR_WATCHDOG_PID" ]; then
  WD_PID="$(cat "$EAR_WATCHDOG_PID" 2>/dev/null || true)"
  if [ -n "${WD_PID:-}" ] && kill -0 "$WD_PID" >/dev/null 2>&1; then
    echo "🛡️ Krab Ear Watchdog уже запущен (PID $WD_PID)."
  else
    rm -f "$EAR_WATCHDOG_PID"
  fi
fi

if [ ! -f "$EAR_WATCHDOG_PID" ]; then
  if [ -f "$EAR_WATCHDOG" ] && [ -n "${PY_BIN:-}" ]; then
    echo "🛡️ Запускаю Krab Ear Watchdog..."
    nohup "$PY_BIN" "$EAR_WATCHDOG" \
      --ear-dir "$EAR_DIR" \
      --start-script "$EAR_START" \
      --runtime-bin "$EAR_RUNTIME" \
      >> "$EAR_WATCHDOG_LOG" 2>&1 &
    echo $! > "$EAR_WATCHDOG_PID"
    echo "✅ Krab Ear Watchdog запущен (PID $(cat "$EAR_WATCHDOG_PID"))."
  else
    echo "⚠️ Не удалось запустить Krab Ear Watchdog (нет python или скрипта)."
  fi
fi

echo "🦀 Перехожу к запуску Krab/OpenClaw..."
exec "$DIR/new start_krab.command"
