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
LOG_DIR="$DIR/logs"
RUNTIME_STATE_DIR="${KRAB_RUNTIME_STATE_DIR:-$HOME/.openclaw/krab_runtime_state}"

resolve_voice_gateway_dir() {
  # Stop-скрипт должен смотреть в тот же resolved path, что и start-скрипты.
  local current_user
  current_user="$(id -un)"
  local candidates=()

  if [ -n "${KRAB_VOICE_GATEWAY_DIR:-}" ]; then
    candidates+=("$KRAB_VOICE_GATEWAY_DIR")
  fi

  if [ "$current_user" = "pablito" ]; then
    candidates+=(
      "$AG_ROOT/Krab Voice Gateway"
      "/Users/Shared/Antigravity_AGENTS/Krab Voice Gateway"
    )
  else
    candidates+=(
      "/Users/Shared/Antigravity_AGENTS/Krab Voice Gateway"
      "$AG_ROOT/Krab Voice Gateway"
      "/Users/pablito/Antigravity_AGENTS/Krab Voice Gateway"
    )
  fi

  local candidate
  for candidate in "${candidates[@]}"; do
    [ -n "$candidate" ] || continue
    if [ -d "$candidate" ] && [ -f "$candidate/requirements.txt" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  printf '%s\n' "${KRAB_VOICE_GATEWAY_DIR:-$AG_ROOT/Krab Voice Gateway}"
}

VOICE_DIR="$(resolve_voice_gateway_dir)"
VOICE_STOP="$VOICE_DIR/scripts/stop_gateway.command"
VOICE_FALLBACK_PID="$RUNTIME_STATE_DIR/voice_gateway/gateway.pid"
VOICE_PORT="${KRAB_VOICE_PORT:-8090}"
CURRENT_USER="$(id -un)"

EAR_DIR="${KRAB_EAR_DIR:-$AG_ROOT/Krab Ear}"
EAR_RUNTIME="$EAR_DIR/native/runtime/KrabEarAgent"
EAR_LEGACY="$EAR_DIR/native/KrabEarAgent/.build/release/KrabEarAgent"
EAR_BACKEND="$EAR_DIR/KrabEar/backend/service.py"
EAR_WATCHDOG_PID="$DIR/logs/krab_ear_watchdog.pid"
EAR_AGENT_LABEL="com.krabear.agent"
EAR_REST_LABEL="ai.krab.ear.rest"
EAR_FALLBACK_PID="$LOG_DIR/krab_ear_fallback.pid"

is_pid_alive() {
  local pid="$1"
  [ -n "${pid:-}" ] && kill -0 "$pid" >/dev/null 2>&1
}

stop_pid_file_process() {
  local pid_file="$1"
  local label="$2"
  if [ ! -f "$pid_file" ]; then
    return 0
  fi

  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if is_pid_alive "${pid:-}"; then
    echo "🧹 Останавливаю $label по PID-файлу (PID $pid)..."
    kill -TERM "$pid" >/dev/null 2>&1 || true
    sleep 0.8
    if is_pid_alive "$pid"; then
      kill -KILL "$pid" >/dev/null 2>&1 || true
    fi
  fi
  rm -f "$pid_file"
}

voice_listener_pids() {
  local listeners
  listeners="$(lsof -t -i "tcp:${VOICE_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -z "$listeners" ]; then
    return 0
  fi

  while IFS= read -r pid; do
    [ -n "$pid" ] || continue
    local cmd
    local owner
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    owner="$(ps -p "$pid" -o user= 2>/dev/null | awk '{print $1}')"
    if [ -n "$owner" ] && [ "$owner" != "$CURRENT_USER" ]; then
      continue
    fi
    if echo "$cmd" | grep -F "$VOICE_DIR" >/dev/null 2>&1 || echo "$cmd" | grep -F "app.main:app" >/dev/null 2>&1; then
      echo "$pid"
    fi
  done <<< "$listeners"
}

voice_gateway_owned_by_current_user() {
  local listeners
  listeners="$(lsof -t -i "tcp:${VOICE_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -z "$listeners" ]; then
    return 1
  fi
  local pid owner
  for pid in $listeners; do
    owner="$(ps -p "$pid" -o user= 2>/dev/null | awk '{print $1}')"
    if [ -n "$owner" ] && [ "$owner" = "$CURRENT_USER" ]; then
      return 0
    fi
  done
  return 1
}

bootout_launch_agent() {
  local label="$1"
  # Krab Ear поднимается через launchd с KeepAlive=true, поэтому простого kill
  # недостаточно: launchd мгновенно воскрешает процесс. Для truly-full stop
  # выгружаем агент из user bootstrap namespace текущей macOS-учётки.
  launchctl bootout "gui/$(id -u)/${label}" >/dev/null 2>&1 || true
  launchctl bootout "user/$(id -u)/${label}" >/dev/null 2>&1 || true
  launchctl remove "${label}" >/dev/null 2>&1 || true
}

echo "🛑 Остановка полной экосистемы Krab..."

if [ -f "$EAR_WATCHDOG_PID" ]; then
  WD_PID="$(cat "$EAR_WATCHDOG_PID" 2>/dev/null || true)"
  if [ -n "${WD_PID:-}" ] && kill -0 "$WD_PID" >/dev/null 2>&1; then
    echo "🛡️ Останавливаю Krab Ear Watchdog (PID $WD_PID)..."
    kill -TERM "$WD_PID" 2>/dev/null || true
  fi
  rm -f "$EAR_WATCHDOG_PID"
fi

if [ -x "$DIR/Stop Krab.command" ]; then
  "$DIR/Stop Krab.command" || true
else
  echo "⚠️ Не найден Stop Krab.command"
fi

stop_pid_file_process "$VOICE_FALLBACK_PID" "Voice Gateway per-account runtime"

if voice_gateway_owned_by_current_user; then
  if [ -x "$VOICE_STOP" ]; then
    echo "🎙️ Останавливаю Krab Voice Gateway..."
    "$VOICE_STOP" || true
  else
    echo "⚠️ Не найден stop скрипт Voice Gateway: $VOICE_STOP"
  fi

  VOICE_PIDS="$(voice_listener_pids | awk 'NF { print $1 }' | awk '!seen[$1]++')"
  if [ -n "$VOICE_PIDS" ]; then
    echo "🎙️ Добиваю fallback/listener-процессы Voice Gateway: $VOICE_PIDS"
    echo "$VOICE_PIDS" | xargs kill -TERM 2>/dev/null || true
    sleep 1
    VOICE_PIDS="$(
      echo "$VOICE_PIDS" | while read -r pid; do
        ps -p "$pid" >/dev/null 2>&1 && echo "$pid"
      done || true
    )"
    if [ -n "$VOICE_PIDS" ]; then
      echo "⚠️ Форс-остановка Voice Gateway: $VOICE_PIDS"
      echo "$VOICE_PIDS" | xargs kill -KILL 2>/dev/null || true
    fi
  fi
else
  echo "ℹ️ Voice Gateway не принадлежит текущей учётке или не найден — пропускаю остановку."
fi

echo "🦻 Останавливаю Krab Ear Agent..."
echo "🧯 Выгружаю launchd-агенты Krab Ear..."
bootout_launch_agent "$EAR_AGENT_LABEL"
bootout_launch_agent "$EAR_REST_LABEL"
stop_pid_file_process "$EAR_FALLBACK_PID" "Krab Ear fallback"

EAR_PIDS="$(
  {
    pgrep -f "$EAR_RUNTIME --project-root $EAR_DIR" 2>/dev/null || true
    pgrep -f "$EAR_LEGACY --project-root $EAR_DIR" 2>/dev/null || true
    pgrep -f "$EAR_BACKEND" 2>/dev/null || true
    pgrep -f "$EAR_DIR/KrabEar/backend/rest_server.py" 2>/dev/null || true
  } | awk 'NF { print $1 }' | awk '!seen[$1]++'
)"

if [ -n "$EAR_PIDS" ]; then
  echo "👻 Найдены процессы Krab Ear: $EAR_PIDS"
  echo "$EAR_PIDS" | xargs kill -TERM 2>/dev/null || true
  sleep 1
  EAR_PIDS="$(
    echo "$EAR_PIDS" | while read -r pid; do
      ps -p "$pid" >/dev/null 2>&1 && echo "$pid"
    done || true
  )"
  if [ -n "$EAR_PIDS" ]; then
    echo "⚠️ Форс-остановка Krab Ear: $EAR_PIDS"
    echo "$EAR_PIDS" | xargs kill -KILL 2>/dev/null || true
  fi
else
  echo "ℹ️ Krab Ear процессы не найдены."
fi

echo "✅ Полная остановка завершена."
sleep 1
