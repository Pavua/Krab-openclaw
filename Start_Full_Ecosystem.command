#!/bin/zsh
# ------------------------------------------------------------------
# Гибридный запуск экосистемы Krab через штатные стартеры проектов.
# Этот скрипт НЕ дублирует логику venv/uvicorn и не использует kill -9.
# ------------------------------------------------------------------

set -euo pipefail

BASE_DIR="/Users/pablito/Antigravity_AGENTS"
KRAB_DIR="$BASE_DIR/Краб"
EAR_DIR="$BASE_DIR/Krab Ear"
VOICE_DIR="$BASE_DIR/Krab Voice Gateway"

KRAB_STARTER="$KRAB_DIR/start_krab.command"
EAR_STARTER="$EAR_DIR/Start Krab Ear.command"
VOICE_STARTER="$VOICE_DIR/scripts/start_gateway.command"
OPENCLAW_STARTER="$KRAB_DIR/restart_openclaw.command"

HEALTH_OPENCLAW_URL="${OPENCLAW_BASE_URL:-http://127.0.0.1:18789}/health"
HEALTH_VOICE_URL="${VOICE_GATEWAY_URL:-http://127.0.0.1:8090}/health"

ensure_executable() {
  local path="$1"
  if [ ! -x "$path" ]; then
    echo "❌ Не найден исполняемый стартер: $path"
    exit 1
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
  pgrep -f "KrabEarAgent --project-root $EAR_DIR" >/dev/null 2>&1
}

ensure_executable "$KRAB_STARTER"
ensure_executable "$EAR_STARTER"
ensure_executable "$VOICE_STARTER"
ensure_executable "$OPENCLAW_STARTER"

echo "🚀 Запуск экосистемы Krab (hybrid wrapper)"
echo

# 1) OpenClaw
if check_http "$HEALTH_OPENCLAW_URL"; then
  echo "[1/4] OpenClaw уже доступен: $HEALTH_OPENCLAW_URL"
else
  echo "[1/4] Запускаю OpenClaw через $OPENCLAW_STARTER"
  "$OPENCLAW_STARTER" >/dev/null 2>&1 || true
fi

# 2) Voice Gateway

echo "[2/4] Запускаю Krab Voice Gateway через $VOICE_STARTER"
"$VOICE_STARTER" >/dev/null 2>&1 || true

# 3) Krab Ear
if is_ear_running; then
  echo "[3/4] Krab Ear уже запущен"
else
  echo "[3/4] Запускаю Krab Ear через $EAR_STARTER"
  nohup "$EAR_STARTER" >/tmp/krab_ear_start.log 2>&1 &
fi

# 4) Krab
if is_krab_running; then
  echo "[4/4] Krab уже запущен"
else
  echo "[4/4] Запускаю Krab через $KRAB_STARTER"
  nohup "$KRAB_STARTER" >>"$KRAB_DIR/krab.log" 2>&1 &
fi

echo
echo "⏳ Ожидаю инициализацию сервисов..."
sleep 8

echo "--- Health Report ---"
if check_http "$HEALTH_OPENCLAW_URL"; then
  echo "✅ OpenClaw: UP"
else
  echo "❌ OpenClaw: DOWN"
fi

if check_http "$HEALTH_VOICE_URL"; then
  echo "✅ Voice Gateway: UP"
else
  echo "❌ Voice Gateway: DOWN"
fi

if is_ear_running; then
  echo "✅ Krab Ear: UP"
else
  echo "❌ Krab Ear: DOWN"
fi

if is_krab_running; then
  echo "✅ Krab Core: UP"
else
  echo "❌ Krab Core: DOWN"
fi

echo
echo "Готово."
