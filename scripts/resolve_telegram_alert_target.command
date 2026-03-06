#!/bin/bash
# =============================================================================
# Определение Telegram chat_id через Bot API getUpdates и запись в .env.
# Зачем: перевести OPENCLAW_ALERT_TARGET с @username на стабильный chat_id.
# Связь: используется runbook и check_signal_alert_route.command.
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE=".env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ .env не найден"
  exit 1
fi

set -a
# shellcheck disable=SC1091
source "$ENV_FILE"
set +a

if [[ -z "${OPENCLAW_TELEGRAM_BOT_TOKEN:-}" ]]; then
  echo "❌ OPENCLAW_TELEGRAM_BOT_TOKEN не задан в .env"
  exit 2
fi

RAW_OWNER="${1:-${OWNER_USERNAME:-}}"
OWNER_NAME="${RAW_OWNER#@}"
export OWNER_NAME

CHAT_ID="$({
python3 - <<'PY'
import json
import os
import urllib.request

token = os.environ.get("OPENCLAW_TELEGRAM_BOT_TOKEN", "").strip()
owner = os.environ.get("OWNER_NAME", "").strip().lower()
if not token:
    print("")
    raise SystemExit(0)

url = f"https://api.telegram.org/bot{token}/getUpdates?limit=100"
try:
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
except Exception:
    print("")
    raise SystemExit(0)

if not isinstance(data, dict) or not data.get("ok"):
    print("")
    raise SystemExit(0)

updates = data.get("result", [])
candidates = []
for upd in updates:
    for key in ("message", "edited_message", "my_chat_member", "channel_post"):
        msg = upd.get(key)
        if not isinstance(msg, dict):
            continue
        chat = msg.get("chat")
        if isinstance(chat, dict):
            candidates.append(chat)

# Приоритет 1: owner username
if owner:
    for chat in reversed(candidates):
        username = str(chat.get("username", "")).lower()
        if username == owner:
            print(str(chat.get("id", "")))
            raise SystemExit(0)

# Приоритет 2: любой private
for chat in reversed(candidates):
    if str(chat.get("type", "")).lower() == "private":
        print(str(chat.get("id", "")))
        raise SystemExit(0)

print("")
PY
} || true)"

if [[ -z "$CHAT_ID" ]]; then
  BOT_HINT="${OPENCLAW_TELEGRAM_BOT_USERNAME:-<ваш_бот_username>}"
  echo "❌ Chat ID не найден в getUpdates."
  echo "   1) Открой диалог с ботом @$BOT_HINT"
  echo "   2) Отправь /start"
  echo "   3) Повтори ./scripts/resolve_telegram_alert_target.command"
  exit 3
fi

python3 - "$ENV_FILE" "$CHAT_ID" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
chat_id = sys.argv[2]

keys = ["OPENCLAW_TELEGRAM_CHAT_ID", "OPENCLAW_ALERT_TARGET"]

lines = env_path.read_text(encoding="utf-8").splitlines()
out = []
seen = set()
for line in lines:
    replaced = False
    for key in keys:
        if line.startswith(f"{key}="):
            if key not in seen:
                out.append(f"{key}={chat_id}")
                seen.add(key)
            replaced = True
            break
    if not replaced:
        out.append(line)

for key in keys:
    if key not in seen:
        out.append(f"{key}={chat_id}")

env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY

echo "✅ Telegram alert target resolved"
echo "   OPENCLAW_TELEGRAM_CHAT_ID=$CHAT_ID"
echo "   OPENCLAW_ALERT_TARGET=$CHAT_ID"
