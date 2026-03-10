# OpenClaw Krab Roadmap

Дата актуализации: 2026-03-11
Ветка реализации: `codex/gpt54-userbot-primary`
Текущая ориентировочная готовность большого плана: `76%`

## Цель

Перевести Краба на модель `GPT-5.4 first`, сделать `Telegram userbot` основным owner-каналом, оставить Telegram bot резервным контуром, убрать ложные model IDs и привязать repo/UI к реальному runtime OpenClaw без дублирования уже существующего функционала.

## Канонический source-of-truth

- [x] Зафиксировано, что runtime-истина живёт в `~/.openclaw/*`
- [x] Разделены repo-level docs и runtime persona-файлы
- [ ] Web-панель полностью привязана к live runtime truth OpenClaw
- [ ] Все owner/workspace настройки переживают restart и читаются единообразно

## Этапы

### Этап 1. Документация и базовый ACL

- [x] Создать канонический roadmap/progress документ
- [x] Переписать repo-level `AGENTS.md`, `SKILLS.md`, `TOOLS.md`
- [x] Внедрить owner/full/partial ACL для userbot
- [x] Добавить owner-only runtime-управление ACL через `!acl` / `!access`
- [x] Покрыть ACL unit-тестами

### Этап 2. Userbot-primary и общий runtime state

- [ ] Перевести userbot на общий workspace/state без отдельной амнезии (частично)
- [ ] Зафиксировать Telegram bot как reserve transport
- [ ] Развести права owner / full / partial / guest по командам и tool-контру

### Этап 3. GPT-5.4 first routing

- [ ] Убрать `gpt-4.5-preview` как ложный primary
- [ ] Реализовать compatibility stage для `GPT-5.4` (частично)
- [x] Добавить read-only diagnostics слой для runtime model routing
- [ ] Настроить downgrade до последнего подтверждённого OpenClaw-compatible OpenAI/Codex ID (частично)
- [ ] Восстановить `google-antigravity` как боевой fallback
- [ ] Собрать production fallback chain (частично)

### Этап 4. Thinking / slots / quota UX

- [ ] Ввести presets `off / low / medium / high / xhigh`
- [ ] Разделить слоты `Chat / Coding / Vision / Fast`
- [x] Показывать реальные capabilities, а не hardcoded catalog
- [ ] Показывать auth/quota/runtime state из OpenClaw
- [x] Добавить owner-oriented web ACL control для userbot в `:8080`

### Этап 5. Browser / MCP readiness

- [ ] Довести owner browser контур до полного readiness
- [ ] Показать staged browser state в `:8080`
- [ ] Агрегировать MCP health поверх существующего runtime-реестра

### Этап 6. Безопасность и release discipline

- [x] Закрыть открытые `groupPolicy=open` для Telegram reserve-контура
- [x] Ужесточить reserve bot и risky каналы для Telegram bot
- [ ] Добавить release-checklist и merge-gate для крупных этапов

## Что сделано в этой ветке

- [x] Выбрана ветка `codex/gpt54-userbot-primary`
- [x] Подтверждён hidden runtime source-of-truth OpenClaw
- [x] Подтверждено, что `gpt-4.5-preview` в runtime падает с `404`
- [x] Подтверждено, что `GPT-5.4` доступен в текущем Codex-контуре пользователя
- [x] Подтверждено отсутствие нативного Telegram userbot в upstream OpenClaw
- [x] Подтверждено, что browser relay частично жив, но browser RPC ещё не доведён
- [x] Внедрён ACL owner/full/partial
- [x] Обновлён web model catalog до runtime-aware режима
- [x] Добавлен autoswitch-профиль `production-safe` на основе runtime registry/auth/log truth
- [x] Добавлен честный canary-профиль `gpt54-canary` с `BLOCKED`, если `GPT-5.4` ещё не готов в OpenClaw runtime
- [x] Добавлен read-only compatibility probe для target-модели через текущий OpenClaw gateway
- [x] Userbot читает канонический OpenClaw workspace bundle для owner/full контура
- [x] `!remember / !recall` подключены к общей markdown-памяти `workspace-main-messaging/memory`
- [x] Владелец может выдавать и снимать `full/partial` доступ без ручного редактирования hidden ACL JSON
- [x] В web-панель `:8080` добавлен selector autoswitch-профилей
- [x] В web-панель `:8080` добавлен owner-oriented блок `Userbot ACL` с `Refresh / Grant / Revoke`
- [x] Browser smoke подтвердил, что `gpt54-canary` dry-run виден в UI и отдаёт `target_model_not_in_runtime_registry`
- [x] Live probe подтвердил: `GPT-5.4` пока `BLOCKED` в OpenClaw, потому что ещё отсутствует в runtime registry
- [x] Browser smoke подтвердил, что `Userbot ACL` в UI читает owner/full/partial и применяет `Grant / Revoke` на runtime ACL
- [x] Repair-слой runtime теперь умеет безопасно переводить `dmPolicy=allowlist` без wildcard-дыры
- [x] Repair-слой runtime теперь умеет выставлять `groupPolicy=allowlist` и выводить `groupAllowFrom` из live-конфига
- [x] Создан one-click файл `Apply Reserve Telegram Policy.command` для reserve-safe Telegram Bot
- [x] Live runtime Telegram bot переведён в reserve-safe policy: `allowlist` для DM и групп, внешние tool-guards включены

## Блокеры и риски

- Совместимость `GPT-5.4` в Codex не гарантирует совместимость того же ID в `OpenClaw openai-codex`
- В рабочем дереве уже есть незакоммиченные изменения, часть из них не относится к этой задаче
- `src/core/provider_manager.py` уже существует как незакоммиченный файл и требует аккуратной миграции, а не перезаписи
- Боевой процесс панели на `:8080` запущен без hot-reload, поэтому для появления новых endpoint'ов и секций нужен контролируемый restart runtime
- Для полного подтверждения reserve-контура ещё нужен внешний smoke самого Telegram Bot после runtime-refresh/реального сообщения

## Проверка

- [x] Unit: ACL и runtime-aware status
- [x] Unit: runtime-aware model catalog
- [x] Unit: autoswitch `production-safe` / `gpt54-canary`
- [x] Unit: compatibility probe + web endpoint для него
- [x] Unit: workspace prompt bundle + shared memory bridge userbot/OpenClaw
- [x] Unit: ACL runtime CRUD + owner command `!acl`
- [x] Unit: web ACL endpoints `/api/userbot/acl/status` и `/api/userbot/acl/update`
- [x] Unit: reserve-safe repair для `dmPolicy/groupPolicy` Telegram Bot
- [x] Browser smoke: selector autoswitch-профилей на `:8080`
- [x] Browser smoke: `Userbot ACL` на изолированном web-инстансе подтверждает `Refresh / Grant / Revoke`
- [x] Runtime repair smoke: Telegram reserve-policy применён и отражён в `~/.openclaw/openclaw.json`
- [ ] Integration: общий workspace/state для userbot и reserve bot
- [ ] E2E: owner message через userbot после restart
- [ ] E2E: emergency запрос через Telegram Bot в reserve-safe режиме
- [ ] E2E: совместимость `GPT-5.4` в OpenClaw

## Merge gate

- [x] Ветка проходит targeted unit tests
- [ ] Ветка проходит smoke
- [ ] Есть краткий отчёт: что сделано, как проверено, что осталось
- [ ] Только после этого можно рассматривать merge в `main`
