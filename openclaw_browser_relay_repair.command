#!/bin/zsh
# -----------------------------------------------------------------------------
# Восстановление OpenClaw Browser Relay одним кликом.
# Проверяет daemon, порт relay и выполняет smoke check через web API.
# -----------------------------------------------------------------------------

set -euo pipefail

PROJECT_ROOT="/Users/pablito/Antigravity_AGENTS/Краб"
RELAY_URL="http://127.0.0.1:18789"
WEB_PANEL_URL="http://127.0.0.1:8080"

cd "$PROJECT_ROOT"

echo "======================================="
echo "  🛠 OpenClaw Browser Relay Repair"
echo "======================================="

if [[ -x "$PROJECT_ROOT/openclaw_signal_daemon_status.command" ]]; then
  echo "\n[1/4] Проверка signal daemon статуса..."
  "$PROJECT_ROOT/openclaw_signal_daemon_status.command" || true
fi

if ! curl -fsS --max-time 4 "$RELAY_URL" >/dev/null 2>&1; then
  echo "\n[2/4] Relay не отвечает на $RELAY_URL — запускаю daemon..."
  "$PROJECT_ROOT/openclaw_signal_daemon.command" || true
  sleep 2
else
  echo "\n[2/4] Relay reachable: $RELAY_URL"
fi

if curl -fsS --max-time 4 "$RELAY_URL" >/dev/null 2>&1; then
  echo "[OK] Relay отвечает."
else
  echo "[FAIL] Relay всё ещё недоступен. Проверьте логи daemon/расширения Chrome."
fi

echo "\n[3/4] Browser smoke через Krab Web API..."
if curl -fsS --max-time 25 "$WEB_PANEL_URL/api/openclaw/browser-smoke?url=https%3A%2F%2Fexample.com" >/tmp/krab_browser_smoke.json 2>/dev/null; then
  if rg -q '"ok"\s*:\s*true' /tmp/krab_browser_smoke.json; then
    echo "[OK] Browser smoke вернул ok=true"
  else
    echo "[WARN] Browser smoke ответил без ok=true (смотрите /tmp/krab_browser_smoke.json)"
  fi
else
  echo "[WARN] Не удалось выполнить browser smoke через $WEB_PANEL_URL"
fi

echo "\n[4/4] Следующий ручной шаг в Chrome extension:"
echo "- Убедитесь, что в OpenClaw Browser Relay options стоит порт 18789"
echo "- На целевой вкладке нажмите иконку расширения для attach/detach"
echo "- Если остаётся жёлтый статус: обновите вкладку и повторите attach"

echo "\n✅ Repair script завершён."
