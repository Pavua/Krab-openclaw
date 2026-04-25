#!/usr/bin/env bash
# activate_named_tunnel.sh — финальная активация Named Cloudflare Tunnel.
#
# Pre-requisite (1 ручной шаг — НЕ автоматизируется, OAuth):
#   cloudflared tunnel login
#   → откроется браузер → залогиниться → выбрать зону
#   → создаст ~/.cloudflared/cert.pem
#
# Запуск:
#   bash scripts/activate_named_tunnel.sh <hostname>
#   # пример: bash scripts/activate_named_tunnel.sh krab-alerts.pavelrodionov.com
#
# Что делает (всё автоматически после login):
#   1. Проверяет cert.pem
#   2. Создаёт tunnel `krab-alerts` (если ещё нет)
#   3. Генерирует ~/.cloudflared/config.yml с UUID + hostname
#   4. Привязывает DNS CNAME
#   5. Опционально — обновляет Sentry webhook
#   6. Останавливает quick tunnel + sentry-sync LaunchAgents
#   7. Загружает named tunnel LaunchAgent
#   8. Smoke-test: curl https://<hostname>/api/health/lite

set -euo pipefail

KRAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CF_DIR="$HOME/.cloudflared"
LA_DIR="$HOME/Library/LaunchAgents"
TUNNEL_NAME="krab-alerts"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[info]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}  $*"; }
fatal() { echo -e "${RED}[fatal]${NC} $*" >&2; exit 1; }

HOSTNAME_ARG="${1:-}"
[[ -z "$HOSTNAME_ARG" ]] && fatal "Usage: $0 <hostname>  (e.g. krab-alerts.pavelrodionov.com)"

# ── 1. cert.pem check ────────────────────────────────────────────────────────
if [[ ! -f "$CF_DIR/cert.pem" ]]; then
    fatal "cert.pem не найден. Сначала запусти: cloudflared tunnel login"
fi
info "cert.pem OK"

# ── 2. create tunnel (idempotent) ────────────────────────────────────────────
EXISTING_UUID=$(cloudflared tunnel list --output json 2>/dev/null \
    | python3 -c "import sys,json; t=json.load(sys.stdin); m=[x['id'] for x in t if x['name']=='${TUNNEL_NAME}']; print(m[0] if m else '')" 2>/dev/null || true)

if [[ -n "$EXISTING_UUID" ]]; then
    TUNNEL_UUID="$EXISTING_UUID"
    info "Tunnel '${TUNNEL_NAME}' уже существует: $TUNNEL_UUID"
else
    info "Создаём tunnel '${TUNNEL_NAME}'..."
    cloudflared tunnel create "$TUNNEL_NAME"
    TUNNEL_UUID=$(cloudflared tunnel list --output json 2>/dev/null \
        | python3 -c "import sys,json; t=json.load(sys.stdin); m=[x['id'] for x in t if x['name']=='${TUNNEL_NAME}']; print(m[0] if m else '')")
    [[ -z "$TUNNEL_UUID" ]] && fatal "Не удалось получить UUID после create"
    info "Tunnel создан: $TUNNEL_UUID"
fi

# ── 3. generate config.yml ───────────────────────────────────────────────────
CFG="$CF_DIR/config.yml"
CREDS="$CF_DIR/${TUNNEL_UUID}.json"
[[ -f "$CREDS" ]] || fatal "credentials file не найден: $CREDS"

cat > "$CFG" <<EOF
# Krab Named Cloudflare Tunnel
# Generated: $(date)
tunnel: ${TUNNEL_UUID}
credentials-file: ${CREDS}

ingress:
  - hostname: ${HOSTNAME_ARG}
    service: http://127.0.0.1:8080
    originRequest:
      connectTimeout: 30s
      tlsTimeout: 30s
      tcpKeepAlive: 30s
      httpHostHeader: localhost
      keepAliveConnections: 10
      keepAliveTimeout: 90s
  - service: http_status:404

loglevel: warn
logfile: /tmp/krab_cf_tunnel/named-tunnel.log
metrics: localhost:2999
EOF
info "config.yml записан: $CFG"

# ── 4. DNS route ─────────────────────────────────────────────────────────────
info "Привязываем DNS CNAME ${HOSTNAME_ARG} → ${TUNNEL_UUID}.cfargotunnel.com..."
cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME_ARG" 2>&1 \
    | grep -v "already exists" || true

# ── 5. Sentry webhook update (optional) ──────────────────────────────────────
WEBHOOK_URL="https://${HOSTNAME_ARG}/api/sentry/webhook"
if [[ -n "${SENTRY_TOKEN:-}" && -n "${SENTRY_ORG:-}" ]]; then
    info "Обновляем Sentry webhook → $WEBHOOK_URL"
    python3 "$KRAB_ROOT/scripts/setup_sentry_alerts.py" \
        --webhook-url "$WEBHOOK_URL" || warn "Sentry update не прошёл — обнови вручную"
else
    warn "SENTRY_TOKEN/SENTRY_ORG не выставлены — обнови webhook вручную:"
    warn "  Sentry → Settings → Integrations → Internal Integration → $WEBHOOK_URL"
fi

# ── 6. swap LaunchAgents ─────────────────────────────────────────────────────
mkdir -p /tmp/krab_cf_tunnel

info "Останавливаем quick tunnel + sentry-sync..."
launchctl unload -w "$LA_DIR/ai.krab.cloudflared-tunnel.plist" 2>/dev/null || true
launchctl unload -w "$LA_DIR/ai.krab.cloudflared-sentry-sync.plist" 2>/dev/null || true

info "Копируем named tunnel plist..."
cp "$KRAB_ROOT/scripts/launchagents/ai.krab.cloudflared-named-tunnel.plist" "$LA_DIR/"
launchctl load -w "$LA_DIR/ai.krab.cloudflared-named-tunnel.plist"

# ── 7. smoke test ────────────────────────────────────────────────────────────
info "Ждём 8 сек на запуск туннеля..."
sleep 8

if launchctl list | grep -q "ai.krab.cloudflared-named-tunnel"; then
    info "LaunchAgent активен"
else
    warn "LaunchAgent не виден в launchctl list — проверь /tmp/krab_cf_tunnel/named-tunnel.err.log"
fi

echo ""
info "Smoke test:"
echo "  curl -I https://${HOSTNAME_ARG}/api/health/lite"
echo ""
info "DONE. Hostname: https://${HOSTNAME_ARG}"
info "Логи: tail -f /tmp/krab_cf_tunnel/named-tunnel.log"
