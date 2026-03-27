#!/bin/zsh
# One-click логин второго Telegram аккаунта для отдельного MCP session.

set -euo pipefail

ROOT_DIR="/Users/pablito/Antigravity_AGENTS/Краб"
cd "$ROOT_DIR"

default_session="krab_test"
echo "Введите базовое имя session для второго Telegram MCP аккаунта."
echo "Например: krab_test"
printf "Session name [%s]: " "$default_session"
read -r session_name
session_name="${session_name:-$default_session}"

echo
echo "Сейчас откроется интерактивная авторизация Pyrogram."
echo "Введи номер телефона, код Telegram и при необходимости 2FA именно для ВТОРОГО аккаунта."
echo

export TELEGRAM_SESSION_NAME="$session_name"
if [[ -x "$ROOT_DIR/venv/bin/python" ]]; then
  "$ROOT_DIR/venv/bin/python" mcp-servers/telegram/auth_setup.py
else
  python3 mcp-servers/telegram/auth_setup.py
fi

echo
echo "Готово."
echo "Session alias: $session_name"
echo "Файл сессии: ~/.krab_mcp_sessions/${session_name}_mcp.session"
echo
echo "Следующий шаг: добавить второй MCP entry в ~/.codex/config.toml по инструкции из docs/SECOND_TELEGRAM_MCP_SETUP_RU.md"
