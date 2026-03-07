#!/bin/zsh

# Этот .command-файл открывает системную страницу Chrome, где включается
# Remote Debugging для подключения Chrome DevTools MCP к вашему обычному профилю.
# Это безопаснее, чем каждый раз вспоминать внутренний chrome:// URL вручную.

set -euo pipefail

URL="chrome://inspect/#remote-debugging"

echo "🌐 Открываю Chrome на странице Remote Debugging..."

if [ -d "/Applications/Google Chrome.app" ]; then
  open -a "/Applications/Google Chrome.app" "$URL"
else
  open -a "Google Chrome" "$URL"
fi

cat <<'EOF'

Что нужно сделать дальше:
1. Включить Remote Debugging на открывшейся странице Chrome.
2. Оставить Chrome запущенным в вашем обычном профиле.
3. Перезапустить Codex или открыть новое окно Codex, чтобы MCP перечитал конфиг.

После этого Chrome DevTools MCP сможет подключаться к вашему обычному браузеру.
EOF

echo
read -r "?Нажмите Enter, чтобы закрыть окно..."
