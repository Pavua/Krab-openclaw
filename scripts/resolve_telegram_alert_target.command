#!/bin/zsh
# Определение Telegram chat_id для автоалертов через Bot API getUpdates.
#
# Что делает:
# 1) Читает OPENCLAW_TELEGRAM_BOT_TOKEN из .env
# 2) Ищет подходящий чат (по OWNER_USERNAME или аргументу)
# 3) Записывает OPENCLAW_TELEGRAM_CHAT_ID в .env
# 4) При telegram-канале обновляет OPENCLAW_ALERT_TARGET на найденный chat_id

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE=".env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ .env не найден"
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

if [[ -z "${OPENCLAW_TELEGRAM_BOT_TOKEN:-}" ]]; then
  echo "❌ OPENCLAW_TELEGRAM_BOT_TOKEN не задан в .env"
  exit 2
fi

RAW_OWNER="${1:-${OWNER_USERNAME:-}}"
OWNER_NAME="${RAW_OWNER#@}"
export OWNER_NAME

CHAT_ID="$(
python3 - <<'PY'
import json
import os
import sys
import urllib.parse
import urllib.request

token = os.environ.get("OPENCLAW_TELEGRAM_BOT_TOKEN", "").strip()
owner = os.environ.get("OWNER_NAME", "").strip().lower()
if not token:
    print("")
    raise SystemExit(0)

url = f"https://api.telegram.org/bot{token}/getUpdates?limit=100"
with urllib.request.urlopen(url, timeout=20) as resp:
    data = json.loads(resp.read().decode("utf-8"))

if not data.get("ok"):
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
        if not isinstance(chat, dict):
            continue
        candidates.append(chat)

# Перебираем с конца: самые свежие апдейты важнее.
for chat in reversed(candidates):
    ctype = str(chat.get("type", "")).lower()
    username = str(chat.get("username", "")).lower()
    if owner and username == owner:
        print(str(chat.get("id", "")))
        raise SystemExit(0)

for chat in reversed(candidates):
    if str(chat.get("type", "")).lower() == "private":
        print(str(chat.get("id", "")))
        raise SystemExit(0)

print("")
PY
)"

if [[ -z "$CHAT_ID" ]]; then
  echo "❌ Chat ID не найден."
  echo "   1) Открой в Telegram диалог с ботом @mytest_feb2026_bot"
  echo "   2) Отправь команду /start"
  echo "   3) Повтори: ./scripts/resolve_telegram_alert_target.command"
  exit 3
fi

upsert_env() {
  local key="$1"
  local value="$2"
  python3 - "$ENV_FILE" "$key" "$value" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]

lines = env_path.read_text(encoding="utf-8").splitlines()
out = []
found = False
for line in lines:
    if line.startswith(f"{key}="):
        if not found:
            out.append(f"{key}={value}")
            found = True
        # дубли ключа отбрасываем
        continue
    out.append(line)

if not found:
    out.append(f"{key}={value}")

env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}

upsert_env "OPENCLAW_TELEGRAM_CHAT_ID" "$CHAT_ID"

if [[ "${OPENCLAW_ALERT_CHANNEL:-telegram}" == "telegram" ]]; then
  upsert_env "OPENCLAW_ALERT_TARGET" "$CHAT_ID"
fi

echo "✅ Telegram alert target resolved"
echo "   OPENCLAW_TELEGRAM_CHAT_ID=$CHAT_ID"
if [[ "${OPENCLAW_ALERT_CHANNEL:-telegram}" == "telegram" ]]; then
  echo "   OPENCLAW_ALERT_TARGET=$CHAT_ID"
fi
