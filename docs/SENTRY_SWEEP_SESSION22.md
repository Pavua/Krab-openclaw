# Sentry Sweep — Session 22

**Дата:** 2026-04-25
**Окно:** последние 24h
**Org:** `po-zm` | Проекты: `python-fastapi`, `krab-ear-agent`, `krab-ear-backend`
**Ветка:** `fix/daily-review-20260421`

## Сводка

| Severity / категория | Кол-во |
|----------------------|--------|
| Active unresolved (всего)             | 41 |
| python-fastapi                        | 30 |
| krab-ear-agent                        | 8  |
| krab-ear-backend                      | 3  |
| NEW (firstSeen <24h)                  | 21 |
| RESOLVED today (до sweep)             | 2  |
| RESOLVED этим sweep                   | 5  |
| GROWING (count >50)                   | 2  |
| STALE clusters (требуют root-cause)   | 5+ |

## NEW regressions (firstSeen <24h)

Большинство свежих issue — кластер ~3h назад на `python-fastapi`, совпадает с
рестартами Краба и валидацией панели после деплоя session 22:

- `PYTHON-FASTAPI-5M` (×6) — `userbot_not_ready` на `/api/hooks/sentry`
- `PYTHON-FASTAPI-5R` (×2) / `5V` (×1) — `userbot_not_ready` на `/api/notify`
- `PYTHON-FASTAPI-5N` / `5S` — `router_not_configured` на `/api/assistant/query`
- `PYTHON-FASTAPI-5Q` (×3) — `voice_channel_not_initialized` на `/v1/voice/message`
- `PYTHON-FASTAPI-5P` — `chat_session_clear_not_supported`
- `PYTHON-FASTAPI-5T` — `Request timed out` на `/api/notify`
- `KRAB-EAR-AGENT-3..8` — `App Hanging ≥2000ms` (×25 events суммарно; вероятно UI-thread blocking в KrabEar)
- `KRAB-EAR-BACKEND-2/3` — `BrokenPipeError` в `_handle_connection`

Это **не regressions** от наших фиксов — это последствия рестартов. После
`49857a4 rate-limit /api/krab/restart_userbot (5min)` поток рестартов прекращён,
но запросы во время startup-окна пока не gated → возвращают `userbot_not_ready`.

## GROWING (накопленные, count >50)

| ShortId            | Title                                       | Count | Action |
|--------------------|---------------------------------------------|-------|--------|
| `PYTHON-FASTAPI-10`  | `userbot_not_ready /api/notify`            | 80    | требует gate `503 + Retry-After` или авторазогрев очереди |
| `PYTHON-FASTAPI-5A`  | `database is locked` (pyrogram update_usernames) | 58 | sqlite WAL + busy_timeout, либо сериализатор записей в pyrogram session |

## RESOLVED — что мы реально закрыли

### До sweep (2):
- `PYTHON-FASTAPI-5K` — `ChatWriteForbidden` в `_worker` (Sessions 21).
- `PYTHON-FASTAPI-5J` — `E2EKrabTestError` (тестовый event).

### Этим sweep (5):
- `PYTHON-FASTAPI-58` — `No module named 'src.core.trusted_guests'` (модуль удалён, 1 event, не повторится).
- `PYTHON-FASTAPI-57` — `MESSAGE_TOO_LONG` (Telegram limit, корректное поведение, 1 event).
- `PYTHON-FASTAPI-56` / `PYTHON-FASTAPI-55` — `openclaw_stream_connect_error` (transient gateway, 1 event каждый).
- `KRAB-EAR-BACKEND-1` — `Krab Ear test event from Claude Code session` (manual smoke).

## Cross-reference: commits → Sentry impact

| Commit (session 21-22)                              | Ожидаемый эффект                  | Подтверждение |
|------------------------------------------------------|-----------------------------------|---------------|
| W31 hotfix (phantom_action / acl_denied)            | закрытие issue                    | OK — отсутствуют в активных |
| W32 (`!status` spam)                                 | закрытие issue                    | OK — не повторяются |
| C2 (Sentry webhook 401)                              | закрытие 401-cluster              | OK — нет 401 в активных |
| `49857a4` rate-limit `/api/krab/restart_userbot`    | стоп restart loop                 | OK — событий рестартов нет; косвенно `5M-5V` ещё долетают |
| `6fc969a` `/api/dashboard/summary` async subprocess | конец 504 timeouts                | OK — нет 504-related issue |
| codex_quota fix                                      | корректный billing                | not Sentry-tracked |

## Top-3 priority для следующей wave

1. **`PYTHON-FASTAPI-10` `userbot_not_ready` (80 events).** Добавить gate
   middleware на `/api/notify`, `/api/hooks/sentry`, `/api/assistant/query` —
   возвращать `503 + Retry-After: 5` пока `userbot_bridge.is_ready` не True,
   вместо raise HTTPException → Sentry. Иначе всплеск повторяется при каждом
   рестарте (cluster `5M-5V`).
2. **`PYTHON-FASTAPI-5A/5B/5C` `database is locked`** в pyrogram session
   (`update_usernames`/`update_peers`). Включить `PRAGMA journal_mode=WAL` и
   `busy_timeout=5000` для pyrogram-сессии, либо сериализовать записи через
   single-writer task. Cluster прирастает (61 events суммарно).
3. **`KRAB-EAR-AGENT-3..8` App Hanging ≥2000ms.** Профилировать main-thread —
   вероятно, синхронный network call в UI loop. Перенести на background queue.
   Свежий cluster (25 events за 4h), 1 user — но это owner. Track D priority.

## STALE / отложено

- `PYTHON-FASTAPI-4W/4Y` — `memory_indexer_embed_failed` SQLite thread
  (объект создан в одном thread, используется в другом). Известно с Wave 28-29
  (sqlite-vec malformed). Дождаться Memory Phase 2.
- `PYTHON-FASTAPI-4T/4S` — asyncio `read() called while another coroutine`.
  Backlog item, не blocker.
- `PYTHON-FASTAPI-2M/2T` — `router_not_configured` / `chat_session_clear_not_supported`.
  Исторические, нужны бизнес-фиксы в OpenClaw scopes.
- `PYTHON-FASTAPI-5D` — `_process_message_serialized() got '_forward_batch_prompt'`.
  Регрессия kwargs в `_process_forward_batch`; завести отдельный issue в backlog.

## Ссылки

- Active: https://po-zm.sentry.io/issues/?query=is%3Aunresolved
- New 24h: https://po-zm.sentry.io/issues/?query=firstSeen%3A-24h
- Resolved 24h: https://po-zm.sentry.io/issues/?query=is%3Aresolved+lastSeen%3A-24h
