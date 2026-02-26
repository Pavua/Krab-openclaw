# OpenClaw Channels & Skills Bootstrap Report

- Время: `2026-02-19T20:01:37.067099+01:00`
- Профиль OpenClaw: `main`
- OpenClaw: `2026.2.19-2`
- macOS: `macOS-26.4-arm64-arm-64bit-Mach-O`
- Архитектура: `arm64`
- RAM: `36.0 GiB`
- .env загружено переменных: `87`

## Сводка skills check
- total: `56`
- eligible: `28`
- missingRequirements: `28`

## Must-have навыки для Krab/OpenClaw
- ✅ `coding-agent` ready
- ✅ `summarize` ready
- ✅ `openai-whisper` ready
- ✅ `openai-whisper-api` ready
- ✅ `github` ready
- ✅ `gh-issues` ready
- ✅ `weather` ready
- ✅ `imsg` ready
- ✅ `discord` ready
- ✅ `slack` ready
- ⚠️ `voice-call` missing (config: plugins.entries.voice-call.enabled)
- ✅ `bluebubbles` ready

## Каналы (приоритет и readiness)
- ✅ `telegram` | state: `enabled` | env hints: `OPENCLAW_TELEGRAM_BOT_TOKEN`
- ✅ `imessage` | state: `enabled` | env hints: `OPENCLAW_IMSG_CLI_PATH`
- ✅ `discord` | state: `enabled` | env hints: `OPENCLAW_DISCORD_BOT_TOKEN`
- ✅ `slack` | state: `enabled` | env hints: `OPENCLAW_SLACK_BOT_TOKEN, OPENCLAW_SLACK_APP_TOKEN`
- ✅ `signal` | state: `enabled` | env hints: `OPENCLAW_SIGNAL_NUMBER, OPENCLAW_SIGNAL_HTTP_URL`

## Текущие каналы gateway
- `telegram`
- `whatsapp`
- `discord`
- `slack`
- `signal`
- `imessage`
- `bluebubbles`

## Browser (OpenClaw)
- enabled: `True`
- running: `False`
- profile: `chrome`
- detected browser: `chrome`
- cdp url: `http://127.0.0.1:18792`
- ⚠️ Browser relay не привязан к вкладке. Открой Chrome и кликни иконку OpenClaw extension.

## Brave Search
- ✅ API key рабочий (HTTP `200`, results `1`)

## Доступные модели OpenClaw
- `google/gemini-2.5-flash-lite` (default, cloud)

## Что критично закрыть дальше
- ✅ Все must-have навыки готовы.

## Brew-пакеты, которые помогут закрыть часть missing requirements
- `brew install wacli`

## Рекомендуемый порядок включения каналов
- `imessage` -> `discord` -> `slack` -> `signal`
- Telegram bot в OpenClaw включать только при явной задаче (у тебя основной Telegram-контур = Pyrogram userbot Krab).

## Следующие команды
- Аудит: `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_channels_skills_bootstrap.command`
- Безопасный baseline: `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_channels_skills_bootstrap.command apply`
- Подключить каналы из env: `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_channels_skills_bootstrap.command apply discord,slack`
