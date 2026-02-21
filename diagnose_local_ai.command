#!/bin/zsh
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------
# Диагностика локального AI-контура Krab (LM Studio) в один клик.
# Связь с проектом:
# - Используется перед/после full_restart.command для проверки, почему Krab
#   ушел в cloud fallback (local_unavailable).
# Что делает:
# 1) Проверяет доступность LM Studio endpoint /api/v1/models.
# 2) Пытается выбрать LLM-модель и выполнить load/unload цикл.
# 3) Печатает итог: OK/FAILED + конкретная причина + действия.
# ----------------------------------------------------------------------------

set -u

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT" || exit 1

if [[ -f ".venv/bin/activate" ]]; then
  source ".venv/bin/activate"
fi

# Поддерживаем переменную LM_STUDIO_URL с /v1 и без.
LM_URL_RAW="${LM_STUDIO_URL:-http://127.0.0.1:1234/v1}"
LM_BASE="${LM_URL_RAW%/}"
LM_BASE="${LM_BASE%/v1}"
LM_BASE="${LM_BASE%/api/v1}"
MODELS_URL="${LM_BASE}/api/v1/models"
LOAD_URL="${LM_BASE}/api/v1/models/load"
UNLOAD_URL="${LM_BASE}/api/v1/models/unload"

TARGET_MODEL="${1:-${LOCAL_PREFERRED_MODEL:-}}"

printf "\n🧪 Диагностика локального AI (LM Studio)\n"
printf "• LM Studio base: %s\n" "$LM_BASE"
printf "• Models endpoint: %s\n\n" "$MODELS_URL"

MODELS_JSON="$(curl -sS -m 8 "$MODELS_URL" 2>&1)"
CURL_RC=$?

if [[ $CURL_RC -ne 0 ]]; then
  printf "❌ FAILED: LM Studio endpoint недоступен.\n"
  printf "Причина: %s\n\n" "$MODELS_JSON"
  printf "Что сделать:\n"
  printf "1) Запусти LM Studio и включи Local Server.\n"
  printf "2) Проверь порт/URL (LM_STUDIO_URL).\n"
  printf "3) Повтори диагностику.\n"
  exit 1
fi

# Парсим модели через python (без зависимости от jq).
PARSE_OUTPUT="$(python3 - <<'PY' "$MODELS_JSON" "$TARGET_MODEL"
import json
import sys

raw = sys.argv[1]
requested = (sys.argv[2] or "").strip()

try:
    payload = json.loads(raw)
except Exception as exc:
    print(f"ERROR|invalid_json|{exc}")
    raise SystemExit(0)

models = payload.get("models") if isinstance(payload, dict) else None
if not isinstance(models, list):
    print("ERROR|models_missing|Ответ не содержит списка models")
    raise SystemExit(0)

llm_ids = []
loaded_llm = []
for item in models:
    if not isinstance(item, dict):
        continue
    model_id = str(item.get("key") or item.get("id") or "").strip()
    if not model_id:
        continue
    model_type = str(item.get("type") or "llm").strip().lower()
    if model_type == "embedding" or "embedding" in model_id.lower():
        continue
    llm_ids.append(model_id)
    loaded_instances = item.get("loaded_instances") or []
    if isinstance(loaded_instances, list) and loaded_instances:
        loaded_llm.append(model_id)

selected = ""
if requested and requested in llm_ids:
    selected = requested
elif llm_ids:
    selected = llm_ids[0]

print(
    "OK|"
    + str(len(models))
    + "|"
    + str(len(llm_ids))
    + "|"
    + str(len(loaded_llm))
    + "|"
    + (selected or "")
)
PY
)"

if [[ "$PARSE_OUTPUT" == ERROR* ]]; then
  printf "❌ FAILED: не удалось разобрать ответ LM Studio.\n"
  printf "Детали: %s\n\n" "$PARSE_OUTPUT"
  printf "Что сделать:\n"
  printf "1) Открой LM Studio → Developer Logs.\n"
  printf "2) Проверь, что /api/v1/models возвращает корректный JSON.\n"
  exit 1
fi

IFS='|' read -r _ TOTAL_COUNT LLM_COUNT LOADED_LLM_COUNT SELECTED_MODEL <<< "$PARSE_OUTPUT"

printf "✅ LM Studio endpoint доступен.\n"
printf "• Всего моделей в ответе: %s\n" "$TOTAL_COUNT"
printf "• LLM-моделей: %s\n" "$LLM_COUNT"
printf "• Загруженных LLM: %s\n" "$LOADED_LLM_COUNT"

if [[ -z "$SELECTED_MODEL" ]]; then
  printf "\n❌ FAILED: не найдена LLM-модель для теста load/unload.\n"
  printf "Что сделать:\n"
  printf "1) Добавь/скачай хотя бы одну LLM-модель в LM Studio.\n"
  printf "2) Повтори запуск diagnose_local_ai.command.\n"
  exit 1
fi

printf "• Тестовая модель: %s\n\n" "$SELECTED_MODEL"

LOAD_BODY="{\"model\":\"${SELECTED_MODEL}\"}"
LOAD_RESPONSE_FILE="$(mktemp)"
LOAD_HTTP_CODE=$(curl -sS -m 30 -o "$LOAD_RESPONSE_FILE" -w "%{http_code}" -X POST "$LOAD_URL" -H 'Content-Type: application/json' -d "$LOAD_BODY" 2>/dev/null || echo "000")
LOAD_TEXT="$(cat "$LOAD_RESPONSE_FILE")"
rm -f "$LOAD_RESPONSE_FILE"

if [[ "$LOAD_HTTP_CODE" != "200" ]]; then
  printf "❌ FAILED: load модели не прошел (HTTP %s).\n" "$LOAD_HTTP_CODE"
  if [[ "$LOAD_TEXT" == *"Utility process"* ]] || [[ "$LOAD_TEXT" == *"snapshot of system resources failed"* ]]; then
    printf "Причина: LM Studio internal Utility process error.\n\n"
    printf "Что сделать:\n"
    printf "1) Полностью перезапусти LM Studio.\n"
    printf "2) Проверь системные ресурсы/перезагрузи Mac при необходимости.\n"
    printf "3) Повтори диагностику.\n"
  else
    printf "Ответ: %s\n\n" "${LOAD_TEXT:-<empty>}"
    printf "Что сделать:\n"
    printf "1) Проверь точный model_id через /api/v1/models или !model scan.\n"
    printf "2) Проверь Developer Logs в LM Studio.\n"
  fi
  exit 1
fi

UNLOAD_BODY="{\"model\":\"${SELECTED_MODEL}\"}"
UNLOAD_HTTP_CODE=$(curl -sS -m 20 -o /dev/null -w "%{http_code}" -X POST "$UNLOAD_URL" -H 'Content-Type: application/json' -d "$UNLOAD_BODY" 2>/dev/null || echo "000")

printf "✅ load прошел успешно (HTTP 200).\n"
if [[ "$UNLOAD_HTTP_CODE" == "200" ]]; then
  printf "✅ unload прошел успешно (HTTP 200).\n"
else
  printf "⚠️ unload вернул HTTP %s (не критично для проверки).\n" "$UNLOAD_HTTP_CODE"
fi

printf "\n🏁 ИТОГ: OK\n"
printf "Локальный контур LM Studio доступен, Krab может работать в local-first режиме.\n"
exit 0
