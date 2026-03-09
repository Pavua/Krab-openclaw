# MIGRATION HANDOFF 2026-03-02

## Цель документа
Этот файл фиксирует текущее состояние стабилизации Krab/OpenClaw перед миграцией в новое окно чата (anti-413), чтобы не терять прогресс.

## Текущее состояние (сводка)
1. Userbot путь чаще всего работает через `local_direct` и может автозагружать локальную модель.
2. В каналах через OpenClaw (Telegram bot/iMessage/dashboard) периодически воспроизводится `No models loaded`.
3. Есть нестабильность Telegram session lifecycle:
   - `auth key not found`,
   - `sqlite3.OperationalError: disk I/O error` при stop.
4. По LM Studio воспроизводятся:
   - `StopIteration: <EMPTY MESSAGE>`,
   - `The model has crashed without additional information`.
5. Cloud путь иногда возвращает `401 Unauthorized`.
6. По Telegram edit path воспроизводятся `MESSAGE_ID_INVALID` / `MESSAGE_EMPTY`.
7. Фото-путь периодически зависает на `👀 Разглядываю фото...`.
8. Krab Ear IPC может быть жив, но backend/агент иногда падает.

## Что уже внедрено на текущем этапе
1. Добавлен one-click экспорт handoff-пакета:
   - [scripts/export_handoff_bundle.py](/Users/pablito/Antigravity_AGENTS/Краб/scripts/export_handoff_bundle.py)
   - [Export Handoff Bundle.command](/Users/pablito/Antigravity_AGENTS/Краб/Export%20Handoff%20Bundle.command)
2. Экспорт формирует bundle в `artifacts/handoff_<timestamp>/`:
   - `runtime_snapshot.json`
   - `known_issues_matrix.md`
   - `krab_log_tail.log`
   - `openclaw_log_tail.log`
3. Подготовлены базовые документы для нового окна:
   - [docs/OPEN_ISSUES_CHECKLIST.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/OPEN_ISSUES_CHECKLIST.md)
   - [docs/NEW_CHAT_BOOTSTRAP_PROMPT.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/NEW_CHAT_BOOTSTRAP_PROMPT.md)

## Следующий приоритетный блок
1. Telegram hardening:
   - safe-stop при sqlite I/O в `userbot_bridge`,
   - защита lifecycle от параллельного stop/start.
2. Единый local autoload для каналов вне userbot.
3. EMPTY MESSAGE/model crash fallback-контур.
4. Runtime endpoint’ы:
   - `GET /api/runtime/handoff`
   - `POST /api/runtime/recover`
   - расширение `GET /api/health/lite`.

## Контрольный чек перед переходом в новый чат
1. Запустить `Export Handoff Bundle.command`.
2. Убедиться, что создан новый каталог `artifacts/handoff_<timestamp>/`.
3. Передать в новое окно:
   - `docs/NEW_CHAT_BOOTSTRAP_PROMPT.md`,
   - свежий `artifacts/handoff_<timestamp>/runtime_snapshot.json`,
   - свежий `artifacts/handoff_<timestamp>/known_issues_matrix.md`.
