# Wave 35-A: Sentry events analysis — 2026-05-05/06

Сгенерировано: 2026-05-06. Анализ после Session 38 waves.
Источник: Sentry org `po-zm` (de.sentry.io), период 24h.

---

## Summary

| Показатель | Значение |
|---|---|
| Всего unresolved issues (active 24h) | **37** |
| Уникальных групп (lastSeen:-24h) | **24** |
| Новых issues (firstSeen:-24h) | **12** |
| Проекты | python-fastapi (25), krab-ear-agent (8), krab-ear-backend (4) |
| Всего events 24h (топ-20 агрегат) | ~**127** подтверждённых в window |

### Top-5 по count за 24h

| # | Issue | Count | Title |
|---|---|---|---|
| 1 | PYTHON-FASTAPI-66 | 19 | `db_corruption_detected: late` |
| 2 | PYTHON-FASTAPI-67 | 19 | `Traceback (most recent call last):` (DB corruption chain) |
| 3 | KRAB-EAR-AGENT-G | 16 | `App Hanging ≥2000 ms` (KrabEar) |
| 4 | KRAB-EAR-AGENT-5 | 8 | `App Hanging ≥2000 ms` (KrabEar) |
| 5 | KRAB-EAR-AGENT-8 | 6 | `App Hanging ≥2000 ms` (KrabEar) |

---

## Критичные (fix urgent)

### 1. DB Corruption кластер — kraab.session (5 issues, ~50+ events)

Все события сконцентрированы в интервале `20:08–20:41 UTC+3 05.05.2026` (примерно через 12h после Session 33 patches).

| Issue ID | Title | Count | Severity |
|---|---|---|---|
| PYTHON-FASTAPI-66 | `db_corruption_detected: late` | 19 | error |
| PYTHON-FASTAPI-67 | `Traceback (most recent call last):` (DB chain) | 19 | error |
| PYTHON-FASTAPI-6E | `db_corruption_detected_runtime: database disk image is malformed` | 19 (total), 1 each event | error |
| PYTHON-FASTAPI-7H | `DatabaseError: storage marked corrupt — connection invalidated after malformed write` | 3 | error |
| PYTHON-FASTAPI-7Z | `OperationalError: disk I/O error` (pyrogram_patch `_safe`) | 2 | error |
| PYTHON-FASTAPI-7G | `wake_up_failed: storage marked corrupt` (userbot_bridge) | 2 | error |
| PYTHON-FASTAPI-80 | `telegram_watchdog_reconnect_failed: database disk image is malformed` | 1 | error |
| PYTHON-FASTAPI-7Y | `command_error: storage marked corrupt` (DatabaseError in handler) | 1 | error |

**Контекст:** Обнаружен `kraab.session.bak-corrupt-1777737556` (2026-05-02 17:59:16) — автоматическая recovery уже была. Тем не менее события корруппции возобновились в 24h window ~в 20:00 05.05. Этот паттерн рекуррентен (Sessions 33+34). WAL/SHM файлы присутствуют (`kraab.session-shm`, `kraab.session-wal`).

**Ключевой сигнал:** `db_corruption_detected_runtime` из модуля `runtime` (не только startup integrity-gate) — значит, поломка происходит во время работы, не только при старте.

**Stack frames:** `src.bootstrap.pyrogram_patch → _safe_read / _safe` — патч работает, но corruptoin пробивается в runtime.

---

### 2. KrabEar AppHang кластер (5 issues, 30+ events)

| Issue ID | Title | Count | Culprit |
|---|---|---|---|
| KRAB-EAR-AGENT-G | App Hanging ≥2000 ms | 16 | redacted |
| KRAB-EAR-AGENT-5 | App Hanging ≥2000 ms | 8 | redacted |
| KRAB-EAR-AGENT-8 | App Hanging ≥2000 ms | 6 | redacted |
| KRAB-EAR-AGENT-A | App Hanging ≥2000 ms | 9 (total 24h) | redacted |
| KRAB-EAR-AGENT-4 | App Hanging ≥2000 ms | 10 (total 24h) | redacted |

**Контекст:** Задокументировано в backlog (Wave 14-H). Проблема существует давно. Новый issue `KRAB-EAR-AGENT-G` (firstSeen: 8h ago) — свежий. Hang-ы продолжаются непрерывно.

---

### 3. KrabEar Backend — SyntaxError в worktrees (3 issues, 3 events, firstSeen: 3h ago)

| Issue ID | Title | Count |
|---|---|---|
| KRAB-EAR-BACKEND-D | `SyntaxError: '{' was never closed (error_codes.py, line 21)` | 1 |
| KRAB-EAR-BACKEND-C | `SyntaxError: invalid character '→' (U+2192) (llm_rewriter.py, line 272)` | 1 |
| KRAB-EAR-BACKEND-B | `SyntaxError: invalid character '→' (U+2192) (llm_rewriter.py, line 272)` | 1 |

**Контекст:** Ошибки в worktree-агентах (не в main ветке KrabEar). Culprits: `__main__ in __init__`, `__main__ in _init_llm_rewriter`, `backend.action_items_extractor in <module>`. Основной `llm_rewriter.py` в main KrabEar не содержит строки 272 с `→`. Worktrees с агент-сессиями содержат экспериментальный код. **Не блокирует production, но указывает на незавершённый агентный код в worktrees.**

---

## Recurring noise (filter candidates)

### 1. OpenClaw `openclaw_api_error` 500 (52 total, 3 в окне)

| Issue ID | Title | Count (total / 24h window) |
|---|---|---|
| PYTHON-FASTAPI-60 | `openclaw_api_error: internal error, status=500` | 52 total / 3 в window |
| PYTHON-FASTAPI-5Y | `openclaw_health_check_failed: All connection attempts failed` | 11 total / 3 в window |
| PYTHON-FASTAPI-6B | `openclaw_health_check_failed: (empty error)` | 5 total / 1 в window |

**Оценка:** OpenClaw gateway периодически недоступен или перегружен. Это known pattern при перезапусках или overload. Уже частично фильтруется. Рекомендуется добавить predicate в `_BENIGN_ERROR_MARKERS`:
```python
"openclaw_health_check_failed",
"openclaw_api_error.*internal error",
```
**Однако** — если частота растёт, это может быть сигналом деградации gateway.

---

### 2. `wake_up_failed: PEER_ID_INVALID` (9 events)

| Issue ID | Title | Count |
|---|---|---|
| PYTHON-FASTAPI-71 | `wake_up_failed: [400 PEER_ID_INVALID]` | 9 |

**Оценка:** Краб пытается отправить wake-up message в чат с невалидным peer_id. Скорее всего, сохранённый chat_id устарел или бот был удалён из чата. Можно добавить в фильтр:
```python
"wake_up_failed.*PEER_ID_INVALID",
```

---

### 3. `RuntimeError: Cannot send a request, as the client has been closed` (5 events)

| Issue ID | Title | Count | Module |
|---|---|---|---|
| PYTHON-FASTAPI-7X | `RuntimeError: Cannot send a request, as the client has been closed.` | 5 | `src.core.local_health in is_lm_studio_available` |

**Оценка:** httpx клиент закрывается до завершения health-check. Типичный race condition при shutdown. Можно фильтровать:
```python
"Cannot send a request, as the client has been closed.*is_lm_studio_available",
```

---

### 4. `RateLimitError: 429 anthropic-claude-haiku-4-5` (4 events)

| Issue ID | Title | Count |
|---|---|---|
| PYTHON-FASTAPI-7T | `RateLimitError: 429 — Quota exceeded for claude-haiku-4-5` | 4 |

**Оценка:** Превышение квоты Google Vertex AI для claude-haiku-4-5 по tokens/minute. Эпизодическое. Не критично — есть fallback. Фильтр:
```python
"RateLimitError.*claude-haiku.*Quota exceeded",
```

---

### 5. `RuntimeError: read() called while another coroutine is already waiting` (5 total)

| Issue ID | Title | Count |
|---|---|---|
| PYTHON-FASTAPI-4T | `RuntimeError: read() called while another coroutine is already waiting for incoming data` | 5 total |

**Оценка:** asyncio race в stream reading. Давний issue (old firstSeen). Recurring noise. Фильтр:
```python
"read\\(\\) called while another coroutine is already waiting for incoming data",
```

---

## One-offs

| Issue ID | Title | Count | Вердикт |
|---|---|---|---|
| PYTHON-FASTAPI-7W | `[Errno 48] address already in use (:8080)` | 5 | Дублирующийся запуск при рестарте (launchd respawn race). Знакомый pattern. |
| PYTHON-FASTAPI-7V | `command_error: Invalid parse mode "markdown"` in `handle_quota` | 1 | Один раз, в handler'е quota — minor bug в parse_mode. |
| PYTHON-FASTAPI-7S | `memory_indexer_embed_failed: dlopen sqlite_vec/vec0.dylib` | 1 | sqlite_vec не загрузился (dylib path issue). Вероятно, при перезапуске с нечистым env. |
| PYTHON-FASTAPI-7M | `ClientError: Budget 0 is invalid. This model only works in thinking mode.` | 8 total | Модель требует thinking-бюджет > 0, но передаётся 0. Нужно добавить guard. |

---

## Новые issues (firstSeen в последние 24h)

12 новых групп — большинство являются sub-issues уже известных кластеров (DB corruption chain, KrabEar hang). Подлинно новые:
- `KRAB-EAR-AGENT-G` (hang, 16 events) — новый hang instance
- `PYTHON-FASTAPI-7S` — sqlite_vec dylib load fail
- `PYTHON-FASTAPI-7V` — Invalid parse mode "markdown"
- `KRAB-EAR-BACKEND-B/C/D` — SyntaxError в worktrees

---

## Recommendations

### Срочно (P0)

1. **DB Corruption рекуррентна** — события `db_corruption_detected_runtime` в runtime (не только startup) указывают на то, что integrity-gate при старте недостаточен. Corruption происходит в процессе работы. Root cause: WAL checkpoint или malformed write во время активной сессии. Нужен runtime DB watchdog с circuit-breaker (close+reopen connection при первом corruption event вместо продолжения работы).

2. **KrabEar AppHang** — 30+ событий за 24h, 5 разных group. Wave 14-H зафиксировал проблему как deferred. Требует отдельного исследования (возможно, blocking I/O в main thread или deadlock в STT pipeline).

### Средний приоритет (P1)

3. **`ClientError: Budget 0`** — 8 событий (PYTHON-FASTAPI-7M). Когда передаётся модель требующая thinking, но `budget_tokens=0`, возникает ошибка. Добавить guard в `openclaw_client.py` / model routing: если модель требует thinking, устанавливать минимальный budget (напр. 1024).

4. **KrabEar Backend SyntaxError в worktrees** — агентский код в worktrees содержит невалидный Python. Worktrees нужно регулярно очищать или изолировать от Sentry reporting (добавить `.sentryignore` для worktree paths).

### Фильтры для расширения `_BENIGN_ERROR_MARKERS`

```python
# Добавить в src/core/error_handler.py или аналог:
"openclaw_health_check_failed",
"openclaw_api_error.*internal error",
"wake_up_failed.*PEER_ID_INVALID",
"Cannot send a request, as the client has been closed.*is_lm_studio_available",
"RateLimitError.*claude-haiku.*Quota exceeded",
"read\\(\\) called while another coroutine is already waiting for incoming data",
```

### Observability baseline (для следующей сессии)

- **DB corruption rate:** 50+ events / 24h — высокий. Цель: 0 runtime events.
- **KrabEar hang rate:** 30+ events / 24h — высокий. Цель: <5.
- **OpenClaw 500 rate:** 52 events / ~7 days — приемлемо при restart cycles.
- **Новых критичных issues:** 0 (все новые — sub-instances известных кластеров).

---

## Sentry links

- [All unresolved issues](https://po-zm.sentry.io/issues/?query=is%3Aunresolved+lastSeen%3A-24h)
- [New issues (24h)](https://po-zm.sentry.io/issues/?query=is%3Aunresolved+firstSeen%3A-24h)
- [python-fastapi project](https://po-zm.sentry.io/projects/python-fastapi/)
- [krab-ear-agent project](https://po-zm.sentry.io/projects/krab-ear-agent/)
