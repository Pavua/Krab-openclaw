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

cd "$PROJECT_ROOT"
mkdir -p "$PROJECT_ROOT/logs"

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
      echo "❌ Процесс умер на ${sec}-й секунде после старта. Смотрите лог: $LOG_FILE"
      return 1
    fi
    sec=$((sec + 1))
  done

  echo "✅ Ядро стабильно живо ${stable_window}с после старта."
  return 0
}

echo "======================================="
echo "   ♻️ KRAB CORE HARD RESTART         "
echo "======================================="

stop_existing_core
start_core

echo "✅ Операция завершена."
