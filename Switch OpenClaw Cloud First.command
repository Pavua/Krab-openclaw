#!/bin/bash
# Переключает OpenClaw main-agent в профиль cloud-first одним кликом.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
fi

python3 scripts/openclaw_model_autoswitch.py --profile cloud-first
EXIT_CODE=$?

echo ""
if [ "$EXIT_CODE" -eq 0 ]; then
  echo "Профиль cloud-first применён."
  echo "Рекомендуется перезапустить OpenClaw gateway для гарантированного применения."
else
  echo "Ошибка переключения профиля cloud-first."
fi

echo ""
echo "Готово. Код выхода: $EXIT_CODE"
read -p "Нажми Enter для закрытия окна..."
exit $EXIT_CODE
