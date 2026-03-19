# Статус провайдеров — Краб (актуально на 19.03.2026)

## Конфигурация OpenClaw (`~/.openclaw/openclaw.json`)

```
Primary:    openai-codex/gpt-5.4
Fallback 1: google/gemini-3.1-pro-preview    (thinking=high)
Fallback 2: qwen-portal/coder-model           (thinking=high)
Fallback 3: google-gemini-cli/gemini-3-flash-preview (thinking=xhigh)
```

## Диагностика провайдеров

### openai-codex (GitHub Copilot OAuth)

**Статус**: ❌ Падает с 401 после 1-2 успешных сообщений
**Ошибка**: `HTTP 401: Missing scopes: model.request`
**Причина**: Copilot OAuth-токен не включает `model.request` scope. Это не квота — это уровень подписки.
**Как проверить**: `openclaw auth openai-codex` — посмотреть какие scopes в токене
**Возможные решения**:
1. Перелогиниться: `openclaw auth openai-codex --force`
2. Проверить план GitHub Copilot (нужен Copilot Enterprise или Business с API access)
3. Временно убрать из primary, поставить Gemini

### google/gemini-3.1-pro-preview (REST API)

**Статус**: ⚠️ Работает, но rate_limit при thinking=high
**Ключ**: `GOOGLE_API_KEY` = `GEMINI_API_KEY_PAID` (исправлено в 03.2026)
**Предупреждение в логах**: `Both GOOGLE_API_KEY and GEMINI_API_KEY are set. Using GOOGLE_API_KEY.` — это нормально после исправления
**Проблема**: `thinking=high` → OpenClaw делает 4 ретрая быстро → исчерпывает RPM
**Рекомендация**: Выставить `thinking=off` для этого провайдера в fallback режиме

### qwen-portal/coder-model

**Статус**: ⚠️ Работает, но rate_limit через ~4 минуты
**Аутентификация**: OAuth через `qwen-portal` profile
**Проблема**: RPM лимит portal'а низкий + `thinking=high` увеличивает потребление
**Рекомендация**: `thinking=low` или `thinking=off`

### google-gemini-cli/gemini-3-flash-preview

**Статус**: ⚠️ Работает на малых запросах, **зависает** на больших контекстах
**Аутентификация**: CLI OAuth `~/.gemini/` (независимо от API key)
**Проблема**: При контексте 50+ сообщений (~31KB) зависает без ответа
**Позиция**: Поставлен ПОСЛЕДНИМ в fallback после правки 03.2026 (был на 2-м месте)

## Рекомендуемые изменения thinking для стабильной работы

В `~/.openclaw/openclaw.json` выставить:
```json
"google/gemini-3.1-pro-preview": {"params": {"thinking": "off"}},
"qwen-portal/coder-model": {"params": {"thinking": "off"}}
```
И перезапустить OpenClaw gateway. Thinking включать вручную через `!thinking on` когда действительно нужно.
