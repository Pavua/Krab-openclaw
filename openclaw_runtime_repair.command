#!/bin/zsh
# -----------------------------------------------------------------------------
# OpenClaw Runtime Repair (one-click) для Krab экосистемы
# -----------------------------------------------------------------------------
# Что это:
# Кнопка быстрого восстановления критичных runtime-настроек OpenClaw, которые
# могут сбрасываться после wizard/update/profile-переключений.
#
# Зачем:
# Чтобы каналы (Telegram/WhatsApp/iMessage) не падали при деградации локальной
# модели и корректно уходили в cloud fallback.
#
# Что делает:
# 1) Устанавливает primary модель: lmstudio/local.
# 2) Восстанавливает fallback-цепочку: Google -> OpenAI.
# 3) Фиксирует безопасные лимиты токенов и DM-изоляцию по каналу/контакту.
# 4) Печатает итоговый status + probe по каналам.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if ! command -v openclaw >/dev/null 2>&1; then
  echo "❌ openclaw CLI не найден в PATH."
  exit 1
fi

echo "🛠️ Восстанавливаю runtime-конфиг OpenClaw..."

# 1) Primary + fallbacks
openclaw models set lmstudio/local >/dev/null
openclaw models fallbacks clear >/dev/null
openclaw models fallbacks add google/gemini-2.5-flash >/dev/null
openclaw models fallbacks add openai/gpt-4o-mini >/dev/null

# 2) Изоляция сессий и лимиты для более стабильных ответов
openclaw config set session.dmScope per-channel-peer >/dev/null
# Безопасный минимум для текущего набора моделей и fallback-цепочки.
# Важно: 12000 вызывает массовые ошибки "Minimum is 16000" в OpenClaw.
openclaw config set agents.defaults.contextTokens 20000 >/dev/null

# Локальные модели: жёстко ограничиваем maxTokens для ответов в каналах.
LM_COUNT="$(openclaw config get models.providers.lmstudio.models 2>/dev/null | jq 'length' 2>/dev/null || echo 0)"
if [[ "$LM_COUNT" =~ '^[0-9]+$' ]]; then
  for ((i=0; i<LM_COUNT; i++)); do
    openclaw config set "models.providers.lmstudio.models.$i.maxTokens" 700 >/dev/null || true
  done
fi

# Cloud-модели: ограничиваем maxTokens по всем элементам, чтобы не было
# "простыней" ответа и перегруза каналов.
GOOGLE_COUNT="$(openclaw config get models.providers.google.models 2>/dev/null | jq 'length' 2>/dev/null || echo 0)"
if [[ "$GOOGLE_COUNT" =~ '^[0-9]+$' ]]; then
  for ((i=0; i<GOOGLE_COUNT; i++)); do
    openclaw config set "models.providers.google.models.$i.maxTokens" 900 >/dev/null || true
  done
fi

OPENAI_COUNT="$(openclaw config get models.providers.openai.models 2>/dev/null | jq 'length' 2>/dev/null || echo 0)"
if [[ "$OPENAI_COUNT" =~ '^[0-9]+$' ]]; then
  for ((i=0; i<OPENAI_COUNT; i++)); do
    openclaw config set "models.providers.openai.models.$i.maxTokens" 900 >/dev/null || true
  done
fi

openclaw config set channels.whatsapp.textChunkLimit 1200 >/dev/null
openclaw config set channels.whatsapp.chunkMode newline >/dev/null
openclaw config set channels.imessage.historyLimit 8 >/dev/null

echo
echo "✅ Runtime-конфиг восстановлен."
echo
echo "Primary/Fallback:"
openclaw models status --json | jq '{resolvedDefault, fallbacks}'
echo
echo "Каналы (probe):"
openclaw channels status --probe || true
echo
echo "Готово."
