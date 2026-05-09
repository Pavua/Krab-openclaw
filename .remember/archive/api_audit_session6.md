# API Audit Session 6 — Krab Owner Panel

**Дата:** 2026-04-12  
**Панель:** http://127.0.0.1:8080  
**Всего endpoints:** 189 (121 GET + 68 POST/PUT/DELETE)  
**Проверено GET endpoints:** 110 (исключены пути с `{param}`, `/docs`, `/redoc`, `/openapi.json`)

---

## Итоговая таблица по категориям

| Категория | Всего GET | OK (200) | Зависают | Soft error (ok=false) | Не настроено |
|-----------|-----------|----------|----------|-----------------------|--------------|
| health    | 4  | 4 | 0 | 0 | 0 |
| runtime   | 4  | 4 | 0 | 0 | 0 |
| system    | 2  | 2 | 0 | 0 | 0 |
| inbox     | 4  | 4 | 0 | 0 | 0 |
| model     | 7  | 5 | 1 (/catalog) | 0 | 1 |
| swarm     | 10 | 10| 0 | 0 | 0 |
| translator| 12 | 11| 0 | 1 (/test) | 0 |
| voice     | 2  | 2 | 0 | 0 | 0 |
| openclaw  | 23 | 14| 4 | 1 (autoswitch) | 4 (no OC response) |
| ops       | 12 | 12| 0 | 0 | 0 |
| policy    | 2  | 2 | 0 | 1 (ok=false) | 0 |
| misc/ui   | 10+| 10+| 0 | 2 (queue, reactions) | 0 |
| ecosystem | 3  | 3 | 0 | 0 | 0 |

---

## Критические баги (CRASH)

### 1. `/api/model/catalog` — убивает Krab процесс

**Severity:** CRITICAL  
**Симптом:** GET-запрос зависает на 8-15 секунд, затем Krab падает (Connection reset by peer → process exit).  
**Причина:** `_build_model_catalog()` → `_resolve_local_runtime_truth()` → `_lmstudio_model_snapshot()` → `_probe_lmstudio_model_snapshot()`. Где-то в цепочке происходит блокировка event loop или неперехваченное исключение, которое не обрабатывается FastAPI (уходит в uvicorn).  
**Из логов (старый venv):** `AttributeError: 'NoneType' object has no attribute 'get'` в `_build_openclaw_model_routing_status` строка 373 — `model_defaults = defaults.get("model")` где `defaults=None`. Эта же логика скорее всего присутствует в текущем venv.  
**Воспроизводится:** 100% при каждом холодном старте (кеш пустой).  

**Фикс:**
```python
# В _build_openclaw_model_routing_status и _build_model_catalog:
# Заменить:
model_defaults = defaults.get("model") if isinstance(defaults, dict) else {}
# На защищённую версию — уже есть, но нужна дополнительная защита:
defaults = defaults or {}
model_defaults = defaults.get("model") if isinstance(defaults, dict) else {}
```

И добавить `try/except Exception` вокруг всего `_build_model_catalog` с graceful 500 ответом.

---

## Зависающие endpoints (hang, не краш)

Эти endpoints зависают (0 bytes) и не убивают Krab, но блокируют HTTP-соединение:

| Endpoint | Время | Причина |
|----------|-------|---------|
| `/api/openclaw/cron/jobs` | >5s hang | Проксирует к OpenClaw, нет ответа |
| `/api/openclaw/cron/status` | >5s hang | То же |
| `/api/openclaw/channels/status` | >5s hang | То же |
| `/api/openclaw/control-compat/status` | >5s hang | То же |
| `/api/openclaw/browser-smoke` | >5s hang | OpenClaw browser не запущен |
| `/api/openclaw/browser-mcp-readiness` | >5s hang | То же |

**Рекомендация:** Добавить `asyncio.wait_for(..., timeout=5.0)` вокруг прокси-запросов к OpenClaw с graceful `{"ok": false, "error": "openclaw_timeout"}` ответом.

---

## Soft errors (ok=false, норма)

Эти endpoints отвечают 200 JSON, но `ok: false` — это ожидаемое поведение когда фича не настроена:

| Endpoint | Ошибка | Значение |
|----------|--------|----------|
| `/api/ctx` | `ai_runtime_not_configured` | OpenClaw runtime context не активен |
| `/api/queue` | `queue_not_configured` | Queue engine не настроен |
| `/api/policy` | `ai_runtime_not_configured` | AI policy не active (но `/api/policy/matrix` работает) |
| `/api/reactions/stats` | `reaction_engine_not_configured` | Reaction engine не настроен |
| `/api/translator/test` | `?text=... required` | Тест без параметра — норма |

---

## Проблемный endpoint с неправильным форматом ответа

| Endpoint | Проблема |
|----------|---------|
| `/api/openclaw/model-autoswitch/status` | Возвращает `{"detail": "openclaw_model_autoswitch_failed: usage: ..."}` — ошибка CLI arg `--profile current`. Нет поля `ok`. |

**Рекомендация:** Исправить вызов `openclaw_model_autoswitch.py` — убрать аргумент `--profile current` или обновить к текущей версии openclaw CLI.

---

## Ключевые endpoints — статус

| Endpoint | Статус | Примечание |
|----------|--------|------------|
| `/api/health/lite` | ✓ ok=true | Полный ответ, все поля |
| `/api/health` | ✓ ok=true | |
| `/api/v1/health` | ✓ ok=true | |
| `/api/runtime/summary` | ✓ ok=true | **Баг `self.kraab` ИСПРАВЛЕН** |
| `/api/translator/status` | ✓ ok=true | |
| `/api/swarm/stats` | ✓ ok=true | |
| `/api/model/status` | ✓ ok=true | |
| `/api/version` | ✓ ok=true | |
| `/api/model/catalog` | ✗ CRASH | Убивает процесс |

---

## Endpoints без поля `ok` (не JSON API, норма)

Возвращают HTML или данные без структуры `{ok: bool}`:
- `/` — landing page HTML
- `/inbox`, `/stats`, `/swarm`, `/translator`, `/costs` — HTML dashboard pages
- `/api/browser/tabs` — HTML
- `/api/links` — без ok (list/dict данные)
- `/api/model/recommend` — без ok
- `/api/openclaw/cloud`, `/api/openclaw/cloud/tier/state` — проксируют данные OpenClaw напрямую
- `/api/provisioning/drafts`, `/api/provisioning/templates` — без ok (list)
- `/api/runtime/handoff`, `/api/swarm/artifacts`, `/api/swarm/artifacts` — без ok

---

## Распределение по категориям (всего 189 endpoints)

| Категория | GET | POST | Всего |
|-----------|-----|------|-------|
| health/status | 6 | 0 | 6 |
| runtime | 4 | 0 | 4 |
| model | 7 | 6 | 13 |
| swarm | 12 | 6 | 18 |
| translator | 12 | 1 | 13 |
| openclaw | 23 | 8 | 31 |
| ops/metrics | 12 | 2 | 14 |
| inbox | 4 | 5 | 9 |
| voice | 2 | 1 | 3 |
| system | 2 | 1 | 3 |
| ecosystem | 3 | 0 | 3 |
| UI pages | 5 | 0 | 5 |
| прочие | 9+ | 38+ | 47+ |

---

## Приоритизированные рекомендации

### P0 (критично)
1. **Починить `/api/model/catalog`** — добавить `try/except` вокруг `_build_model_catalog` и защиту от `None` в `defaults.get("model")`. Endpoint не должен крашить процесс.

### P1 (важно)  
2. **Добавить таймаут для OpenClaw-проксирующих endpoints** — `/api/openclaw/cron/jobs`, `/api/openclaw/channels/status`, `/api/openclaw/control-compat/status`, `/api/openclaw/browser-*`. Максимум 5s timeout с graceful fallback.

### P2 (улучшение)
3. **Исправить `/api/openclaw/model-autoswitch/status`** — убрать `--profile current` из вызова CLI.
4. **Унифицировать ответ** у endpoints без поля `ok` (handoff, artifacts, links, recommend) — добавить `ok: true`.

---

## Итог

- **Работают нормально:** ~95 GET endpoints (из 110 проверенных)
- **Критический баг:** 1 endpoint (`/api/model/catalog`) крашит весь процесс
- **Зависают:** 6 endpoints (OpenClaw proxy без таймаута)
- **Soft-errors (норма):** 5 endpoints (фичи не настроены)
- **Сломан формат:** 1 endpoint (autoswitch CLI arg)
