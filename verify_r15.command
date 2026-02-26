#!/bin/zsh
cd "$(dirname "$0")"
echo "🚀 Запуск верификации Sprint R15 Backend Stability..."
python3 -m pytest tests/test_r15_* -v
echo "\n✅ Проверка завершена. Нажмите любую клавишу для выхода."
read -n 1
