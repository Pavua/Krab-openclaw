#!/usr/bin/env bash
# setup_cloudflare_tunnel.command — интерактивная настройка Cloudflare Tunnel для Krab Panel
# Запуск: bash scripts/setup_cloudflare_tunnel.command

set -euo pipefail

KRAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CF_CONFIG_DIR="$HOME/.cloudflared"
TUNNEL_NAME="krab-panel"

# ── цвета ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[info]${NC} $*"; }
warn()    { echo -e "${YELLOW}[warn]${NC} $*"; }
error()   { echo -e "${RED}[error]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║        Krab Panel — Cloudflare Tunnel Setup              ║"
echo "║  Option 2: free tier, zero open ports, auto-HTTPS        ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── 1. Проверить cloudflared ─────────────────────────────────────────────────
if ! command -v cloudflared &>/dev/null; then
    warn "cloudflared не найден. Устанавливаем через Homebrew..."
    if ! command -v brew &>/dev/null; then
        die "Homebrew не установлен. Установите сначала: https://brew.sh"
    fi
    brew install cloudflared
fi
CF_VERSION=$(cloudflared --version 2>&1 | head -1)
info "cloudflared: $CF_VERSION"

# ── 2. Проверить авторизацию ─────────────────────────────────────────────────
if [[ ! -f "$CF_CONFIG_DIR/cert.pem" ]]; then
    info "Авторизация в Cloudflare (откроется браузер)..."
    cloudflared tunnel login
else
    info "Cloudflare авторизация: OK (cert.pem существует)"
fi

# ── 3. Запросить домен ───────────────────────────────────────────────────────
echo ""
read -rp "Введите hostname для панели (например: krab.yourdomain.com): " KRAB_HOSTNAME
[[ -z "$KRAB_HOSTNAME" ]] && die "Hostname не может быть пустым"

# ── 4. Создать туннель (если не существует) ──────────────────────────────────
EXISTING_UUID=$(cloudflared tunnel list --output json 2>/dev/null \
    | python3 -c "import sys,json; tunnels=json.load(sys.stdin); \
      matches=[t['id'] for t in tunnels if t['name']=='$TUNNEL_NAME']; \
      print(matches[0] if matches else '')" 2>/dev/null || true)

if [[ -n "$EXISTING_UUID" ]]; then
    TUNNEL_UUID="$EXISTING_UUID"
    info "Туннель '$TUNNEL_NAME' уже существует: $TUNNEL_UUID"
else
    info "Создаём туннель '$TUNNEL_NAME'..."
    cloudflared tunnel create "$TUNNEL_NAME"
    TUNNEL_UUID=$(cloudflared tunnel list --output json 2>/dev/null \
        | python3 -c "import sys,json; tunnels=json.load(sys.stdin); \
          matches=[t['id'] for t in tunnels if t['name']=='$TUNNEL_NAME']; \
          print(matches[0] if matches else '')" 2>/dev/null)
    [[ -z "$TUNNEL_UUID" ]] && die "Не удалось получить UUID туннеля"
    info "Туннель создан: $TUNNEL_UUID"
fi

# ── 5. Сгенерировать конфиг ──────────────────────────────────────────────────
CF_CONFIG="$CF_CONFIG_DIR/config.yml"
CREDENTIALS_FILE="$CF_CONFIG_DIR/${TUNNEL_UUID}.json"

info "Генерируем $CF_CONFIG..."
mkdir -p "$CF_CONFIG_DIR"
cat > "$CF_CONFIG" <<EOF
# Krab Panel — Cloudflare Tunnel
# Сгенерировано: $(date)
tunnel: ${TUNNEL_UUID}
credentials-file: ${CREDENTIALS_FILE}

ingress:
  - hostname: ${KRAB_HOSTNAME}
    service: http://localhost:8080
    originRequest:
      connectTimeout: 30s
      keepAliveConnections: 10
      keepAliveTimeout: 90s

  - service: http_status:404

loglevel: warn
logfile: /tmp/cloudflared-krab.log
EOF
info "Конфиг записан: $CF_CONFIG"

# ── 6. Маршрут DNS ───────────────────────────────────────────────────────────
info "Добавляем DNS CNAME для $KRAB_HOSTNAME..."
cloudflared tunnel route dns "$TUNNEL_NAME" "$KRAB_HOSTNAME" || \
    warn "DNS route уже существует или ошибка — проверьте Cloudflare Dashboard"

# ── 7. Установить LaunchDaemon (опционально) ─────────────────────────────────
echo ""
read -rp "Установить автозапуск cloudflared (LaunchDaemon)? [y/N]: " INSTALL_DAEMON
if [[ "${INSTALL_DAEMON,,}" == "y" ]]; then
    info "Устанавливаем сервис (требуется sudo)..."
    sudo cloudflared service install
    info "LaunchDaemon установлен. Проверка: sudo launchctl list | grep cloudflared"
fi

# ── 8. Тест запуска ──────────────────────────────────────────────────────────
echo ""
info "Запуск туннеля для проверки (Ctrl+C для остановки)..."
echo ""
echo "  Krab Panel будет доступна: https://${KRAB_HOSTNAME}"
echo ""
echo "  ВАЖНО: Настройте Zero Trust Access Policy в Cloudflare Dashboard:"
echo "    Zero Trust → Access → Applications → Add Self-Hosted App"
echo "    URL: ${KRAB_HOSTNAME}"
echo "    Policy: Allow → Email → pavelr7@gmail.com"
echo ""
read -rp "Запустить туннель сейчас? [y/N]: " RUN_NOW
if [[ "${RUN_NOW,,}" == "y" ]]; then
    cloudflared tunnel --config "$CF_CONFIG" run "$TUNNEL_NAME"
else
    info "Запуск вручную: cloudflared tunnel --config $CF_CONFIG run $TUNNEL_NAME"
fi

echo ""
info "Готово! Панель: https://${KRAB_HOSTNAME}"
