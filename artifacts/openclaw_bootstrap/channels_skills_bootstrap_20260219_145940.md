# OpenClaw Channels & Skills Bootstrap Report

- Время: `2026-02-19T14:59:40.451683+01:00`
- Профиль OpenClaw: `main`
- OpenClaw: `2026.2.17`
- macOS: `macOS-26.4-arm64-arm-64bit-Mach-O`
- Архитектура: `arm64`
- RAM: `36.0 GiB`

## Сводка skills check
- total: `56`
- eligible: `25`
- missingRequirements: `31`

## Must-have навыки для Krab/OpenClaw
- ✅ `coding-agent` ready
- ✅ `summarize` ready
- ✅ `openai-whisper` ready
- ✅ `openai-whisper-api` ready
- ✅ `github` ready
- ✅ `gh-issues` ready
- ✅ `weather` ready
- ⚠️ `imsg` missing (bins: imsg)
- ⚠️ `discord` missing (config: channels.discord.token)
- ⚠️ `slack` missing (config: channels.slack)
- ⚠️ `voice-call` missing (config: plugins.entries.voice-call.enabled)
- ✅ `bluebubbles` ready

## Каналы (приоритет и readiness)
- 🟡 `telegram` | state: `disabled` | env hints: `OPENCLAW_TELEGRAM_BOT_TOKEN`
- ⚪ `imessage` | state: `unconfigured` | env hints: `OPENCLAW_IMSG_CLI_PATH`
- ⚪ `discord` | state: `unconfigured` | env hints: `OPENCLAW_DISCORD_BOT_TOKEN`
- ⚪ `slack` | state: `unconfigured` | env hints: `OPENCLAW_SLACK_BOT_TOKEN, OPENCLAW_SLACK_APP_TOKEN`
- ⚪ `signal` | state: `unconfigured` | env hints: `OPENCLAW_SIGNAL_NUMBER, OPENCLAW_SIGNAL_HTTP_URL`

## Текущие каналы gateway
- `(нет активных каналов)`

## Доступные модели OpenClaw
- `google/gemini-2.5-flash-lite` (default, cloud)

## Применение safe-baseline (--apply-safe)
✅ channels.telegram.groupPolicy="allowlist"
✅ channels.telegram.streamMode="partial"
✅ channels.telegram.dmPolicy="pairing"
✅ channels.telegram.enabled=false

## Что критично закрыть дальше
- ✅ Все must-have навыки готовы.

## Brew-пакеты, которые помогут закрыть часть missing requirements
- `brew install imsg wacli`

## Рекомендуемый порядок включения каналов
- `imessage` -> `discord` -> `slack` -> `signal`
- Telegram bot в OpenClaw включать только при явной задаче (у тебя основной Telegram-контур = Pyrogram userbot Krab).

## Следующие команды
- Аудит: `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_channels_skills_bootstrap.command`
- Безопасный baseline: `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_channels_skills_bootstrap.command apply`
- Подключить каналы из env: `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_channels_skills_bootstrap.command apply discord,slack`
