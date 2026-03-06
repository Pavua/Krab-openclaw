#!/bin/bash
# =============================================================================
# One-click запуск autoswitch локальной/облачной модели OpenClaw.
# Зачем: быстрый ручной триггер логики autoswitch без запуска всего runtime.
# Связь: использует scripts/openclaw_model_autoswitch.py.
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

# Подхватываем переменные окружения проекта, если файл есть.
if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
elif [[ -x "venv/bin/python" ]]; then
  PYTHON_BIN="venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

if [[ -z "${PYTHON_BIN:-}" ]]; then
  echo "❌ Не найден Python (python3/python)."
  exit 2
fi

# Если выбранный python без pip (частый кейс в урезанном venv), переключаемся
# на системный python3, чтобы не ломать one-click запуск.
SYSTEM_PYTHON="$(command -v python3 || command -v python || true)"
if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
  if [[ -n "$SYSTEM_PYTHON" && "$SYSTEM_PYTHON" != "$PYTHON_BIN" ]]; then
    echo "ℹ️ Текущий venv без pip, переключаюсь на системный Python: $SYSTEM_PYTHON"
    PYTHON_BIN="$SYSTEM_PYTHON"
  fi
fi

# В старых окружениях dotenv может отсутствовать — доставляем зависимость.
if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
from dotenv import load_dotenv
print(load_dotenv is not None)
PY
then
  echo "📦 Не найден python-dotenv, устанавливаю..."
  if ! "$PYTHON_BIN" -m pip install --quiet python-dotenv; then
    echo "❌ Не удалось установить python-dotenv в текущее окружение."
    echo "   Попробуй вручную: $PYTHON_BIN -m pip install python-dotenv"
    exit 3
  fi
fi

echo "🔁 OpenClaw model autoswitch (single pass)"
"$PYTHON_BIN" "$ROOT_DIR/scripts/openclaw_model_autoswitch.py" "$@"
EXIT_CODE=$?

echo
if [[ "$EXIT_CODE" -eq 0 ]]; then
  echo "✅ Готово."
else
  echo "❌ Завершено с ошибкой: $EXIT_CODE"
fi

exit "$EXIT_CODE"
