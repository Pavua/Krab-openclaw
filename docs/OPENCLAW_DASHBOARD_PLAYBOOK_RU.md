# OpenClaw Dashboard Playbook (RU)

## Цель
Этот документ фиксирует безопасный и практичный режим работы с OpenClaw Dashboard в связке с Krab, чтобы:
- не ломать боевой контур;
- не дублировать функционал Krab и OpenClaw;
- быстро подключать новые каналы связи.

## 1. Что у нас разделено по ролям

### Krab (твой проект)
- Telegram userbot (через Pyrogram-сессию).
- Локальный роутинг моделей (LM Studio + cloud fallback).
- Бизнес-логика, команды `!status`, `!ops`, `!model`, автологика ответов.

### OpenClaw
- Gateway, скиллы, channel-коннекторы (Telegram bot, iMessage, Signal, Discord, Slack и т.д.).
- Dashboard/операторская панель.
- Унифицированный доступ к инструментам.

## 2. Почему в Dashboard Telegram может быть `Not configured`
Это нормально, если ты общаешься с Krab через **Telegram userbot**:
- Krab Telegram-контур живёт отдельно от OpenClaw Channels.
- В Dashboard `Channels -> Telegram` отображается только Telegram-канал самого OpenClaw (ботовый контур), а не Pyrogram userbot Krab.

## 3. Безопасный baseline для Nodes
Рекомендуемый режим для боевого контура:
- `Security`: `Allowlist`
- `Ask`: `On miss`
- `Ask fallback`: `Deny`
- `Auto-allow skill CLIs`: `Enabled` только если ты понимаешь, какие CLI разрешены

Если нужно временно дебажить:
- можно переключать `Security=Full`, но только на время диагностики;
- после теста возвращать `Allowlist`.

## 4. Что лучше не делать в Dashboard
- Не менять сразу много критичных полей в `Nodes` и `Channels` за один заход.
- Не включать `groupPolicy=open` при elevated-инструментах в группах.
- Не крутить `Rotate/Revoke` токены массово, если нет плана миграции (иначе можно отрезать работающие контуры).

## 5. Prod + Lab стратегия (рекомендуется)
- `stable` (default-профиль) = боевая работа.
- `lab` (отдельный профиль) = beta/dev эксперименты.

Для этого есть one-click файлы:
- `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_prod_status.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_lab_beta.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_ops_guard.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_prod_harden.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_channels_skills_bootstrap.command` (аудит и bootstrap каналов/скиллов)

Примечание по LAB:
- LAB теперь принудительно уходит на отдельный порт (`18890` по умолчанию), чтобы не конфликтовать с PROD (`18789`).
- Это устраняет кейс `device token mismatch` из-за пересечения портов/LaunchAgent.

## 6. Приоритет подключения каналов
Практичный порядок:
1. **iMessage** (если нужен нативный macOS-контур).
2. **Signal** (если нужен приватный мобильный канал).
3. **Discord** (для рабочих/командных чатов).
4. **Slack** (если нужен корпорат-контур и workflow).

## 7. Логика «не дублировать OpenClaw»
- В Krab оставлять оркестрацию, персону, локальные интеграции и Telegram userbot.
- Инструменты и внешние каналы по возможности подключать через OpenClaw.
- Если функционал уже есть в OpenClaw skill/channel — не писать его второй раз в Krab без причины.

## 8. Быстрая диагностика, когда UI ведёт себя странно
В терминале:
```bash
openclaw doctor --fix
openclaw status
openclaw logs --follow
```

Если видишь ошибки `invalid config`:
- не нажимай хаотично `Save/Reload` в нескольких секциях;
- зафиксируй один шаг изменения;
- проверь логи;
- откатись к backup-конфигу при необходимости.

Если запускаешь beta/lab:
- сначала подними LAB через `openclaw_lab_beta.command`;
- убедись, что в статусе LAB Dashboard = `http://127.0.0.1:18890/`;
- не запускай LAB на `18789`, если PROD уже работает.

## 9. Что уже поправлено в текущем цикле
- Обновлён OpenClaw до актуального stable.
- Убрана критичная security-позиция (`groupPolicy=open` -> `allowlist`).
- Исправлены права на `auth-profiles.json` до `600`.
- Добавлены one-click `.command` для prod/lab режима.

## 10. Runtime-конфиг, который чаще всего “слетает”
Ключевой момент: часть настроек живёт не в проекте, а в runtime OpenClaw:
- `~/.openclaw/openclaw.json`

Из-за этого после wizard/update/profile-switch можно увидеть:
- пустые fallback-модели;
- возврат дефолтных лимитов;
- неожиданные ошибки `No models loaded` / деградацию каналов.

### Быстрое восстановление (one-click)
Используй:
- `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_runtime_repair.command`

Что он гарантированно восстанавливает:
- primary: `lmstudio/local`
- fallbacks: `google/gemini-2.5-flash` -> `openai/gpt-4o-mini`
- `session.dmScope=per-channel-peer`
- безопасные лимиты токенов для каналов и local/cloud

### Autoswitch local/cloud (новое)
Если модель в LM Studio выгружена, а каналы должны продолжать отвечать без `No models loaded`, используй:
- `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_model_autoswitch.command`

Логика:
1. Если `lms ps` показывает загруженную модель -> default = `lmstudio/local`.
2. Если `lms ps` показывает, что моделей нет -> default = `google/gemini-2.5-flash`.
3. Fallback-цепочка обновляется автоматически.

Для web-панели доступны endpoint:
- `GET /api/openclaw/model-autoswitch/status`
- `POST /api/openclaw/model-autoswitch/apply`

## 11. Signal: что считается “нормальной” ошибкой и что делать
Если в логах видишь цикл:
- `Signal SSE stream error: TypeError: fetch failed`
- `Signal SSE connection lost, reconnecting...`

обычно это означает, что `signal-cli` номер не зарегистрирован (или daemon не поднят).

### Шаги восстановления Signal
1. Запусти регистрацию:
   - `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_signal_register.command`
2. После успешного `verify` запусти daemon:
   - `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_signal_daemon.command`
3. Проверь статус:
   - `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_signal_daemon_status.command`

### Если Signal вернул `429 Rate Limited`
Это внешний лимит Signal (не баг Krab/OpenClaw).
Практика:
- подождать 30-60 минут;
- повторить с новой captcha-ссылкой;
- при длительном лимите ждать до 24 часов.
