#!/bin/zsh
set -euo pipefail

# Файл: new Fix Obsidian Terminal.command
# Назначение:
# - Чинит плагин Terminal в Obsidian, когда он подхватывает системный Python 3.9
#   и падает с ошибкой "cannot import name 'Self' from typing".
# - Выставляет безопасный профиль по умолчанию (zsh integrated).
# - Прописывает абсолютный путь к Python 3.10+ для всех integrated-профилей.
# - Подправляет сохранённые вкладки в workspace.json, чтобы старые терминалы не падали.

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "🔧 Fix Obsidian Terminal"

autodetect_python() {
  if [[ -x "/opt/homebrew/bin/python3" ]]; then
    echo "/opt/homebrew/bin/python3"
    return
  fi
  if [[ -x "/opt/homebrew/anaconda3/bin/python3" ]]; then
    echo "/opt/homebrew/anaconda3/bin/python3"
    return
  fi
  command -v python3
}

PY_BIN="$(autodetect_python)"
PY_VER="$($PY_BIN -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
echo "🐍 Python: $PY_BIN (v$PY_VER)"

# Собираем все data.json плагина terminal во всех vault.
TERMINAL_CONFIGS=()
while IFS= read -r line; do
  TERMINAL_CONFIGS+=("$line")
done < <(find "$HOME/Documents" "$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents" -type f -path "*/.obsidian/plugins/terminal/data.json" 2>/dev/null)

if [[ ${#TERMINAL_CONFIGS[@]} -eq 0 ]]; then
  echo "⚠️ Не найдено ни одного terminal/data.json."
  echo "Открой vault в Obsidian и установи плагин Terminal, затем запусти этот файл снова."
  exit 0
fi

PATCHED=0
for cfg in "${TERMINAL_CONFIGS[@]}"; do
  vault_obsidian_dir="$(dirname "$(dirname "$(dirname "$cfg")")")"
  workspace_json="$vault_obsidian_dir/workspace.json"

  "$PY_BIN" - <<'PY' "$cfg" "$workspace_json" "$PY_BIN"
import json
import pathlib
import sys

cfg = pathlib.Path(sys.argv[1])
workspace = pathlib.Path(sys.argv[2])
py_bin = sys.argv[3]

obj = json.loads(cfg.read_text())
profiles = obj.get("profiles", {})

for key in ("darwinIntegratedDefault", "linuxIntegratedDefault", "win32IntegratedDefault"):
    p = profiles.get(key)
    if isinstance(p, dict):
        p["pythonExecutable"] = py_bin

# Защита от случайного открытия проблемного developerConsole как дефолта.
obj["defaultProfile"] = "darwinIntegratedDefault"
cfg.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")

# Патчим уже сохранённые вкладки терминала.
if workspace.exists():
    ws = json.loads(workspace.read_text())

    def walk(x):
        if isinstance(x, dict):
            if x.get("type") == "integrated":
                x["pythonExecutable"] = py_bin
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(ws)
    workspace.write_text(json.dumps(ws, ensure_ascii=False, indent=2) + "\n")
PY

  echo "✅ patched: $cfg"
  PATCHED=$((PATCHED + 1))
done

echo ""
echo "Готово: patched $PATCHED terminal config(s)."

echo ""
if command -v claude >/dev/null 2>&1; then
  echo "🤖 claude: $(claude --version 2>/dev/null || echo installed)"
else
  echo "⚠️ claude не найден в PATH. Установи: npm i -g @anthropic-ai/claude-code"
fi

if command -v gemini >/dev/null 2>&1; then
  echo "💠 gemini: $(gemini --version 2>/dev/null || echo installed)"
else
  echo "⚠️ gemini не найден в PATH."
fi

echo ""
echo "Следующий шаг: полностью перезапусти Obsidian и открывай команду"
echo "Terminal: Open terminal (default profile), а НЕ Developer console."
