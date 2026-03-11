"""
Безопасный план подключения подписок и OAuth для Krab/OpenClaw.

Зачем нужен этот файл:
- чтобы не возвращаться к опасным сторонним гайдам с зашитыми секретами;
- чтобы явно разделять официально поддерживаемые сценарии и рискованные обходы;
- чтобы следующий агент или человек сразу видел безопасную стратегию интеграции.
"""

# Безопасное подключение подписок

## Что считаем безопасным

- Использовать только официальный flow OpenClaw OAuth.
- Хранить токены только в штатном state/store OpenClaw.
- Давать минимально необходимые scope.
- Не запускать сторонние shell/python-скрипты из случайных markdown/gist/Telegram-постов.
- Не хранить refresh/access token в `/tmp`, в репозитории или в пользовательских заметках.

## Что уже подтверждено

### OpenAI / ChatGPT OAuth

Официальная документация OpenClaw описывает отдельный flow для `OpenAI Codex (ChatGPT OAuth)`:

- `openclaw onboard`
- auth choice: `openai-codex`

OpenClaw хранит токены в:

- `~/.openclaw/agents/<agentId>/agent/auth-profiles.json`

Источник:

- [OpenClaw OAuth docs](https://docs.openclaw.ai/concepts/oauth)

### ChatGPT Plus и API billing

Важно: подписка ChatGPT и OpenAI API billing официально разделены.
Это означает:

- ChatGPT Plus не равен pay-as-you-go API на `platform.openai.com`;
- нельзя обещать, что Plus автоматически заменит обычный API-ключ во всех сценариях.

Источник:

- [OpenAI Help: Billing settings in ChatGPT vs Platform](https://help.openai.com/en/articles/9039756-billing-settings-in-chatgpt-vs-platform)

### Gemini / Google AI Pro

Важно: Gemini API официально биллится через Cloud Billing / AI Studio API key.
Подписка Google AI Pro не равна обычному Gemini API billing "по умолчанию".
В установленном у нас `OpenClaw 2026.3.8` legacy provider `google-antigravity`
уже удалён; актуальный OAuth-путь для Google в runtime идёт через
`google-gemini-cli`.

Источник:

- [Gemini API Billing](https://ai.google.dev/gemini-api/docs/billing/)

## Что считаем рискованным и не используем

- Гайды, где:
  - зашит `CLIENT_SECRET`,
  - OAuth токены пишутся в `/tmp`,
  - используются внутренние endpoint Google/OpenAI,
  - предлагается широкий набор scope без явной необходимости,
  - предлагается запуск от `root` или через `docker exec` без причины.

## Практический план внедрения

### Этап 1. OpenAI OAuth через официальный flow

Цель:

- аккуратно подключить OpenAI OAuth для OpenClaw через `openclaw onboard`;
- проверить, что токены сохраняются в штатный `auth-profiles.json`;
- проверить, что маршрутизация реально использует OAuth-профиль, а не старый API-ключ.

Критерий успеха:

- `openclaw models status` показывает рабочий профиль;
- запрос через cloud-route идёт через OpenAI OAuth-профиль;
- в проекте нет новых секретов в `.env`, markdown или временных файлах.

### Этап 2. Google — только через актуальный поддерживаемый flow

Цель:

- не использовать небезопасные community-скрипты;
- не использовать legacy `google-antigravity`;
- использовать только поддерживаемый flow OpenClaw для `google-gemini-cli`
  или обычный Gemini API key.

Критерий успеха:

- включён bundled plugin `google-gemini-cli-auth`, если нужен OAuth;
- login выполняется через `openclaw models auth login --provider google-gemini-cli`;
- если нужен просто cloud fallback, используем Gemini API key без самодельного OAuth.

## Правило проекта

Если OAuth/подписка не подтверждены официальной документацией провайдера или OpenClaw:

- не внедряем это в боевой контур;
- не просим пользователя вставлять токены в скрипты из интернета;
- сначала документируем риск, потом принимаем решение.
