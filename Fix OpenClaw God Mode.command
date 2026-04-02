#!/bin/bash
# Назначение: one-click восстановление persistent God Mode для OpenClaw.
# Связи: использует scripts/openclaw_god_mode_sync.py и подходит как ручной repair
# после апдейтов OpenClaw, когда exec-права снова расходятся между runtime-файлами.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

PYTHON_BIN="$DIR/venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3 2>/dev/null)"
fi

if [ -z "${PYTHON_BIN:-}" ] || [ ! -x "${PYTHON_BIN:-}" ]; then
    echo "❌ Python не найден. Починка God Mode не выполнена."
    read -p "Press Enter to exit..."
    exit 1
fi

echo "👑 Repairing OpenClaw God Mode..."
"$PYTHON_BIN" "$DIR/scripts/openclaw_god_mode_sync.py" || {
    echo "❌ OpenClaw God Mode sync завершился с ошибкой."
    read -p "Press Enter to exit..."
    exit 1
}

echo "✅ God Mode sync finished."
read -p "Press Enter to exit..."
