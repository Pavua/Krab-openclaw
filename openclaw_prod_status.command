#!/bin/zsh
# Krab/OpenClaw: быстрый статус боевого (stable) контура.
# Зачем: одной кнопкой проверить здоровье gateway, security и сессии.

set -euo pipefail

clear
echo "🦞 OpenClaw PROD STATUS (stable)"
echo "Профиль: default (~/.openclaw)"
echo "Дата: $(date)"
echo

openclaw --version
echo
openclaw status
echo
echo "Готово. Окно можно закрыть."
read -k 1 -s "?Нажми любую клавишу для выхода..."
