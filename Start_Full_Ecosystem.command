#!/bin/zsh
# ------------------------------------------------------------------
# Гибридный запуск экосистемы Krab (v5.6)
# ------------------------------------------------------------------
# Этот скрипт является дирижером всей системы.
# Поддерживает выбор между Native (macOS) и Docker.
# ------------------------------------------------------------------

set -euo pipefail

BASE_DIR="/Users/pablito/Antigravity_AGENTS"
KRAB_DIR="$BASE_DIR/Краб"
EAR_DIR="$BASE_DIR/Krab Ear"
VOICE_DIR="$BASE_DIR/Krab Voice Gateway"

# Стартеры
OPENCLAW_STARTER="$KRAB_DIR/restart_openclaw.command"
VOICE_STARTER="$VOICE_DIR/scripts/start_gateway.command"
EAR_STARTER="$EAR_DIR/Start Krab Ear.command"
CORE_HARD_RESTART="$KRAB_DIR/restart_core_hard.command"

# Варианты запуска Krab Core
DOCKER_STARTER="$KRAB_DIR/scripts/run_docker.command"

HEALTH_OPENCLAW_URL="${OPENCLAW_BASE_URL:-http://127.0.0.1:18789}/health"
HEALTH_VOICE_URL="${VOICE_GATEWAY_URL:-http://127.0.0.1:8090}/health"

ensure_executable() {
  local path="$1"
  if [ ! -x "$path" ]; then
    chmod +x "$path" 2>/dev/null || true
  fi
  if [ ! -x "$path" ]; then
    echo "❌ Не найден или не исполняем: $path"
    # exit 1 # Делаем мягкий выход, чтобы не ломать всё если одного компонента нет
  fi
}

check_http() {
  local url="$1"
  /usr/bin/curl -fsS --max-time 3 "$url" >/dev/null 2>&1
}

is_krab_running() {
  pgrep -f -- "-m src.main" >/dev/null 2>&1 || pgrep -f -- "src/main.py" >/dev/null 2>&1
}

is_ear_running() {
  pgrep -f "KrabEarAgent" >/dev/null 2>&1
}

echo "======================================="
echo "   🦀 KRAB ECOSYSTEM ORCHESTRATOR    "
echo "======================================="
echo

# Разбор аргументов:
# - native|docker (режим)
# - --force-core-restart (принудительный перезапуск ядра)
MODE=""
FORCE_CORE_RESTART=0
for arg in "$@"; do
  case "$arg" in
    native|docker)
      MODE="$arg"
      ;;
    1)
      MODE="native"
      ;;
    2)
      MODE="docker"
      ;;
    --force-core-restart)
      FORCE_CORE_RESTART=1
      ;;
  esac
done

# Выбор режима (интерактивно, если не задан аргументом)
if [ -z "$MODE" ]; then
    echo "Выберите режим запуска Krab Core:"
    echo "1) Native (macOS venv) - Рекомендуется для разработки"
    echo "2) Docker (Isolation)  - Рекомендуется для стабильности"
    read -r -k 1 "CHOICE?Ввод [1/2]: "
    echo
    if [[ "$CHOICE" == "2" ]]; then
        MODE="docker"
    else
        MODE="native"
    fi
fi

echo "🚀 Режим: ${(U)MODE}"
if [[ "$FORCE_CORE_RESTART" == "1" ]]; then
  echo "♻️ Принудительный перезапуск ядра: ВКЛ"
fi
echo

# 1) OpenClaw
if check_http "$HEALTH_OPENCLAW_URL"; then
  echo "[1/4] OpenClaw: OK"
else
  echo "[1/4] Запуск OpenClaw..."
  ensure_executable "$OPENCLAW_STARTER"
  "$OPENCLAW_STARTER" >/dev/null 2>&1 || true
fi

# 2) Voice Gateway
echo "[2/4] Запуск Voice Gateway..."
ensure_executable "$VOICE_STARTER"
"$VOICE_STARTER" >/dev/null 2>&1 || true

# 3) Krab Ear
if is_ear_running; then
  echo "[3/4] Krab Ear: UP"
else
  echo "[3/4] Запуск Krab Ear..."
  ensure_executable "$EAR_STARTER"
  nohup "$EAR_STARTER" >/tmp/krab_ear_start.log 2>&1 &
fi

# 4) Krab Core
if [[ "$MODE" == "docker" ]]; then
  if is_krab_running; then
    echo "[4/4] Krab Core: UP (native process detected)"
  else
    echo "[4/4] Запуск Krab Core (DOCKER)..."
    ensure_executable "$DOCKER_STARTER"
    nohup "$DOCKER_STARTER" >/tmp/krab_docker_start.log 2>&1 &
  fi
else
  # Native режим: используем только каноничный hard-restart скрипт.
  ensure_executable "$CORE_HARD_RESTART"
  if is_krab_running; then
    if [[ "$FORCE_CORE_RESTART" == "1" ]]; then
      echo "[4/4] Krab Core: FORCE RESTART (native)..."
      "$CORE_HARD_RESTART"
    else
      echo "[4/4] Krab Core: UP"
    fi
  else
    echo "[4/4] Запуск Krab Core (NATIVE hard-restart script)..."
    "$CORE_HARD_RESTART"
  fi
fi

echo
echo "⏳ Синхронизация компонентов (8 сек)..."
sleep 8

echo "--- Статус Экосистемы ---"
if check_http "$HEALTH_OPENCLAW_URL"; then
  echo "✅ OpenClaw: UP"
else
  echo "❌ OpenClaw: DOWN (Check: $HEALTH_OPENCLAW_URL)"
fi
check_http "$HEALTH_VOICE_URL" && echo "✅ Voice Gateway: UP" || echo "❌ Voice Gateway: DOWN"
is_ear_running && echo "✅ Krab Ear: UP" || echo "❌ Krab Ear: DOWN"
is_krab_running && echo "✅ Krab Core: UP" || echo "❌ Krab Core: DOWN"

echo
echo "Для просмотра логов Krab Core используй: tail -f krab.log"
echo "Готово."
