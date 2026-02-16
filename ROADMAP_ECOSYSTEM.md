# ROADMAP_ECOSYSTEM.md

Обновлено: 2026-02-16
Статус: In Progress

## Цель
Синхронизировать развитие `Krab`, `OpenClaw`, `Krab Ear`, `Krab Voice Gateway` без жёсткой связки рантаймов и без дублей функционала.

## Принцип интеграции
- `Krab` + `OpenClaw`: единый логический контур UX + reasoning/tool runtime.
- `Krab Ear`: отдельный сервис, интеграция только по API.
- `Krab Voice Gateway`: отдельный сервис, интеграция только по API.

## Фазы

### E1. Core Contracts (P0)
- [x] Единые health/check endpoints.
- [x] Единая модель route/context snapshot для Telegram.
- [x] Fallback local -> cloud при stream-failure.

### E2. Queue/Context Reliability (P0)
- [x] Per-chat FIFO очередь для burst-сообщений.
- [x] Явный forward/reply/author контекст.
- [x] Runtime policy-команды (`!policy`, `!ctx`).

### E3. Feedback/Mood Layer (P0)
- [x] Реакции Telegram как weak-signal (reaction store).
- [x] Chat mood profile (rolling).
- [x] Авто-реакции с kill-switch и rate-limit.

### E4. Voice/Ear Interop (P1)
- [ ] Контракт событий STT/TTS и унифицированные payload schema.
- [ ] E2E сценарий `voice input -> chat reasoning -> voice output`.
- [ ] API-bridge для удалённого режима (вне локальной сети).

### E5. iOS Companion Track (P1)
- [ ] Отдельный roadmap для PSTN-перевода на iOS.
- [ ] Определить target-архитектуру: on-device / relay / hybrid.
- [ ] Прототип remote call translation workflow.

## KPI
- Не теряются сообщения в burst-сценариях.
- Нет циклических «простыней» в финальных ответах.
- Ошибки local stream не ломают UX благодаря cloud fallback.
- Реакции и mood улучшают тональность, не ломая базовую policy.
