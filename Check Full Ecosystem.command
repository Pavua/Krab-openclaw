#!/bin/bash
# -----------------------------------------------------------------------------
# Быстрая проверка состояния полного стека Krab/OpenClaw/Voice/Ear.
# -----------------------------------------------------------------------------

set -euo pipefail

check_url() {
  local name="$1"
  local url="$2"
  local body
  if body="$(curl -sS -m 2 "$url" 2>/dev/null)"; then
    echo "✅ $name: $url"
    echo "   $body" | sed -e 's/^/   /'
  else
    echo "❌ $name: $url (недоступен)"
  fi
}

echo "=== Krab Ecosystem Health Check ==="
echo "Время: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

check_url "Krab Web Panel" "http://127.0.0.1:8080/api/health/lite"
check_url "OpenClaw Gateway" "http://127.0.0.1:18789/health"
check_url "Krab Voice Gateway" "http://127.0.0.1:8090/health"
check_url "Krab Ear Backend" "http://127.0.0.1:5005/health"

echo ""
echo "Готово."
read -p "Нажми Enter для закрытия окна..."

