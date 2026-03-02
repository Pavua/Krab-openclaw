#!/bin/bash
# -----------------------------------------------------------------------------
# Полная остановка экосистемы:
# 1) Krab/OpenClaw
# 2) Krab Voice Gateway
# 3) Krab Ear Agent
# -----------------------------------------------------------------------------

set -euo pipefail

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
AG_ROOT="$(cd "$DIR/.." && pwd)"

VOICE_DIR="${KRAB_VOICE_GATEWAY_DIR:-$AG_ROOT/Krab Voice Gateway}"
VOICE_STOP="$VOICE_DIR/scripts/stop_gateway.command"

EAR_DIR="${KRAB_EAR_DIR:-$AG_ROOT/Krab Ear}"
EAR_RUNTIME="$EAR_DIR/native/runtime/KrabEarAgent"
EAR_LEGACY="$EAR_DIR/native/KrabEarAgent/.build/release/KrabEarAgent"

echo "🛑 Остановка полной экосистемы Krab..."

if [ -x "$DIR/new Stop Krab.command" ]; then
  "$DIR/new Stop Krab.command" || true
else
  echo "⚠️ Не найден new Stop Krab.command"
fi

if [ -x "$VOICE_STOP" ]; then
  echo "🎙️ Останавливаю Krab Voice Gateway..."
  "$VOICE_STOP" || true
else
  echo "⚠️ Не найден stop скрипт Voice Gateway: $VOICE_STOP"
fi

echo "🦻 Останавливаю Krab Ear Agent..."
EAR_PIDS="$(
  {
    pgrep -f "$EAR_RUNTIME --project-root $EAR_DIR" 2>/dev/null || true
    pgrep -f "$EAR_LEGACY --project-root $EAR_DIR" 2>/dev/null || true
  } | awk 'NF { print $1 }' | awk '!seen[$1]++'
)"

if [ -n "$EAR_PIDS" ]; then
  echo "👻 Найдены процессы Krab Ear: $EAR_PIDS"
  echo "$EAR_PIDS" | xargs kill -TERM 2>/dev/null || true
  sleep 1
  EAR_PIDS="$(echo "$EAR_PIDS" | while read -r pid; do ps -p "$pid" >/dev/null 2>&1 && echo "$pid"; done)"
  if [ -n "$EAR_PIDS" ]; then
    echo "⚠️ Форс-остановка Krab Ear: $EAR_PIDS"
    echo "$EAR_PIDS" | xargs kill -KILL 2>/dev/null || true
  fi
else
  echo "ℹ️ Krab Ear процессы не найдены."
fi

echo "✅ Полная остановка завершена."
sleep 1

