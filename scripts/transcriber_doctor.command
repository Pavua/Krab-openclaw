#!/bin/zsh
# -----------------------------------------------------------------------------
# Transcriber Doctor (Krab)
# -----------------------------------------------------------------------------
# Что это:
# Быстрый one-click доктор для проблем с транскрибацией и Voice Gateway.
#
# Зачем:
# Когда "транскрибатор вылетел", этот скрипт за один запуск показывает:
# - здоровье OpenClaw и Voice Gateway;
# - есть ли слушатель на voice-порту;
# - есть ли критичный перегруз RAM (pyrefly/LSP);
# - последние строки логов для мгновенной диагностики.
#
# Режимы:
# - check (по умолчанию): только диагностика.
# - --heal: мягкое восстановление (перезапуск Voice Gateway и очистка heavy pyrefly).
# -----------------------------------------------------------------------------

set -euo pipefail

ACTION="${1:-check}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VOICE_URL="${VOICE_GATEWAY_URL:-http://127.0.0.1:8090}"
OPENCLAW_URL="${OPENCLAW_BASE_URL:-http://127.0.0.1:18789}"
VOICE_ROOT="/Users/pablito/Antigravity_AGENTS/Krab Voice Gateway"

print_section() {
  echo
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "$1"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

http_status() {
  local url="$1"
  curl -sS -m 3 -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000"
}

check_python_voice_env() {
  if [ ! -x "$ROOT_DIR/.venv/bin/python" ]; then
    echo "⚠️  $ROOT_DIR/.venv/bin/python не найден."
    return 0
  fi
  "$ROOT_DIR/.venv/bin/python" - <<'PY'
import importlib.util
mods = ("aiohttp", "structlog", "mlx_whisper")
missing = [m for m in mods if importlib.util.find_spec(m) is None]
if missing:
    print("⚠️  В .venv отсутствуют модули:", ", ".join(missing))
else:
    print("✅ .venv: зависимости для voice-цепочки доступны.")
PY
}

check_ram_pressure() {
  local heavy
  heavy="$(ps -ax -o pid,rss,command | awk '/[p]yrefly/ && $2 > 6000000 {print $1":"$2":"$3}')"
  if [ -n "$heavy" ]; then
    echo "⚠️  Найдены heavy pyrefly процессы (> ~5.7GB RSS):"
    echo "$heavy" | while IFS= read -r line; do
      local pid rss_kb cmd
      pid="$(echo "$line" | cut -d: -f1)"
      rss_kb="$(echo "$line" | cut -d: -f2)"
      cmd="$(echo "$line" | cut -d: -f3-)"
      printf "   - PID=%s RSS=%.1fGB CMD=%s\n" "$pid" "$(awk "BEGIN {print $rss_kb/1048576}")" "$cmd"
    done
  else
    echo "✅ Критичных pyrefly-процессов по RSS не обнаружено."
  fi
}

heal_ram_pressure() {
  local pids
  pids="$(ps -ax -o pid,rss,command | awk '/[p]yrefly/ && $2 > 6000000 {print $1}')"
  if [ -z "$pids" ]; then
    echo "ℹ️  Heavy pyrefly для остановки не найден."
    return 0
  fi
  echo "🛠️  Останавливаю heavy pyrefly PID: $pids"
  for pid in $pids; do
    kill "$pid" 2>/dev/null || true
  done
  sleep 1
}

check_endpoints() {
  local openclaw_health voice_health
  openclaw_health="$(http_status "$OPENCLAW_URL/health")"
  voice_health="$(http_status "$VOICE_URL/health")"

  if [ "$openclaw_health" = "200" ]; then
    echo "✅ OpenClaw health: 200"
  else
    echo "⚠️  OpenClaw health: $openclaw_health ($OPENCLAW_URL/health)"
  fi

  if [ "$voice_health" = "200" ]; then
    echo "✅ Voice Gateway health: 200"
  else
    echo "⚠️  Voice Gateway health: $voice_health ($VOICE_URL/health)"
  fi

  echo
  echo "Порт Voice Gateway (LISTEN):"
  lsof -nP -iTCP:8090 -sTCP:LISTEN || echo "⚠️  На 8090 никто не слушает."
}

heal_voice_gateway() {
  local health
  health="$(http_status "$VOICE_URL/health")"
  if [ "$health" = "200" ]; then
    echo "✅ Voice Gateway уже доступен, перезапуск не требуется."
    return 0
  fi

  if [ -x "$VOICE_ROOT/scripts/start_gateway.command" ]; then
    echo "🛠️  Запускаю Voice Gateway через scripts/start_gateway.command ..."
    "$VOICE_ROOT/scripts/start_gateway.command" || true
  elif [ -x "$VOICE_ROOT/start_gateway.command" ]; then
    echo "🛠️  Запускаю Voice Gateway через start_gateway.command ..."
    "$VOICE_ROOT/start_gateway.command" || true
  else
    echo "❌ Не найден стартовый скрипт Voice Gateway в $VOICE_ROOT"
    return 1
  fi

  for _ in {1..12}; do
    sleep 1
    health="$(http_status "$VOICE_URL/health")"
    if [ "$health" = "200" ]; then
      echo "✅ Voice Gateway поднялся."
      return 0
    fi
  done

  echo "❌ Voice Gateway не поднялся после попытки восстановления."
  return 1
}

tail_logs() {
  local log_file
  print_section "Хвост логов (последние 60 строк)"
  for log_file in \
    "$ROOT_DIR/krab.log" \
    "$ROOT_DIR/openclaw.log" \
    "$VOICE_ROOT/gateway.log"; do
    if [ -f "$log_file" ]; then
      echo
      echo "📄 $log_file"
      tail -n 60 "$log_file"
    fi
  done
}

check_recent_agx_crash() {
  local krab_log="$ROOT_DIR/krab.log"
  if [ ! -f "$krab_log" ]; then
    return 0
  fi
  if tail -n 1200 "$krab_log" | rg -q "AGX|SIGABRT|failed assertion .*command buffer"; then
    echo "⚠️  В последних логах найдены признаки Metal/AGX аварии (SIGABRT)."
    echo "   Рекомендация: оставить STT_ISOLATED_WORKER=1 и перезапустить Krab."
  else
    echo "✅ В хвосте krab.log не найдено свежих AGX/SIGABRT сигнатур."
  fi
}

print_section "Transcriber Doctor"
echo "📂 ROOT: $ROOT_DIR"
echo "⚙️  MODE: $ACTION"

print_section "Проверка окружения"
check_python_voice_env

print_section "Проверка health/портов"
check_endpoints

print_section "Проверка RAM pressure"
check_ram_pressure
check_recent_agx_crash

if [ "$ACTION" = "--heal" ]; then
  print_section "Восстановление"
  heal_ram_pressure
  heal_voice_gateway || true

  print_section "Повторная проверка после heal"
  check_endpoints
fi

tail_logs

echo
echo "✅ Transcriber Doctor завершён."
if [ "$ACTION" = "--heal" ]; then
  echo "ℹ️  Рекомендация: перезапусти окно транскрибатора и повтори check для контроля."
fi
