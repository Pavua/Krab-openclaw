# Handoff: Краб — Checkpoint 2026-03-24

> Этот файл — точка входа для нового диалога.
> Читать первым, не интерпретировать цифры прогресса как окончательные.

---

## Состояние системы прямо сейчас

| Компонент | Статус | Заметки |
|---|---|---|
| Краб (main process) | ✅ running | PID ~41532, порт 8080 |
| Telegram userbot | ✅ running | authorized_user: yung_nagato |
| OpenClaw routing | ✅ ok | provider: google-gemini-cli/gemini-3-flash-preview |
| Unit tests | ✅ 615/615 pass | `pytest tests/unit/ -q` |
| Acceptance gates Phase 1 | ✅ все прошли | `pytest tests/acceptance/ -v -s` |

**Запустить Краб если не запущен:**
```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
.venv/bin/python3 -m src.main &
```

**Проверить здоровье:**
```bash
curl -s http://127.0.0.1:8080/api/health/lite | python3 -c "import sys,json; d=json.load(sys.stdin); print('state:', d.get('telegram_userbot_state'), '| model:', d.get('last_runtime_route',{}).get('model','?'))"
```

---

## Что закрыто в этой сессии

### Phase 1 — OpenClaw Stability Kernel ✅ COMPLETE

Все acceptance gates прошли:
- **Gate 1**: 10/10 restart cycles — userbot поднимается после каждого рестарта
- **Gate 2**: 50/50 owner round-trips без silent-drop (через OpenClaw pipeline)
- **Gate 3**: 3/3 freeze/reclaim multi-account cycles — state изолирован
- **Smoke**: identity envelope с 6 полями (`operator_id`, `account_id`, `channel_id`, `team_id`, `trace_id`, `approval_scope`)

Тесты: `tests/acceptance/test_phase1_gates.py`

### Relay Feature ✅ COMPLETE

**Проблема:** Краб отвечал "запомнил и передам" но никогда не передавал.

**Решение** (`src/userbot_bridge.py`):
- `_RELAY_INTENT_KEYWORDS` — frozenset ключевых слов RU+EN (передай/скажи/tell him/notify/...)
- `_detect_relay_intent()` — pre-LLM keyword check, не блокирует ответ
- `_escalate_relay_to_owner()` — inbox item + Telegram Saved Messages уведомление
- Wired через `asyncio.create_task` после `_sync_incoming_message_to_inbox`

Unit tests: `tests/unit/test_userbot_relay_intent.py` — 33/33 pass

### Model config

`~/.openclaw/openclaw.json` (`agents.defaults.model`):
- primary: `qwen-portal/coder-model` (конфиг-preference, OpenClaw сам решает routing)
- fallbacks: `[codex-cli/gpt-5.4, qwen-portal/coder-model]`
- **Удалён:** `google/gemini-3.1-pro-preview` и `google/gemini-3-flash-preview` (платный API, нет баланса)
- Реальный routing идёт через `google-gemini-cli/gemini-3-flash-preview` (CLI subscription)

---

## Phase 2: Channel Reliability + Proactive Core

**Текущий статус Phase 2:** ~60% (relay + proactive traces + auto-handoff закрыты)

### 2.1 Reserve bot parity ❌ TODO

**Проблема:** `TELEGRAM_BOT_TOKEN` настроен в конфиге, но второй Telegram-бот не запущен — нет fallback-канала связи с владельцем.

**Что нужно:**
1. Проверить есть ли `TELEGRAM_BOT_TOKEN` в env/config
2. Если есть — запустить `python-telegram-bot` или `aiogram` instance как reserve bot
3. Маршрутизировать owner-escalations через него если userbot offline

**Файлы для изучения:** `src/config.py` (TELEGRAM_BOT_TOKEN), `src/modules/web_app.py` (webhook routes)

### 2.2 Deterministic memory/handoff export ✅ COMPLETE

**Проблема:** Memory и handoff существуют, но нет автотриггера — экспорт происходил только вручную.

**Решение** (`src/userbot_bridge.py`):
- `_auto_export_handoff_snapshot()` — автоэкспорт через `/api/runtime/handoff`
- Триггер при остановке userbot → `await self._auto_export_handoff_snapshot(reason="userbot_stop")`
- Периодический триггер в `_safe_maintenance()` → каждые 4 часа
- Never raises — graceful degradation при ошибках

Unit tests: `tests/unit/test_userbot_auto_handoff_export.py` — 4/4 pass
Artifacts: `artifacts/auto_handoff_<timestamp>.json`

### 2.3 Owner-visible trace для proactive actions ❌ TODO

**Проблема:** Cron jobs, reminders, proactive_watch — действуют незаметно для владельца. Нет inbox-следа.

**Что нужно:**
- Все proactive actions (cron, reminder fire, watch trigger) должны создавать `inbox_service.upsert_item` с `kind="proactive_action"`
- Минимальный trace: что сработало, когда, результат

**Файлы:** `src/core/proactive_watch.py`, `src/modules/web_app.py` (cron endpoints), `src/handlers/command_handlers.py` (remind)

---

## Архитектурные заметки

### Как работает routing

```
Telegram message → KraabUserbot._process_message_serialized()
  → _detect_relay_intent() [pre-LLM, frozenset lookup]
  → _sync_incoming_message_to_inbox() [всегда]
  → asyncio.create_task(_escalate_relay_to_owner()) [если relay]
  → OpenClaw API → google-gemini-cli (или fallback)
  → ответ пользователю
```

### Inbox как центральная шина

`inbox_service.upsert_item()` — единая точка для всех проактивных событий:
- `kind="relay_request"` — relay от non-owner
- `kind="proactive_action"` — cron/reminder/watch
- `kind="owner_request"` — входящий запрос владельца
- severity: `info` / `warning` / `critical`

Identity envelope обязателен в каждом item.

---

## Ключевые файлы

```
src/userbot_bridge.py          — главный файл, relay + message flow
src/core/inbox_service.py      — шина событий + identity envelope
src/core/access_control.py     — ACL owner/full/partial
src/core/runtime_policy.py     — provider policies + runtime modes
src/modules/web_app.py         — HTTP API :8080 (health, restart, assistant...)
tests/acceptance/test_phase1_gates.py  — Phase 1 acceptance suite
tests/unit/test_userbot_relay_intent.py — relay detection tests
~/.openclaw/openclaw.json      — OpenClaw config (model routing preferences)
```

---

## Быстрые команды

```bash
# Запустить все unit тесты
cd /Users/pablito/Antigravity_AGENTS/Краб && .venv/bin/python -m pytest tests/unit/ -q

# Acceptance gates
.venv/bin/python -m pytest tests/acceptance/ -v -s

# Статус Краба
curl -s http://127.0.0.1:8080/api/health/lite | python3 -m json.tool

# Рестарт userbot
curl -s -X POST http://127.0.0.1:8080/api/krab/restart_userbot

# Логи живые
tail -f /tmp/krab_test_run.log 2>/dev/null || journalctl -f 2>/dev/null
```

---

## Следующий блок работы

**Приоритет 1 (взять первым):** Phase 2.2 — deterministic memory/handoff export
Почему: наименьший риск сайд-эффектов, высокая ценность для непрерывности.

**Приоритет 2:** Phase 2.3 — owner-visible trace для proactive actions
Почему: закрывает "слепые пятна" в inbox, простая интеграция через уже готовый `inbox_service`.

**Приоритет 3:** Phase 2.1 — reserve bot parity
Почему: требует отдельный bot token и process management, больше scope.

---

*Создан: 2026-03-24 | Branch: codex/fix-handle-shop-export | Commit: e31aad2*
