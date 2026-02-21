#!/bin/zsh
# -----------------------------------------------------------------------------
# OpenClaw Model Keys Sync (one-click)
# -----------------------------------------------------------------------------
# Зачем:
# - Иногда OpenClaw продолжает брать старые ключи из ~/.openclaw/.../models.json,
#   даже после обновления .env в проекте.
# - Этот скрипт синхронизирует ключи provider'ов из локального .env в models.json
#   и мягко перезапускает gateway.
#
# Что делает:
# 1) Читает GEMINI_API_KEY и OPENAI_API_KEY из .env (если есть).
# 2) Обновляет providers.google.apiKey / providers.openai.apiKey в
#    ~/.openclaw/agents/main/agent/models.json.
# 3) Перезапускает gateway через `openclaw gateway restart`.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

ENV_FILE="$ROOT_DIR/.env"
MODELS_JSON="$HOME/.openclaw/agents/main/agent/models.json"

if [[ ! -f "$MODELS_JSON" ]]; then
  echo "❌ Не найден models.json: $MODELS_JSON"
  echo "Сначала инициализируй OpenClaw (gateway/setup)."
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

if [[ -z "${GEMINI_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
  echo "⚠️ В .env не найдены GEMINI_API_KEY/OPENAI_API_KEY."
  echo "Синхронизировать нечего."
  exit 0
fi

python3 - <<'PY'
import json
import os
from pathlib import Path

models_path = Path.home() / ".openclaw/agents/main/agent/models.json"
data = json.loads(models_path.read_text(encoding="utf-8"))
providers = data.setdefault("providers", {})

updated = []

gemini = os.environ.get("GEMINI_API_KEY", "").strip()
if gemini:
    google = providers.setdefault("google", {})
    if google.get("apiKey") != gemini:
        google["apiKey"] = gemini
        updated.append("google")

openai = os.environ.get("OPENAI_API_KEY", "").strip()
if openai:
    oa = providers.setdefault("openai", {})
    if oa.get("apiKey") != openai:
        oa["apiKey"] = openai
        updated.append("openai")

if updated:
    models_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print("updated_providers=", ",".join(updated) if updated else "none")
PY

if command -v openclaw >/dev/null 2>&1; then
  echo "⏳ Перезапускаю OpenClaw gateway..."
  openclaw gateway restart || true
  echo "✅ Готово: синхронизация выполнена."
  echo "Проверь статус: openclaw models status --json"
else
  echo "⚠️ openclaw CLI не найден в PATH. Ключи обновлены только в models.json."
fi

echo
read -k "_ANY?Нажми любую клавишу для закрытия..."
echo
