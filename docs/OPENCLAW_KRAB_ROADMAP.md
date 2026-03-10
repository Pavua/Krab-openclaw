# OpenClaw Krab Roadmap

Дата актуализации: 2026-03-10
Ветка реализации: `codex/gpt54-userbot-primary`
Текущая ориентировочная готовность большого плана: `40%`

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
- [x] Покрыть ACL unit-тестами

### Этап 2. Userbot-primary и общий runtime state

- [ ] Перевести userbot на общий workspace/state без отдельной амнезии
- [ ] Зафиксировать Telegram bot как reserve transport
- [ ] Развести права owner / full / partial / guest по командам и tool-контру

### Этап 3. GPT-5.4 first routing

- [ ] Убрать `gpt-4.5-preview` как ложный primary
- [ ] Реализовать compatibility stage для `GPT-5.4`
- [ ] Настроить downgrade до последнего подтверждённого OpenClaw-compatible OpenAI/Codex ID
- [ ] Восстановить `google-antigravity` как боевой fallback
- [ ] Собрать production fallback chain

### Этап 4. Thinking / slots / quota UX

- [ ] Ввести presets `off / low / medium / high / xhigh`
- [ ] Разделить слоты `Chat / Coding / Vision / Fast`
- [x] Показывать реальные capabilities, а не hardcoded catalog
- [ ] Показывать auth/quota/runtime state из OpenClaw

### Этап 5. Browser / MCP readiness

- [ ] Довести owner browser контур до полного readiness
- [ ] Показать staged browser state в `:8080`
- [ ] Агрегировать MCP health поверх существующего runtime-реестра

### Этап 6. Безопасность и release discipline

- [ ] Закрыть открытые `groupPolicy=open` там, где есть elevated tools
- [ ] Ужесточить reserve bot и risky каналы
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

## Блокеры и риски

- Совместимость `GPT-5.4` в Codex не гарантирует совместимость того же ID в `OpenClaw openai-codex`
- В рабочем дереве уже есть незакоммиченные изменения, часть из них не относится к этой задаче
- `src/core/provider_manager.py` уже существует как незакоммиченный файл и требует аккуратной миграции, а не перезаписи

## Проверка

- [x] Unit: ACL и runtime-aware status
- [x] Unit: runtime-aware model catalog
- [ ] Integration: общий workspace/state для userbot и reserve bot
- [ ] E2E: owner message через userbot после restart
- [ ] E2E: совместимость `GPT-5.4` в OpenClaw

## Merge gate

- [ ] Ветка проходит targeted unit tests
- [ ] Ветка проходит smoke
- [ ] Есть краткий отчёт: что сделано, как проверено, что осталось
- [ ] Только после этого можно рассматривать merge в `main`
