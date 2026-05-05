#!/usr/bin/env bash
# Wave 24-A: Helper для login дополнительного codex аккаунта в изолированный CODEX_HOME.
#
# Использование:
#   ./scripts/setup_codex_account.sh account2
#   ./scripts/setup_codex_account.sh account3
#
# После выполнения auth сохраняется в ~/.codex_accounts/<name>/auth.json
# и аккаунт автоматически подхватывается codex_account_rotator.py.

set -euo pipefail

ACCOUNT="${1:?Использование: $0 <account_name>  (например: account2 или account3)}"
HOME_DIR="$HOME/.codex_accounts/$ACCOUNT"

mkdir -p "$HOME_DIR"

echo "========================================"
echo " Codex Multi-Account Login"
echo "========================================"
echo " Аккаунт : $ACCOUNT"
echo " CODEX_HOME: $HOME_DIR"
echo "========================================"
echo ""
echo "Запускаем 'codex login' для аккаунта '$ACCOUNT'."
echo "Браузер откроется для 2FA — войдите в нужный ChatGPT Plus аккаунт."
echo ""

CODEX_HOME="$HOME_DIR" codex login

echo ""
if [ -f "$HOME_DIR/auth.json" ]; then
    echo "Готово! Аккаунт '$ACCOUNT' залогинен."
    echo "auth.json создан: $HOME_DIR/auth.json"
    echo ""
    echo "Краб автоматически будет ротировать этот аккаунт при следующем старте."
else
    echo "Предупреждение: auth.json не найден в $HOME_DIR"
    echo "Возможно login не завершился. Повторите попытку."
    exit 1
fi
