#!/bin/zsh
# Krab/OpenClaw: лабораторный профиль для beta-ветки.
# Зачем: тестировать новые фичи OpenClaw без риска для боевого stable-контура.
# Важно: работает в отдельном профиле (--profile lab), прод не затрагивается.
# Важно-2: LAB изолируется на отдельном порту, чтобы не конфликтовать с PROD.

set -euo pipefail

PROFILE="lab"
LAB_PORT="${OPENCLAW_LAB_PORT:-18890}"

clear
echo "🧪 OpenClaw LAB (beta) — изолированный профиль"
echo "Профиль: ${PROFILE} (~/.openclaw-${PROFILE})"
echo "Порт LAB gateway: ${LAB_PORT}"
echo "Дата: $(date)"
echo

echo "1) Перевожу LAB-профиль на beta-канал..."
openclaw --profile "${PROFILE}" update --channel beta --yes
echo

echo "2) Применяю изолированные настройки LAB (mode=local, port=${LAB_PORT})..."
openclaw --profile "${PROFILE}" config set gateway.mode "\"local\"" --json
openclaw --profile "${PROFILE}" config set gateway.port "${LAB_PORT}" --json
echo

echo "3) Устанавливаю безопасные права credentials в LAB..."
mkdir -p "$HOME/.openclaw-${PROFILE}/credentials"
chmod 700 "$HOME/.openclaw-${PROFILE}/credentials" || true
echo

echo "4) Переустанавливаю LAB LaunchAgent на порт ${LAB_PORT}..."
openclaw --profile "${PROFILE}" gateway uninstall || true
openclaw --profile "${PROFILE}" gateway install --force --port "${LAB_PORT}"
openclaw --profile "${PROFILE}" gateway start
echo

echo "5) Статус LAB-профиля..."
openclaw --profile "${PROFILE}" status
echo

echo "6) Открываю Dashboard для LAB-профиля..."
openclaw --profile "${PROFILE}" dashboard
