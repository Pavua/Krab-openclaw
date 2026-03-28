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
CURRENT_USER="$(id -un)"

resolve_voice_gateway_dir() {
  # Экосистема должна поднимать тот же Voice Gateway path, что и standalone
  # launcher, иначе на USER2/USER3 снова появится drift в сторону `pablito`.
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
VOICE_START="$DIR/Start Voice Gateway.command"

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

probe_krab_ear_ready() {
  # Для one-click запуска truth берём из watchdog probe, а не из мгновенного pgrep.
  if [ -f "$EAR_WATCHDOG" ] && [ -n "${PY_BIN:-}" ] && [ -x "${PY_BIN:-}" ]; then
    local probe_json=""
    probe_json="$("$PY_BIN" "$EAR_WATCHDOG" --probe --ear-dir "$EAR_DIR" 2>/dev/null || true)"
    if printf '%s' "$probe_json" | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; then
      return 0
    fi
  fi

  if [ -x "$EAR_RUNTIME" ] && pgrep -f "$EAR_RUNTIME --project-root $EAR_DIR" >/dev/null 2>&1; then
    return 0
  fi

  return 1
}

wait_krab_ear_ready() {
  local timeout_sec="${1:-12}"
  local started_at
  local now

  started_at="$(date +%s)"
  while true; do
    if probe_krab_ear_ready; then
      return 0
    fi

    now="$(date +%s)"
    if [ $((now - started_at)) -ge "$timeout_sec" ]; then
      return 1
    fi

    sleep 1
  done
}

resolve_krab_start_launcher() {
  # Shared/user-specific контур может держать канонический launcher
  # вне текущего repo, поэтому не жёстко привязываемся к `new start_krab.command`.
  local candidate
  for candidate in \
    "$DIR/new start_krab.command" \
    "/Users/$CURRENT_USER/Antigravity_AGENTS/new start_krab.command" \
    "$DIR/start_krab.command" \
    "$DIR/Krab.command"
  do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

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
  if probe_krab_ear_ready; then
    echo "🦻 Krab Ear уже запущен."
  else
    echo "🦻 Запускаю Krab Ear Agent..."
    nohup "$EAR_START" --launched-by-launchd > "$EAR_LOG" 2>&1 &
    if wait_krab_ear_ready 12; then
      echo "✅ Krab Ear Agent запущен."
    else
      echo "⚠️ Krab Ear не подтвердил IPC readiness за 12 сек. Лог: $EAR_LOG"
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
KRAB_START_LAUNCHER="$(resolve_krab_start_launcher || true)"
if [ -z "${KRAB_START_LAUNCHER:-}" ]; then
  echo "❌ Не найден launcher Krab/OpenClaw ни в repo, ни в аккаунтном каталоге."
  exit 1
fi
echo "🚀 Launcher: $KRAB_START_LAUNCHER"
exec "$KRAB_START_LAUNCHER"
