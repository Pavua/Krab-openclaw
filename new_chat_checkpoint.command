#!/bin/bash
# -----------------------------------------------------------------------------
# Checkpoint перед переходом в новый чат Codex (защита от 413/потери контекста)
# -----------------------------------------------------------------------------

cd "$(dirname "$0")" || exit 1

if [ -x ".venv/bin/python3" ]; then
  PYTHON_BIN=".venv/bin/python3"
else
  PYTHON_BIN="python3"
fi

"$PYTHON_BIN" "scripts/new_chat_checkpoint.py"

echo ""
echo "Открой последний файл из artifacts/context_checkpoints и вставь блок [CHECKPOINT] в новый чат."
