#!/bin/bash
# Переключает OpenClaw main-agent в профиль local-first одним кликом.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
fi

python3 scripts/openclaw_model_autoswitch.py --profile local-first
EXIT_CODE=$?

echo ""
if [ "$EXIT_CODE" -eq 0 ]; then
  echo "Профиль local-first применён."
  echo "Рекомендуется перезапустить OpenClaw gateway для гарантированного применения."
else
  echo "Ошибка переключения профиля local-first."
fi

echo ""
echo "Готово. Код выхода: $EXIT_CODE"
read -p "Нажми Enter для закрытия окна..."
exit $EXIT_CODE
