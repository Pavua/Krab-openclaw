# OpenClaw Channels/Skills Baseline (RU)

Обновлено: 2026-02-19  
Назначение: практический baseline для экосистемы Krab без дублирования OpenClaw-функционала.

## 1. Цель

1. Держать Krab как thin-client (Telegram UX + orchestration).
2. Внешние каналы и tool-skills подключать через OpenClaw.
3. Поднимать каналы поэтапно и безопасно, без ломки рабочего userbot-контура.

## 2. Что уже есть (по факту аудита)

1. OpenClaw gateway работает локально.
2. Активированы каналы: `telegram`, `discord`, `slack`, `imessage`, `signal` (Signal требует отдельной регистрации номера/daemon).
3. `whatsapp` включён, но требует линковки QR (пока не linked).
4. Часть полезных skills уже ready (включая `coding-agent`, `summarize`, `github`, `gh-issues`, `openai-whisper-api`).

## 3. Must-have skills для текущего этапа

1. `coding-agent`
2. `summarize`
3. `openai-whisper-api`
4. `github`
5. `gh-issues`

Этого достаточно, чтобы покрыть основной контур разработки/ops без каналов.

## 4. Приоритет подключения каналов

1. `imessage` (нативный macOS-контур).
2. `discord` (командные/рабочие чаты).
3. `slack` (корпоративные интеграции).
4. `signal` (приватный мобильный контур, если действительно нужен).

Важно:
1. Telegram в OpenClaw не обязателен, пока основной Telegram-контур живёт в Krab userbot.
2. Не включай OpenClaw Telegram-bot «просто так», чтобы не получить дубль поведения.

## 5. One-click команды

1. Аудит readiness:
   - `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_channels_skills_bootstrap.command`
2. Применить безопасный baseline:
   - `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_channels_skills_bootstrap.command apply`
3. Применить baseline и подключить каналы из env:
   - `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_channels_skills_bootstrap.command apply discord,slack`

Отчёты сохраняются в:
- `/Users/pablito/Antigravity_AGENTS/Краб/artifacts/openclaw_bootstrap/`

## 6. Переменные окружения для auto-enable

1. Discord:
   - `OPENCLAW_DISCORD_BOT_TOKEN`
2. Slack:
   - `OPENCLAW_SLACK_BOT_TOKEN`
   - `OPENCLAW_SLACK_APP_TOKEN` (опционально, но желательно)
3. iMessage:
   - `OPENCLAW_IMSG_CLI_PATH` (опционально, если `imsg` не в PATH)
4. Signal:
   - `OPENCLAW_SIGNAL_NUMBER`
   - `OPENCLAW_SIGNAL_HTTP_URL`
5. Telegram-bot (только при явной задаче):
   - `OPENCLAW_TELEGRAM_BOT_TOKEN`

### 6.1 Как включить iMessage и Discord пошагово
1. Заполнить `.env`:
   - `OPENCLAW_IMSG_CLI_PATH` (обычно `/opt/homebrew/bin/imsg` после `brew install imsg`).
   - `OPENCLAW_DISCORD_BOT_TOKEN`.
2. Установить бинарь `imsg` (если нет): `brew install imsg`.
3. Запустить безопасный baseline + подключить каналы:
   - `./openclaw_channels_skills_bootstrap.command apply imessage,discord`
4. Проверить: `openclaw channels status --probe --json` — iMessage/Discord должны быть в списке.

### 6.2 Signal (без конфликта с WEB_PORT=8080)
1. В `.env` использовать выделенный порт daemon:
   - `OPENCLAW_SIGNAL_HTTP_URL=http://127.0.0.1:18080`
2. Зарегистрировать номер в `signal-cli`:
   - `./openclaw_signal_register.command`
3. Запустить daemon:
   - `./openclaw_signal_daemon.command`
4. Проверить канал:
   - `openclaw channels status --probe`
5. Если видишь `probe failed`:
   - проверь, что daemon реально слушает `18080`;
   - убедись, что номер зарегистрирован (`signal-cli listAccounts`).

## 7. Safe baseline policy

Скрипт в режиме `apply` применяет только безопасные настройки:

1. `channels.telegram.groupPolicy = allowlist`
2. `channels.telegram.streamMode = partial`
3. `channels.telegram.dmPolicy = pairing`
4. `channels.telegram.enabled = false`

Это не включает новые каналы автоматически и не ломает текущий userbot-контур.

## 8. Что не делать

1. Не включать сразу несколько каналов без credentials и smoke-проверки.
2. Не менять много секций в Dashboard одновременно.
3. Не переносить channel runtime в Krab, если он уже поддерживается OpenClaw.

## 9. Связанные документы

1. `/Users/pablito/Antigravity_AGENTS/Краб/docs/OPENCLAW_DASHBOARD_PLAYBOOK_RU.md`
2. `/Users/pablito/Antigravity_AGENTS/Краб/ROADMAP_ECOSYSTEM.md`
3. `/Users/pablito/Antigravity_AGENTS/Краб/MIGRATION.md`

## 10. Быстрый troubleshooting

1. `LLM error ... "API key was reported as leaked"`:
   - это ошибка cloud-провайдера ключа, не канала мессенджера;
   - замени ключ у провайдера и перезапусти gateway/бот;
   - до ротации ключа используй local-first маршрут.
2. В Web Panel строка вида `!model set ...`:
   - должна идти через командный режим (`!model/.model`), а не как обычный prompt;
   - проверка: после команды выполни `!model` или `!model preflight`.
3. `!model scan` показывает только 1 cloud-модель или пусто:
   - проверь `openclaw models list --all --json` (истинный каталог OpenClaw);
   - если каталог нормальный, а в панели пусто — перезапусти Krab Core daemon:
     `./krab_core_daemon_stop.command` → `./krab_core_daemon_start.command`.
