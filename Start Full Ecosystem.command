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

echo "🦀 Перехожу к запуску Krab/OpenClaw..."
exec "$DIR/new start_krab.command"

