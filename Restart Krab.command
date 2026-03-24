#!/bin/bash
# 🔄 Restart Krab 🦀
# Назначение: legacy-restart, сведённый к канонической паре `new Stop` -> `new start`.
# Дополнительно: принудительно перекомпилирует .pyc чтобы гарантировать актуальный код.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$(cd "$DIR/.." && pwd)"
cd "$DIR"

VENV_PYTHON="$DIR/venv/bin/python"

echo "🔄 Restarting Krab..."

# Принудительная перекомпиляция ключевых модулей.
# Зачем: файлы .pyc в __pycache__ могут принадлежать другому macOS аккаунту (USER2)
# и не обновляться при правках → Краб использует устаревший код без ошибки.
if [ -x "$VENV_PYTHON" ]; then
    echo "🔨 Перекомпиляция src/ (сброс stale .pyc)..."
    "$VENV_PYTHON" -m compileall -q "$DIR/src" 2>/dev/null || true
fi

"$ROOT_DIR/new Stop Krab.command"
sleep 2
open -a Terminal "$ROOT_DIR/new start_krab.command"
echo "✅ Restart command sent."
sleep 1
