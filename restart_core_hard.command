#!/bin/zsh
# -----------------------------------------------------------------------------
# Каноничный hard-restart ядра Krab.
#
# Что делает:
# 1) Находит все процессы ядра (`src/main.py`, `-m src.main`)
# 2) Пытается корректно остановить (TERM -> ожидание -> KILL)
# 3) Поднимает один новый процесс из выбранного venv
# 4) Записывает PID и проверяет, что процесс жив
#
# Режим DRY RUN:
#   KRAB_RESTART_DRY_RUN=1 ./restart_core_hard.command
# -----------------------------------------------------------------------------

set -euo pipefail

PROJECT_ROOT="/Users/pablito/Antigravity_AGENTS/Краб"
LOG_FILE="$PROJECT_ROOT/logs/krab.log"
PID_FILE="$PROJECT_ROOT/krab_core.pid"
DRY_RUN="${KRAB_RESTART_DRY_RUN:-0}"
LOCK_FILE="$PROJECT_ROOT/.runtime/krab_core.lock"
BACKOFF_STATE_FILE="$PROJECT_ROOT/.runtime/restart_core_backoff.state"

cd "$PROJECT_ROOT"
mkdir -p "$PROJECT_ROOT/logs"
mkdir -p "$PROJECT_ROOT/.runtime"

# Загружаем .env поверх текущего окружения, чтобы рестарт использовал
# актуальные ключи/настройки проекта, а не случайные переменные из shell.
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  source "$PROJECT_ROOT/.env"
  set +a
fi

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[DRY RUN] Режим проверки без остановки/запуска процессов."
fi

find_core_pids() {
  local pids
  pids="$(
    {
      pgrep -f -- "python(.+)?src/main.py" || true
      pgrep -f -- "python(.+)?-m src.main" || true
    } | tr ' ' '\n' | sed '/^$/d' | sort -u
  )"
  echo "$pids"
}

read_lock_pid() {
  if [[ ! -f "$LOCK_FILE" ]]; then
    return 1
  fi
  python3 - "$LOCK_FILE" <<'PY'
import json, sys
path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    pid = int(data.get("pid", 0) or 0)
    if pid > 0:
        print(pid)
except Exception:
    pass
PY
}

cleanup_stale_lock() {
  local lock_pid
  lock_pid="$(read_lock_pid || true)"
  if [[ -z "${lock_pid:-}" ]]; then
    return 0
  fi

  if ! ps -p "$lock_pid" >/dev/null 2>&1; then
    echo "🧹 Найден stale lock (PID=$lock_pid), удаляю: $LOCK_FILE"
    if [[ "$DRY_RUN" != "1" ]]; then
      rm -f "$LOCK_FILE"
    fi
  fi
}

load_backoff_state() {
  if [[ ! -f "$BACKOFF_STATE_FILE" ]]; then
    echo "0 0"
    return 0
  fi
  awk 'NR==1 {print $1" "$2}' "$BACKOFF_STATE_FILE" 2>/dev/null || echo "0 0"
}

save_backoff_state() {
  local fail_count="$1"
  local last_ts="$2"
  echo "$fail_count $last_ts" > "$BACKOFF_STATE_FILE"
}

reset_backoff_state() {
  save_backoff_state "0" "0"
}

apply_start_backoff_if_needed() {
  local now_ts
  now_ts="$(date +%s)"
  local state
  state="$(load_backoff_state)"
  local fail_count last_fail_ts
  fail_count="$(echo "$state" | awk '{print $1}')"
  last_fail_ts="$(echo "$state" | awk '{print $2}')"
  fail_count="${fail_count:-0}"
  last_fail_ts="${last_fail_ts:-0}"

  if [[ "$fail_count" -gt 0 ]] && [[ $((now_ts - last_fail_ts)) -lt 300 ]]; then
    local sleep_sec=$((fail_count * 5))
    if [[ "$sleep_sec" -gt 30 ]]; then
      sleep_sec=30
    fi
    echo "⏳ Backoff: обнаружены недавние падения ядра ($fail_count), пауза ${sleep_sec}с перед запуском."
    sleep "$sleep_sec"
  fi
}

stop_existing_core() {
  local pids
  pids="$(find_core_pids)"
  if [[ -z "$pids" ]]; then
    echo "ℹ️ Активных процессов ядра не найдено."
    return 0
  fi

  echo "🛑 Найдены процессы ядра:"
  echo "$pids"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY RUN] Пропускаю остановку процессов."
    return 0
  fi

  echo "$pids" | xargs kill -TERM 2>/dev/null || true

  # Ждём graceful shutdown до 12 секунд.
  for _ in {1..12}; do
    sleep 1
    local still_running
    still_running="$(find_core_pids)"
    if [[ -z "$still_running" ]]; then
      echo "✅ Graceful stop завершён."
      return 0
    fi
  done

  local hard_pids
  hard_pids="$(find_core_pids)"
  if [[ -n "$hard_pids" ]]; then
    echo "⚠️ Процессы не завершились, выполняю kill -9:"
    echo "$hard_pids"
    echo "$hard_pids" | xargs kill -KILL 2>/dev/null || true
  fi

  sleep 1
  cleanup_stale_lock
}

resolve_python() {
  if [[ -x "$PROJECT_ROOT/.venv/bin/python3" ]]; then
    echo "$PROJECT_ROOT/.venv/bin/python3"
    return 0
  fi
  if [[ -x "$PROJECT_ROOT/.venv_krab/bin/python3" ]]; then
    echo "$PROJECT_ROOT/.venv_krab/bin/python3"
    return 0
  fi
  echo "python3"
}

start_core() {
  local py
  py="$(resolve_python)"
  echo "🚀 Запуск ядра через: $py -m src.main"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY RUN] Пропускаю запуск."
    return 0
  fi

  apply_start_backoff_if_needed

  export PYTHONPATH="$PROJECT_ROOT"
  nohup "$py" -m src.main >> "$LOG_FILE" 2>&1 &
  local new_pid=$!
  echo "$new_pid" > "$PID_FILE"
  echo "🧾 Новый PID: $new_pid"

  # Проверяем не только мгновенный старт, но и стабильность в течение окна.
  local stable_window="${KRAB_HEALTHCHECK_SECONDS:-12}"
  local sec=1
  while [[ "$sec" -le "$stable_window" ]]; do
    sleep 1
    if ! ps -p "$new_pid" >/dev/null 2>&1; then
      # Важно: если целевой PID завершился из-за singleton-lock гонки,
      # но другой процесс ядра уже жив, считаем рестарт успешным.
      local active_now
      active_now="$(find_core_pids)"
      if [[ -n "${active_now:-}" ]]; then
        local active_pid
        active_pid="$(echo "$active_now" | head -n 1)"
        echo "⚠️ Стартовый PID $new_pid завершился на ${sec}-й секунде, но ядро активно на PID: $active_pid"
        echo "$active_pid" > "$PID_FILE"
        echo "✅ Рестарт считается успешным (обнаружен живой singleton-процесс)."
        reset_backoff_state
        return 0
      fi

      echo "❌ Процесс умер на ${sec}-й секунде после старта. Смотрите лог: $LOG_FILE"
      local now_ts
      now_ts="$(date +%s)"
      local state
      state="$(load_backoff_state)"
      local fail_count
      fail_count="$(echo "$state" | awk '{print $1}')"
      fail_count="${fail_count:-0}"
      fail_count=$((fail_count + 1))
      save_backoff_state "$fail_count" "$now_ts"
      echo "🧪 Последние строки лога перед падением:"
      tail -n 40 "$LOG_FILE" || true
      return 1
    fi
    sec=$((sec + 1))
  done

  echo "✅ Ядро стабильно живо ${stable_window}с после старта."
  reset_backoff_state
  return 0
}

echo "======================================="
echo "   ♻️ KRAB CORE HARD RESTART         "
echo "======================================="

stop_existing_core
start_core

echo "✅ Операция завершена."
