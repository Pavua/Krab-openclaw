#!/bin/zsh
# -----------------------------------------------------------------------------
# Полный перезапуск Krab с защитой от sqlite lock в session-файлах Pyrogram.
#
# Что делает:
# 1) Останавливает все варианты процессов ядра (`src/main.py`, `-m src.main`)
# 2) Дожидается завершения, при необходимости делает kill -9
# 3) Чистит временные логи/кэш и sidecar-файлы SQLite (`-wal`, `-shm`, `-journal`)
# 4) Запускает ядро в foreground через единый entrypoint `-m src.main`
#
# DRY RUN:
#   FULL_RESTART_DRY_RUN=1 ./full_restart.command
# -----------------------------------------------------------------------------

set -euo pipefail
setopt null_glob

PROJECT_ROOT="/Users/pablito/Antigravity_AGENTS/Краб"
DRY_RUN="${FULL_RESTART_DRY_RUN:-0}"

cd "$PROJECT_ROOT"

# Критично: перед запуском ядра принудительно загружаем .env и
# перекрываем внешние переменные окружения (старые ключи/URL).
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  source "$PROJECT_ROOT/.env"
  set +a
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

stop_core_processes() {
  local pids
  pids="$(find_core_pids)"
  if [[ -z "$pids" ]]; then
    echo "ℹ️ Активных процессов ядра не найдено."
    return 0
  fi

  echo "🛑 Найдены процессы ядра:"
  echo "$pids"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY RUN] Остановка процессов пропущена."
    return 0
  fi

  echo "$pids" | xargs kill -TERM 2>/dev/null || true
  for _ in {1..12}; do
    sleep 1
    if [[ -z "$(find_core_pids)" ]]; then
      echo "✅ Ядро остановлено корректно."
      return 0
    fi
  done

  local hard_pids
  hard_pids="$(find_core_pids)"
  if [[ -n "$hard_pids" ]]; then
    echo "⚠️ Процессы ядра не завершились, выполняю kill -9:"
    echo "$hard_pids"
    echo "$hard_pids" | xargs kill -KILL 2>/dev/null || true
  fi
}

stop_project_node_processes() {
  local node_pids
  node_pids="$(
    pgrep -f -- "node(.+)?Antigravity_AGENTS/Краб" 2>/dev/null || true
  )"
  if [[ -z "$node_pids" ]]; then
    return 0
  fi
  echo "🛑 Найдены node-процессы проекта:"
  echo "$node_pids"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY RUN] Остановка node-процессов пропущена."
    return 0
  fi
  echo "$node_pids" | xargs kill -TERM 2>/dev/null || true
}

cleanup_runtime_files() {
  echo "🧹 Очищаю логи и временные файлы..."
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY RUN] Очистка файлов пропущена."
    return 0
  fi

  mkdir -p "$PROJECT_ROOT/logs" "$PROJECT_ROOT/voice_cache"
  rm -rf "$PROJECT_ROOT/logs/"* || true
  rm -rf "$PROJECT_ROOT/voice_cache/"* || true

  # Убираем только sidecar-файлы SQLite, основной .session не трогаем.
  for session_file in "$PROJECT_ROOT"/*.session; do
    [[ -e "$session_file" ]] || continue
    rm -f "${session_file}-journal" "${session_file}-wal" "${session_file}-shm" || true
  done
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

echo "🛑 Останавливаю всё..."
stop_core_processes
stop_project_node_processes
cleanup_runtime_files

PYTHON_BIN="$(resolve_python)"
echo "🚀 Запускаю Krab v11.0 (Autonomous)..."
echo "Использую Python: $PYTHON_BIN"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[DRY RUN] Запуск пропущен."
  exit 0
fi

export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"
exec "$PYTHON_BIN" -m src.main
