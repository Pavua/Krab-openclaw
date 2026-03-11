#!/bin/bash
# =============================================================================
# Проверка готовности маршрута Signal-алертов в Telegram.
# Зачем: быстро убедиться, что аварийные уведомления действительно дойдут.
# Связь: используется в runbook и pre_release_smoke.py (опциональная проверка).
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
elif [[ -x ".venv_krab/bin/python" ]]; then
  PYTHON_BIN=".venv_krab/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

SEND_TEST=0
STRICT_MODE=0
for arg in "$@"; do
  case "$arg" in
    --send-test)
      SEND_TEST=1
      ;;
    --strict)
      STRICT_MODE=1
      ;;
    *)
      echo "❌ Неизвестный аргумент: $arg"
      echo "Использование: $0 [--strict] [--send-test]"
      exit 2
      ;;
  esac
done

if [[ ! -f .env ]]; then
  echo "❌ .env не найден"
  exit 1
fi

set -a
# shellcheck disable=SC1091
source ./.env
set +a

fail_count=0
warn_count=0

say_ok() { echo "✅ $1"; }
say_warn() { echo "⚠️ $1"; warn_count=$((warn_count + 1)); }
say_fail() { echo "❌ $1"; fail_count=$((fail_count + 1)); }

echo "🔎 Signal Alert Route Check"
echo "--------------------------------------------------"

if [[ -n "${OPENCLAW_TELEGRAM_BOT_TOKEN:-}" ]]; then
  say_ok "OPENCLAW_TELEGRAM_BOT_TOKEN задан"
else
  say_fail "OPENCLAW_TELEGRAM_BOT_TOKEN не задан"
fi

if [[ -n "${OPENCLAW_ALERT_CHANNEL:-}" ]]; then
  say_ok "OPENCLAW_ALERT_CHANNEL=${OPENCLAW_ALERT_CHANNEL}"
else
  say_warn "OPENCLAW_ALERT_CHANNEL пуст (обычно telegram)"
fi

if [[ -n "${OPENCLAW_ALERT_TARGET:-}" ]]; then
  say_ok "OPENCLAW_ALERT_TARGET=${OPENCLAW_ALERT_TARGET}"
else
  say_warn "OPENCLAW_ALERT_TARGET пуст"
fi

if [[ -n "${OPENCLAW_TELEGRAM_CHAT_ID:-}" ]]; then
  say_ok "OPENCLAW_TELEGRAM_CHAT_ID=${OPENCLAW_TELEGRAM_CHAT_ID}"
else
  say_warn "OPENCLAW_TELEGRAM_CHAT_ID не задан"
fi

echo
echo "📡 Проверка статуса Telegram канала..."
status_output="$({
"$PYTHON_BIN" - <<'PY'
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

web_url = "http://127.0.0.1:8080/api/openclaw/channels/status"

try:
    with urllib.request.urlopen(web_url, timeout=8) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    channels = payload.get("channels") if isinstance(payload, dict) else []
    if isinstance(channels, list):
        for item in channels:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            status = str(item.get("status") or "")
            meta = str(item.get("meta") or "")
            if "telegram" in name.lower():
                print("source=web")
                print(f"ok={1 if status.upper() == 'OK' else 0}")
                print(f"detail={name}: {status} {meta}".strip())
                raise SystemExit(0)
except (urllib.error.URLError, TimeoutError, ValueError):
    pass

proc = subprocess.run(
    ["openclaw", "channels", "status", "--probe"],
    capture_output=True,
    text=True,
    check=False,
)
raw = (proc.stdout or proc.stderr or "").strip()
ok = int(proc.returncode == 0 and ("Telegram" in raw or "telegram" in raw) and "works" in raw.lower())
print("source=cli")
print(f"ok={ok}")
print(f"detail={raw}")
PY
} || true)"

status_ok="$(echo "$status_output" | awk -F= '/^ok=/{print $2}')"
status_source="$(echo "$status_output" | awk -F= '/^source=/{print $2}')"
status_detail="$(echo "$status_output" | awk -F= '/^detail=/{print substr($0,8)}')"

if [[ "$status_ok" == "1" ]]; then
  say_ok "openclaw Telegram канал в статусе works (${status_source:-unknown})"
else
  say_fail "openclaw Telegram канал не в works (${status_source:-unknown})"
  if [[ -n "${status_detail:-}" ]]; then
    echo "$status_detail" | rg -n "Telegram|telegram|Warnings|warning|error|failed|unauthorized|works" || true
  fi
fi

echo
echo "🤖 Проверка getUpdates у Telegram бота..."
updates_info="$({
"$PYTHON_BIN" - <<'PY'
import json
import os
import ssl
import time
import urllib.request

try:
    import certifi
except Exception:  # noqa: BLE001
    certifi = None

token = os.environ.get("OPENCLAW_TELEGRAM_BOT_TOKEN", "").strip()
if not token:
    print("ok=0")
    print("updates=0")
    print("private_chat_id=")
    print("error=no_token")
    raise SystemExit(0)

url = f"https://api.telegram.org/bot{token}/getUpdates?limit=100"
ca_bundle = (
    os.environ.get("OPENCLAW_ALERT_ROUTE_CA_BUNDLE", "").strip()
    or os.environ.get("SSL_CERT_FILE", "").strip()
    or (certifi.where() if certifi else "")
)
ssl_context = None
if ca_bundle:
    try:
        ssl_context = ssl.create_default_context(cafile=ca_bundle)
    except Exception:  # noqa: BLE001
        ssl_context = None

data = None
last_error = ""
for attempt in range(1, 4):
    try:
        with urllib.request.urlopen(url, timeout=20, context=ssl_context) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        break
    except Exception as exc:
        last_error = str(exc)
        if attempt < 3:
            time.sleep(1.5)

if not isinstance(data, dict):
    print("ok=0")
    print("updates=0")
    print("private_chat_id=")
    print(f"error={last_error}")
    raise SystemExit(0)

ok = bool(data.get("ok"))
updates = data.get("result", [])
private_chat_id = ""
for upd in reversed(updates):
    for key in ("message", "edited_message", "my_chat_member", "channel_post"):
        msg = upd.get(key)
        if not isinstance(msg, dict):
            continue
        chat = msg.get("chat")
        if not isinstance(chat, dict):
            continue
        if str(chat.get("type", "")).lower() == "private":
            private_chat_id = str(chat.get("id", ""))
            break
    if private_chat_id:
        break

print(f"ok={1 if ok else 0}")
print(f"updates={len(updates)}")
print(f"private_chat_id={private_chat_id}")
print("error=")
print(f"ca_bundle={ca_bundle}")
PY
} || true)"

updates_ok="$(echo "$updates_info" | awk -F= '/^ok=/{print $2}')"
updates_count="$(echo "$updates_info" | awk -F= '/^updates=/{print $2}')"
private_chat_id="$(echo "$updates_info" | awk -F= '/^private_chat_id=/{print $2}')"
updates_error="$(echo "$updates_info" | awk -F= '/^error=/{print substr($0,7)}')"
updates_ca_bundle="$(echo "$updates_info" | awk -F= '/^ca_bundle=/{print substr($0,11)}')"

if [[ "$updates_ok" == "1" ]]; then
  say_ok "Bot API getUpdates доступен"
else
  say_fail "Bot API getUpdates недоступен"
  if [[ -n "${updates_error:-}" ]]; then
    echo "   detail: ${updates_error}"
  fi
  if [[ -n "${updates_ca_bundle:-}" ]]; then
    echo "   ca_bundle: ${updates_ca_bundle}"
  fi
fi

if [[ "${updates_count:-0}" -gt 0 ]]; then
  say_ok "У бота есть updates: ${updates_count}"
else
  say_warn "У бота нет updates (возможно, /start ещё не отправляли)"
fi

if [[ -n "${private_chat_id:-}" ]]; then
  say_ok "Найден private chat_id в updates: ${private_chat_id}"
else
  say_warn "Private chat_id не найден в updates"
fi

if [[ -n "${OPENCLAW_TELEGRAM_CHAT_ID:-}" && -n "${private_chat_id:-}" ]]; then
  if [[ "${OPENCLAW_TELEGRAM_CHAT_ID}" == "${private_chat_id}" ]]; then
    say_ok "OPENCLAW_TELEGRAM_CHAT_ID совпадает с private chat_id"
  else
    say_warn "OPENCLAW_TELEGRAM_CHAT_ID отличается от private chat_id"
  fi
fi

if [[ "${OPENCLAW_ALERT_CHANNEL:-telegram}" == "telegram" && -n "${OPENCLAW_TELEGRAM_CHAT_ID:-}" ]]; then
  if [[ "${OPENCLAW_ALERT_TARGET:-}" == "${OPENCLAW_TELEGRAM_CHAT_ID:-}" ]]; then
    say_ok "OPENCLAW_ALERT_TARGET синхронизирован с chat_id"
  else
    say_warn "OPENCLAW_ALERT_TARGET не синхронизирован с OPENCLAW_TELEGRAM_CHAT_ID"
  fi
fi

echo "--------------------------------------------------"
if [[ "$SEND_TEST" -eq 1 ]]; then
  echo "🧪 Запуск test alert..."
  if [[ -x "./scripts/signal_alert_test.command" ]]; then
    if ./scripts/signal_alert_test.command; then
      say_ok "signal_alert_test прошел"
    else
      say_fail "signal_alert_test не прошел"
    fi
  else
    say_warn "signal_alert_test.command отсутствует, тест отправки пропущен"
  fi
fi

if [[ "$STRICT_MODE" -eq 1 && "$warn_count" -gt 0 ]]; then
  echo "Итог: FAIL (strict mode, предупреждений: ${warn_count})"
  exit 1
fi

if [[ "$fail_count" -gt 0 ]]; then
  echo "Итог: FAIL (${fail_count} критичных, ${warn_count} предупреждений)"
  exit 1
fi

echo "Итог: OK (${warn_count} предупреждений)"
exit 0
