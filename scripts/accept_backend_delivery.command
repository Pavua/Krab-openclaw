#!/bin/zsh
# Приёмка backend-поставки от внешних агентов (one-click).
#
# Проверяет:
# 1) отсутствие конфликтов ownership;
# 2) целевые backend-тесты (voice gateway + telegram/moderation).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "🧭 Шаг 1/2: Проверка ownership-overlap..."
python3 scripts/check_workstream_overlap.py

echo
echo "🧪 Шаг 2/2: Запуск целевых backend-тестов..."
if ! pytest -q \
  tests/test_tools_voice_gateway_errors.py \
  tests/test_voice_gateway_hardening.py \
  tests/test_telegram_control.py \
  tests/test_telegram_summary_service.py \
  tests/test_group_moderation_engine.py
then
  echo
  echo "❌ Backend-приёмка не пройдена."
  echo "   Проверь вывод pytest выше и исправь регрессию в backend-поставке."
  exit 1
fi

echo
echo "✅ Backend-приёмка завершена успешно."
