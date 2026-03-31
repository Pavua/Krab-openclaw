# Transition Bundle — 31.03.2026

Эту папку можно целиком прикладывать в новый чат или использовать как handoff-пакет для `USER3`.

## Что внутри

1. `00_README_RU.md`
   - этот индекс и порядок чтения
2. `01_CURRENT_STATE_RU.md`
   - текущий truth snapshot по runtime, MCP, branch, PR и рискам
3. `02_NEW_CHAT_PROMPT_RU.md`
   - готовый prompt для нового диалога
4. `03_USER3_BOOTSTRAP_RU.md`
   - что запускать на `USER3`, если продолжение будет с другой учётки
5. `04_GIT_AND_EVIDENCE_RU.md`
   - branch, commit, PR, проверки и где лежат основные evidence

## Порядок чтения для нового агента

1. `01_CURRENT_STATE_RU.md`
2. `02_NEW_CHAT_PROMPT_RU.md`
3. `04_GIT_AND_EVIDENCE_RU.md`

Если работа идёт с `USER3`, дополнительно:

4. `03_USER3_BOOTSTRAP_RU.md`

## Канонические связанные файлы вне bundle

- `docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md`
- `docs/handoff/2026-03-31_telegram_runtime_recovery_and_mcp_status.md`
- `docs/handoff/2026-03-31_next_chat_or_user3_bootstrap_ru.md`

## Что уже доведено до логичной точки

- Telegram userbot restart recovery усилен и проверен.
- Live restart через web API проходит.
- Health truth теперь показывает `telegram_userbot_client_connected`.
- Подготовлена отдельная ветка и draft PR под recovery-блок.
- Подготовлен handoff для нового чата и для `USER3`.

## Что осознанно НЕ добивалось в этой сессии

- Полный merge в `main`
- Полное устранение любых будущих vendor-level race внутри Pyrogram
- Автоматическое исправление прав доступа в домашнем каталоге `USER3`
- Принудительное перемонтирование Telegram MCP tools в уже открытом текущем чате Codex

Это уже разумно добивать в новом чате, чтобы не раздувать текущий контекст.
