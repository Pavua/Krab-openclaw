# Статус провайдеров — Краб (актуально на 19.03.2026)

## Конфигурация OpenClaw (`~/.openclaw/openclaw.json`)

```
Primary:    google-gemini-cli/gemini-3-flash-preview
Fallback 1: google/gemini-3.1-pro-preview
Fallback 2: qwen-portal/coder-model
Fallback 3: openai-codex/gpt-5.4
```

## Диагностика провайдеров

### google-gemini-cli/gemini-3-flash-preview

**Статус**: ✅ текущий рабочий primary  
**Проверено**:
- warmup OpenClaw проходит успешно;
- live smoke вернул `Краб на связи.`;
- owner panel `:8080` показывает этот же маршрут как рекомендованный.
**Ограничение**: на очень длинных контекстах still needs observation, но после
private message batching риск существенно ниже, чем раньше.

### google/gemini-3.1-pro-preview (REST API)

**Статус**: ⚠️ fallback-only, уходит в `rate_limit`
**Ключ**: `GOOGLE_API_KEY` = `GEMINI_API_KEY_PAID` (исправлено в 03.2026)
**Предупреждение в логах**: `Both GOOGLE_API_KEY and GEMINI_API_KEY are set. Using GOOGLE_API_KEY.` — это нормально после исправления
**Проблема**: провайдер быстро отвечает `rate_limit`, поэтому держим его только резервом
**Текущее значение thinking**: `off`

### qwen-portal/coder-model

**Статус**: ⚠️ fallback-only, уходит в `rate_limit`
**Аутентификация**: OAuth через `qwen-portal` profile
**Проблема**: portal RPM лимит остаётся низким даже при `thinking=off`

### openai-codex/gpt-5.4

**Статус**: ❌ нерабочий резерв  
**Ошибка**: `HTTP 401: Missing scopes: model.request`
**Причина**: Copilot OAuth-токен не включает `model.request` scope. Это не квота.
**Рекомендация**: не возвращать его в primary до отдельного auth-fix.

### google-antigravity/*

**Статус**: ⛔ исключён из live-цепочки  
**Причина**: в текущем окружении нет usable-квоты, поэтому в runtime его не трогаем.

## Что реально исправлено в этой сессии

- userbot больше не рвёт buffered cloud-ответ слишком рано;
- `!status` теперь показывает фактический route, а не stale model из конфига;
- несколько быстрых private-сообщений одного отправителя склеиваются в один запрос;
- runtime truth синхронизирован: gateway, health-lite и owner panel смотрят на один primary.
