#!/bin/bash
# =============================================================================
# Настройка маршрута автоалертов OpenClaw в .env.
# Зачем: быстро задать OPENCLAW_ALERT_CHANNEL/OPENCLAW_ALERT_TARGET без
# ручного редактирования .env.
# Связь: используется вместе с resolve/check/signal_alert_test скриптами.
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

python3 - "$ENV_FILE" "$CHANNEL" "$TARGET" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
channel = sys.argv[2]
target = sys.argv[3]

updates = {
    "OPENCLAW_ALERT_CHANNEL": channel,
    "OPENCLAW_ALERT_TARGET": target,
}

lines = env_path.read_text(encoding="utf-8").splitlines()
out = []
seen = set()
for line in lines:
    replaced = False
    for key, value in updates.items():
        if line.startswith(f"{key}="):
            if key not in seen:
                out.append(f"{key}={value}")
                seen.add(key)
            replaced = True
            break
    if not replaced:
        out.append(line)

for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")

env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY

echo "✅ Alert route configured"
echo "   OPENCLAW_ALERT_CHANNEL=$CHANNEL"
echo "   OPENCLAW_ALERT_TARGET=$TARGET"

if [[ "$CHANNEL" == "telegram" && "$TARGET" == @* ]]; then
  echo
  echo "ℹ️ Если алерт по username не проходит (chat not found):"
  echo "   1) напиши боту /start в Telegram"
  echo "   2) выполни ./scripts/resolve_telegram_alert_target.command"
fi
