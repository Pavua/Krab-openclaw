#!/bin/zsh
# -----------------------------------------------------------------------------
# Signal Daemon Logs (one-click) для OpenClaw
# -----------------------------------------------------------------------------
# Что делает:
# 1) Показывает последние строки out/err логов launchd signal-cli daemon.
# 2) Опционально включает follow-режим.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

OUT_LOG="$ROOT_DIR/logs/signal-daemon.out.log"
ERR_LOG="$ROOT_DIR/logs/signal-daemon.err.log"

LINES="${1:-120}"
FOLLOW="${2:-}"

if [[ ! "$LINES" =~ ^[0-9]+$ ]]; then
  echo "❌ Первый аргумент должен быть числом (кол-во строк)."
  echo "Пример: ./openclaw_signal_daemon_logs.command 200 follow"
  exit 1
fi

echo "📄 Signal daemon logs (last ${LINES} lines)"
echo

if [[ -f "$OUT_LOG" ]]; then
  echo "=== OUT: $OUT_LOG ==="
  tail -n "$LINES" "$OUT_LOG"
else
  echo "⚠️ OUT лог не найден: $OUT_LOG"
fi

echo

if [[ -f "$ERR_LOG" ]]; then
  echo "=== ERR: $ERR_LOG ==="
  tail -n "$LINES" "$ERR_LOG"
else
  echo "⚠️ ERR лог не найден: $ERR_LOG"
fi

if [[ "$FOLLOW" == "follow" || "$FOLLOW" == "-f" ]]; then
  echo
  echo "▶ Follow mode (Ctrl+C для выхода)"
  if [[ -f "$OUT_LOG" && -f "$ERR_LOG" ]]; then
    tail -n 0 -f "$OUT_LOG" "$ERR_LOG"
  elif [[ -f "$OUT_LOG" ]]; then
    tail -n 0 -f "$OUT_LOG"
  elif [[ -f "$ERR_LOG" ]]; then
    tail -n 0 -f "$ERR_LOG"
  else
    echo "❌ Нет логов для follow-режима."
    exit 1
  fi
fi

