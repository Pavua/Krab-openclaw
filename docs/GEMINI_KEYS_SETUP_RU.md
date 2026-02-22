# Настройка Gemini Free→Paid Fallback (Krab/OpenClaw)

## Что уже поддерживается в проекте

- `GEMINI_API_KEY_FREE` — приоритетный ключ (бесплатный проект без billing).
- `GEMINI_API_KEY_PAID` — fallback-ключ (платный проект с billing).
- Если free-квота исчерпана, роутер пытается переключиться на `paid`.
- Для обратной совместимости остаются:
  - `GEMINI_API_KEY`
  - `GOOGLE_API_KEY`

## Куда вставлять ключи

Открой файл `/Users/pablito/Antigravity_AGENTS/Краб/.env` и вставь:

```dotenv
# Gemini free tier (проект без billing)
GEMINI_API_KEY_FREE=AIzaSy...FREE

# Gemini paid tier (проект с billing)
GEMINI_API_KEY_PAID=AIzaSy...PAID

# Legacy поля (можно оставить пустыми, если используешь free/paid)
GEMINI_API_KEY=
GOOGLE_API_KEY=
```

## Проверка ключей одним кликом

Запусти:

- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/verify_cloud_keys.command`

Скрипт покажет статус:

- `google: OK/FAIL`
- `openai: OK/FAIL`
- источник ключа (`key_source`)
- тип ошибки (`error_code`)

## Частые причины, почему Gemini не отвечает

1. В Google Cloud не включён `Generative Language API`.
2. Ключ создан в другом проекте, чем ты ожидаешь.
3. Ключ ограничен не тем API.
4. Ключ помечен как leaked (403 `PERMISSION_DENIED` + `reported as leaked`).
5. После замены ключа не перезапущено ядро Krab.

## Быстрый рестарт после обновления `.env`

- Двойной клик: `/Users/pablito/Antigravity_AGENTS/Краб/restart_core_hard.command`

## Важно про OpenAI

Если диагностика показывает `openai -> api_key_invalid`, этот ключ нужно пересоздать в OpenAI и заменить в `.env`:

```dotenv
OPENAI_API_KEY=sk-...
```
