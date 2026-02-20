#!/bin/zsh
# Настройка маршрута автоалертов OpenClaw в .env.
# По умолчанию: Telegram -> chat_id (если известен), иначе OWNER_USERNAME.

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

CHANNEL="${1:-${OPENCLAW_ALERT_CHANNEL:-telegram}}"

if [[ -n "${2:-}" ]]; then
  TARGET="$2"
else
  TARGET="${OPENCLAW_TELEGRAM_CHAT_ID:-${OWNER_TELEGRAM_ID:-${OPENCLAW_ALERT_TARGET:-${OWNER_USERNAME:-}}}}"
fi

if [[ -z "$TARGET" ]]; then
  echo "❌ Не удалось определить target. Передай явно:"
  echo "   ./scripts/configure_alert_route.command telegram @username"
  exit 2
fi

# Нормализация Telegram username.
if [[ "$CHANNEL" == "telegram" ]]; then
  if [[ "$TARGET" != @* && "$TARGET" != -* && "$TARGET" != +* && "$TARGET" =~ ^[A-Za-z0-9_]+$ ]]; then
    TARGET="@${TARGET}"
  fi
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

upsert_env "OPENCLAW_ALERT_CHANNEL" "$CHANNEL"
upsert_env "OPENCLAW_ALERT_TARGET" "$TARGET"

echo "✅ Alert route configured"
echo "   OPENCLAW_ALERT_CHANNEL=$CHANNEL"
echo "   OPENCLAW_ALERT_TARGET=$TARGET"

if [[ "$CHANNEL" == "telegram" && "$TARGET" == @* ]]; then
  echo
  echo "ℹ️ Target задан как username. Если тест алерта падает с chat not found:"
  echo "   1) напиши боту /start в Telegram,"
  echo "   2) выполни ./scripts/resolve_telegram_alert_target.command"
fi
