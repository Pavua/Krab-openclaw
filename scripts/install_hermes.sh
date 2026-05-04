#!/bin/bash
# Wave 19-C: установка Hermes binary для активации Krab Phase C/D wire-up.
#
# Что делает:
#   1. Проверяет, есть ли hermes уже в PATH или ~/.hermes/bin/
#   2. Пытается установить через pipx (предпочтительно) или pip3 + venv
#   3. Создаёт симлинк ~/.hermes/bin/hermes если binary в нестандартном месте
#   4. Проверяет ~/.hermes/config.yaml (Phase A — должен уже существовать)
#   5. Печатает следующие шаги для активации в Krab (env vars + restart)
#
# Использование:
#   ./scripts/install_hermes.sh
#
# НЕ запускать автоматически из Krab — только вручную.

set -euo pipefail

HERMES_BIN_DIR="$HOME/.hermes/bin"
HERMES_VENV="$HOME/.hermes-venv"
HERMES_CONFIG="$HOME/.hermes/config.yaml"

# Цвета для вывода (отключаются если не tty)
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    RED='\033[0;31m'
    BLUE='\033[0;34m'
    NC='\033[0m'
else
    GREEN=''; YELLOW=''; RED=''; BLUE=''; NC=''
fi

info()    { echo -e "${BLUE}ℹ${NC}  $*"; }
success() { echo -e "${GREEN}✅${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠️${NC}  $*"; }
error()   { echo -e "${RED}❌${NC} $*"; }

echo ""
echo "=== Wave 19-C: Hermes binary installer ==="
echo ""

# ──────────────────────────────────────────────
# 1. Проверяем — не установлен ли уже
# ──────────────────────────────────────────────

if command -v hermes &>/dev/null; then
    HERMES_PATH="$(command -v hermes)"
    success "hermes уже в PATH: ${HERMES_PATH}"
    hermes --version 2>&1 | head -3 || true
    echo ""
    info "Активация в Krab — см. шаги ниже."
    HERMES_INSTALLED=1
elif [ -x "$HERMES_BIN_DIR/hermes" ]; then
    success "hermes найден в ~/.hermes/bin/ (не в PATH)"
    echo ""
    warn "Добавь в PATH (или выставь KRAB_HERMES_BINARY):"
    echo "  export PATH=\"\$HOME/.hermes/bin:\$PATH\""
    echo ""
    HERMES_INSTALLED=1
else
    HERMES_INSTALLED=0
fi

# ──────────────────────────────────────────────
# 2. Установка (если не найден)
# ──────────────────────────────────────────────

if [ "$HERMES_INSTALLED" -eq 0 ]; then
    echo "Hermes не найден. Пробуем установить..."
    echo ""

    INSTALL_OK=0

    # Вариант A: pipx (предпочтительно — изолированный env, в PATH автоматически)
    if command -v pipx &>/dev/null; then
        info "Установка через pipx..."
        if pipx install hermes-agent; then
            INSTALL_OK=1
            success "pipx install завершён."
        else
            warn "pipx install не удался, пробуем pip3..."
        fi
    fi

    # Вариант B: pip3 в выделенный venv
    if [ "$INSTALL_OK" -eq 0 ] && command -v pip3 &>/dev/null; then
        info "Установка через pip3 в ${HERMES_VENV}..."
        python3 -m venv "$HERMES_VENV"
        if "$HERMES_VENV/bin/pip" install hermes-agent; then
            INSTALL_OK=1
            mkdir -p "$HERMES_BIN_DIR"
            # Создаём симлинк в стандартное место
            if [ -x "$HERMES_VENV/bin/hermes" ]; then
                ln -sf "$HERMES_VENV/bin/hermes" "$HERMES_BIN_DIR/hermes"
                success "Симлинк создан: $HERMES_BIN_DIR/hermes"
            fi
        else
            warn "pip3 install тоже не удался."
        fi
    fi

    if [ "$INSTALL_OK" -eq 0 ]; then
        error "Автоматическая установка не удалась."
        echo ""
        echo "Ручная установка:"
        echo "  pipx install hermes-agent"
        echo "  ИЛИ"
        echo "  pip install --user hermes-agent"
        echo ""
        echo "Официальная документация: https://docs.hermes-agent.com/install"
        exit 1
    fi
fi

# ──────────────────────────────────────────────
# 3. Финальная верификация binary
# ──────────────────────────────────────────────

HERMES_EXEC=""
if command -v hermes &>/dev/null; then
    HERMES_EXEC="$(command -v hermes)"
elif [ -x "$HERMES_BIN_DIR/hermes" ]; then
    HERMES_EXEC="$HERMES_BIN_DIR/hermes"
fi

if [ -z "$HERMES_EXEC" ]; then
    error "Верификация не прошла: hermes не найден после установки."
    echo "Проверь \$PATH или выставь KRAB_HERMES_BINARY=<путь> в .env"
    exit 1
fi

echo ""
success "Hermes binary: $HERMES_EXEC"
"$HERMES_EXEC" --version 2>&1 | head -3 || true

# ──────────────────────────────────────────────
# 4. Проверяем конфиг (Phase A — уже должен существовать)
# ──────────────────────────────────────────────

echo ""
if [ -f "$HERMES_CONFIG" ]; then
    success "Конфиг найден: $HERMES_CONFIG"
else
    warn "Конфиг не найден: $HERMES_CONFIG"
    echo "  Phase A (scripts/start_hermes_standalone.command) должен создать его."
    echo "  Запусти Phase A скрипт, или создай ~/.hermes/config.yaml вручную."
fi

# ──────────────────────────────────────────────
# 5. Следующие шаги для активации в Krab
# ──────────────────────────────────────────────

echo ""
echo "══════════════════════════════════════════"
echo " Активация Hermes Phase C/D в Krab"
echo "══════════════════════════════════════════"
echo ""
echo "1. Добавь в .env:"
echo ""
echo "   KRAB_AGENT_ENGINE=hermes          # или 'auto' для health-gated routing"
echo "   KRAB_AGENT_ENGINE_DISPATCH_ENABLED=1"
if [ -z "$(command -v hermes 2>/dev/null)" ]; then
    # Binary только в ~/.hermes/bin/ — нужно выставить
    echo "   KRAB_HERMES_BINARY=$HERMES_EXEC"
fi
echo ""
echo "2. Перезапусти Krab:"
echo ""
echo "   'new Stop Krab.command' && 'new start_krab.command'"
echo ""
echo "3. Проверь через owner panel:"
echo ""
echo "   curl http://127.0.0.1:8080/api/agent-engine/status"
echo ""
echo "4. (опционально) A/B по командам в Telegram:"
echo ""
echo "   !engine room analysts hermes"
echo "   !engine status"
echo ""
success "Готово."
