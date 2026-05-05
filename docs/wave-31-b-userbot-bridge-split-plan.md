# Wave 31-B: userbot_bridge.py — анализ и план дальнейшего сплита

> **Тип:** Read-only investigation, NO code changes  
> **Дата:** 2026-05-05  
> **Ветка:** `claude/naughty-ellis-f5a58e`  
> **Статус старого proposal:** `docs/USERBOT_BRIDGE_SPLIT_PROPOSAL.md` (сессия 4) — **ПОЛНОСТЬЮ РЕАЛИЗОВАН**

---

## 1. Текущее состояние файла

| Метрика | Значение |
|---|---|
| LOC | **6 467** |
| Классов | 2 (`_TelegramSendQueue`, `KraabUserbot`) |
| Top-level функций | 1 |
| Методов `KraabUserbot` + `_TelegramSendQueue` | **87** |
| Самый большой метод | `_process_message_serialized` — **1 086 LOC** |
| Второй по размеру | `_process_message` — **476 LOC** |
| Третий | `_setup_handlers` — **946 LOC** |
| Четвёртый | `start` — **540 LOC** |

### Реализованные mixins (из сессии 4–7)

Все 7 модулей из старого proposal уже живут в `src/userbot/`:

| Файл | LOC | Статус |
|---|---:|---|
| `llm_text_processing.py` | 1 139 | DONE — все 18 методов перенесены |
| `llm_flow.py` | 2 513 | DONE — все методы перенесены |
| `runtime_status.py` | 627 | DONE — все 14 методов перенесены |
| `voice_profile.py` | 479 | DONE — все 15 методов перенесены |
| `access_control.py` | 501 | DONE — все 12 методов перенесены |
| `background_tasks.py` | 288 | DONE — все 9 методов перенесены |
| `session.py` | 817 | DONE — все 19 методов перенесены |
| **Итого перенесено** | **6 364** | — |

`KraabUserbot` наследует все 7 mixin-ов через:
```python
from .userbot.access_control import AccessControlMixin
from .userbot.auto_translate import AutoTranslateMixin
from .userbot.background_tasks import BackgroundTasksMixin
from .userbot.llm_flow import LLMFlowMixin, _run_after_previous_sentinel
from .userbot.llm_text_processing import LLMTextProcessingMixin
from .userbot.runtime_status import RuntimeStatusMixin
from .userbot.session import SessionMixin
from .userbot.voice_profile import VoiceProfileMixin
```

---

## 2. Категоризация оставшихся методов (87 методов, 6 189 LOC)

> Методы, которые ОСТАЛИСЬ в `userbot_bridge.py` после всех предыдущих волн.

| Категория | # методов | LOC | Proposed мixin |
|---|---:|---:|---|
| `_TelegramSendQueue` (standalone class) | 9 | 304 | Нет — оставить отдельным классом |
| Lifecycle / Startup (core) | 6 | 743 | **STAYS** — ядро оркестратора |
| `_setup_handlers` | 1 | 946 | `handler_registry.py` (high-risk) |
| Startup state / error markers | 5 | 65 | `startup_state.py` (low-risk) |
| Callback query (inline buttons) | 4 | 100 | `callback_handler.py` (low-risk) |
| Handoff / session export | 1 | 39 | `handoff_ops.py` или в startup_state |
| Cron + scheduler sync | 5 | 323 | `cron_dispatch.py` (medium) |
| Background loops / proactive | 8 | 294 | `proactive_loops.py` (medium) |
| Network watchdog | 2 | 143 | `network_watchdog.py` (low-risk) |
| Translator profile | 9 | 122 | `translator_profile.py` (low-risk) |
| Translator + Voice dispatch | 2 | 152 | `translator_voice.py` (medium) |
| Swarm team clients | 3 | 174 | `swarm_dispatch.py` (medium) |
| Response delivery | 5 | 214 | `response_delivery.py` (medium) |
| Inbox / relay integration | 8 | 397 | `relay_handler.py` (medium) |
| Media processing | 3 | 297 | `media_handler.py` (low-risk) |
| Error helpers (static) | 5 | 25 | Слить в `telegram_send_utils.py` |
| Reaction / monitor | 3 | 134 | `reaction_dispatch.py` (low-risk) |
| Telegram API wrappers | 2 | 92 | `telegram_send_utils.py` (low-risk) |
| Message text utils | 2 | 19 | Слить в `llm_text_processing.py` |
| **Core orchestrator** (`_process_message*`) | 2 | 1 562 | **STAYS** — не трогать |
| Utilities | 2 | 44 | **STAYS** |

### Распределение LOC по судьбе

| Судьба | LOC |
|---|---|
| STAYS (ядро) — `start/stop/restart/__init__` + orchestrator | ~2 349 |
| Кандидаты на вынос — новые mixin-ы | ~2 792 |
| `_TelegramSendQueue` (отдельный класс, остаётся) | 304 |
| `_setup_handlers` (high-risk, отдельная волна) | 946 |

**Цель после полного сплита:** bridge ~2 700 LOC (−58% от текущих 6 467)

---

## 3. Предлагаемая структура новых модулей

```
src/userbot/
  # Уже существуют (реализованы):
  ├── access_control.py        (501 LOC) — ACL, sender checks
  ├── auto_translate.py        — авто-перевод
  ├── background_tasks.py      (288 LOC) — task lifecycle
  ├── background_tasks.py      (288 LOC)
  ├── llm_flow.py              (2513 LOC) — LLM pipeline
  ├── llm_retry.py             — retry logic
  ├── llm_text_processing.py   (1139 LOC) — text utils
  ├── reply_preprocessor.py    — preprocessing
  ├── runtime_status.py        (627 LOC) — status fast-paths
  ├── session.py               (817 LOC) — Pyrogram lifecycle
  ├── typing_keepalive.py      — typing indicator
  ├── voice_profile.py         (479 LOC) — voice settings
  ├── wal_checkpoint_pre_exit.py
  │
  # НОВЫЕ (Wave 31-C+):
  ├── startup_state.py         — _set_startup_state, _mark_*
  ├── callback_handler.py      — inline buttons CQ processing
  ├── network_watchdog.py      — _probe_telegram_dc, _network_offline_monitor_loop
  ├── translator_profile.py    — get/update translator profile+state
  ├── translator_voice.py      — _apply_voice_dispatcher, _handle_translator_voice
  ├── media_handler.py         — document/video processing + describe_video_frame
  ├── reaction_dispatch.py     — send_message_reaction, reaction_updated, monitor
  ├── telegram_send_utils.py   — _safe_edit, _safe_reply_or_send_new + error helpers
  ├── response_delivery.py     — _deliver_response_parts, autodel, smart trigger
  ├── relay_handler.py         — inbox sync, relay intent, forward_guest
  ├── proactive_loops.py       — idea tick, command usage, proactive watch loops
  ├── cron_dispatch.py         — cron prompt/context, scheduled send, scheduler sync
  ├── swarm_dispatch.py        — start/stop/init swarm team clients
  └── handler_registry.py      — _setup_handlers (HIGH RISK — отдельная волна)
```

---

## 4. Приоритизация волн (low-risk сначала)

### Wave 31-C: `startup_state.py` + `callback_handler.py` + `network_watchdog.py`
**Risk: LOW**

**startup_state.py** (~65 LOC, 5 методов):
- `_is_interactive_login_required_error` (static)
- `_set_startup_state`
- `_mark_manual_relogin_required`
- `_mark_transport_degraded`
- `_restore_running_state_after_probe`

Зависимости: только `self._startup_state` dict + `self._mark_*` вызывают друг друга. Легко изолируется.

**callback_handler.py** (~100 LOC, 4 метода):
- `_handle_callback_query`
- `_cb_confirm`, `_cb_page`, `_cb_action`

Зависимости: `self.client`, `self._safe_edit`, `self._deliver_response_parts`. После extraction `_safe_edit` → `telegram_send_utils`, зависимости останутся через `self.*` — ок.

**network_watchdog.py** (~143 LOC, 2 метода):
- `_probe_telegram_dc` (static, нет self)
- `_network_offline_monitor_loop` (использует `self._last_telegram_event_ts`, `self._send_proactive_watch_alert`)

---

### Wave 31-D: `translator_profile.py` + `telegram_send_utils.py` + `reaction_dispatch.py`
**Risk: LOW–MEDIUM**

**translator_profile.py** (~122 LOC, 9 методов):
- `_repo_root`, `_translator_runtime_profile_path`, `_translator_session_state_path`
- `get_translator_runtime_profile`, `get_translator_session_state`
- `update_translator_runtime_profile`, `update_translator_session_state`
- `reset_translator_session_state`, `_is_translator_active_for_chat`

Зависимости: только файловая система + `config`. Нет Pyrogram.

**telegram_send_utils.py** (~117 LOC = 92 wrappers + 25 static helpers):
- `_safe_edit` (56 LOC) — использует `self.client`
- `_safe_reply_or_send_new` (36 LOC) — использует `self.client`
- `_is_message_not_modified_error` … `_is_message_too_long_error` (static)
- `_extract_message_text`, `_is_command_like_text`

**reaction_dispatch.py** (~134 LOC, 3 метода):
- `_send_message_reaction` — использует `self.client`
- `_handle_message_reaction_updated` — event handler
- `_send_monitor_alert` — uses `self.client`

---

### Wave 31-E: `media_handler.py` + `response_delivery.py`
**Risk: MEDIUM**

**media_handler.py** (~297 LOC, 3 метода):
- `_process_document_message` (98 LOC) — OCR + perceptor
- `_describe_video_frame` (60 LOC) — vision AI
- `_process_video_message` (139 LOC) — video pipeline

Зависимости: `self.client`, perceptor, openclaw_client. Средняя сложность, но методы атомарны.

**response_delivery.py** (~214 LOC, 5 методов):
- `_deliver_response_parts` (127 LOC) — центральный deliver
- `_maybe_record_smart_trigger_response` (44 LOC)
- `_maybe_schedule_autodel` (17 LOC)
- `_message_ids_from_delivery` (9 LOC, static)
- `_should_force_cloud_for_photo_route` (17 LOC, static)

Risk: `_deliver_response_parts` — вызывается из `_process_message_serialized` ~3 раза. При mixin-extraction это прозрачно через `self.*`.

---

### Wave 31-F: `relay_handler.py` + `proactive_loops.py`
**Risk: MEDIUM**

**relay_handler.py** (~397 LOC, 8 методов):
- `_record_incoming_reply_to_inbox` (38 LOC)
- `_build_effective_user_query` (38 LOC)
- `_should_capture_incoming_owner_item` (29 LOC)
- `_acknowledge_open_relay_requests_for_chat` (43 LOC)
- `_sync_incoming_message_to_inbox` (60 LOC)
- `_detect_relay_intent` (12 LOC, static)
- `_escalate_relay_to_owner` (85 LOC)
- `_forward_guest_incoming_to_owner` (92 LOC)

Зависимости: `inbox_service`, `self.client`, `self.me`. Широкий fan-out но атомарная группа.

**proactive_loops.py** (~294 LOC, 8 методов):
- `_owner_notify_target`
- `_send_proactive_watch_alert`
- `_ensure_silence_schedule_started`
- `_ensure_memory_indexer_started`
- `_idea_features_tick_loop` (145 LOC — самый большой в группе)
- `_command_usage_save_loop`
- `_ensure_proactive_watch_started`
- `_run_proactive_watch_loop`

Risk: `_idea_features_tick_loop` имеет `self._idea_tick_state` (8 обращений) — состояние инициализируется в `__init__`. При mixin — ок.

---

### Wave 31-G: `cron_dispatch.py` + `swarm_dispatch.py` + `translator_voice.py`
**Risk: MEDIUM**

**cron_dispatch.py** (~323 LOC, 5 методов):
- `_build_cron_system_prompt` (21 LOC)
- `_build_cron_context` (87 LOC)
- `_run_cron_prompt_and_send` (97 LOC)
- `_send_scheduled_message` (23 LOC)
- `_sync_scheduler_runtime` (95 LOC)

**swarm_dispatch.py** (~174 LOC, 3 метода):
- `_start_swarm_team_clients` (105 LOC)
- `_stop_swarm_team_clients` (47 LOC)
- `_init_swarm_team_clients` (22 LOC)

Зависимости: `self._swarm_team_clients` dict (инициализируется в `__init__`), SwarmTeamClient. При mixin — clean.

**translator_voice.py** (~152 LOC, 2 метода):
- `_apply_voice_dispatcher` (68 LOC) — voice vs text dispatch
- `_handle_translator_voice` (84 LOC) — translator pipeline для голоса

---

### Wave 31-H: `_setup_handlers` (handler_registry.py)
**Risk: HIGH — отдельная волна, не раньше E–G завершены**

`_setup_handlers` — 946 LOC, регистрирует 100+ Pyrogram-обработчиков через `@client.on_message(filters.*)`.

Специфика:
- Тело метода — это DSL на фильтрах Pyrogram
- Большинство lambda/closure захватывают `self`
- Результат — side effect: регистрация handlers на клиенте

Подход: **не mixin**, а вынос в `handler_registry.py` как свободную функцию:
```python
# src/userbot/handler_registry.py
def setup_handlers(userbot: "KraabUserbot") -> None:
    client = userbot.client
    # все @client.on_message(...) блоки
```

Это разрывает mixin-паттерн, зато:
- `userbot_bridge.py: _setup_handlers` → однострочник `setup_handlers(self)`
- Файл `handler_registry.py` можно тестировать изолированно

Risk: высокий из-за объёма (946 LOC), но методологически чистый.

---

## 5. Risk Assessment таблица

| Mixin | LOC | Risk | Dependency complexity | Test coverage |
|---|---:|---|---|---|
| `startup_state.py` | 65 | LOW | `self._startup_state` dict только | `test_userbot_startup.py` |
| `callback_handler.py` | 100 | LOW | `self.client`, `self._safe_edit` (через self.*) | `test_userbot_bridge_handlers.py` |
| `network_watchdog.py` | 143 | LOW | `self._last_telegram_event_ts`, нет Pyrogram calls | нет → добавить |
| `translator_profile.py` | 122 | LOW | файловая система + config | нет → добавить |
| `telegram_send_utils.py` | 117 | LOW | `self.client` только | `test_userbot_bridge_message_author_required.py` |
| `reaction_dispatch.py` | 134 | LOW | `self.client` | `test_message_reactions.py` |
| `media_handler.py` | 297 | MEDIUM | perceptor + openclaw_client | `test_userbot_document_flow.py`, `test_image_vision_pipeline.py` |
| `response_delivery.py` | 214 | MEDIUM | 3 вызова из `_process_message_serialized` | `test_userbot_buffered_stream_flow.py` |
| `relay_handler.py` | 397 | MEDIUM | inbox_service, `self.client`, `self.me` | `test_userbot_inbox_flow.py`, `test_userbot_relay_intent.py` |
| `proactive_loops.py` | 294 | MEDIUM | `self._idea_tick_state` (8 refs), `self._proactive_watch_task` | `test_userbot_bridge_idea_features_tick.py` |
| `cron_dispatch.py` | 323 | MEDIUM | openclaw_client, scheduler, `self.client` | `test_cron_prompt_context.py` |
| `swarm_dispatch.py` | 174 | MEDIUM | `self._swarm_team_clients` dict | нет → добавить |
| `translator_voice.py` | 152 | MEDIUM | voice_engine, translator_session_state | `test_userbot_bridge_voice_wire.py` |
| `handler_registry.py` | 946 | HIGH | весь client + все self.* refs | `test_userbot_bridge_handlers.py` |

---

## 6. Состояние `_process_message_serialized` (1 086 LOC)

Это самый большой метод и **не подлежит выносу в mixin** — это главный оркестратор обработки сообщений. Однако внутри него есть логические блоки которые можно **выделить в вспомогательные методы** (без смены файла):

- ~lines 4862–4950: early return gates (spam, silence, ACL) → `_check_message_early_gates()`
- ~lines 4950–5050: message parsing + context building → уже есть `_build_effective_user_query`
- ~lines 5050–5200: command routing → inline `_route_command()`
- ~lines 5200–5400: LLM pipeline dispatch → уже есть `_run_llm_request_flow`
- ~lines 5400–5948: post-processing + delivery → уже есть `_deliver_response_parts`

**Рекомендация:** внутренний рефактор `_process_message_serialized` — отдельная волна (31-I), ПОСЛЕ всех mixin-ов.

---

## 7. Estimated effort

| Волна | Файл(ы) | LOC перемещается | Effort (часы) |
|---|---|---:|---|
| 31-C | startup_state + callback + network_watchdog | 308 | ~2h |
| 31-D | translator_profile + telegram_send_utils + reaction_dispatch | 373 | ~2h |
| 31-E | media_handler + response_delivery | 511 | ~3h |
| 31-F | relay_handler + proactive_loops | 691 | ~3-4h |
| 31-G | cron_dispatch + swarm_dispatch + translator_voice | 649 | ~3h |
| 31-H | handler_registry (HIGH RISK) | 946 | ~6h |
| 31-I | `_process_message_serialized` internal refactor | 0 перемещается | ~4h |
| **ИТОГО** | | **~3 478** | **~23h** |

После 31-G (без H и I): bridge ≈ **2 800 LOC** (−57%)  
После 31-H: bridge ≈ **1 900 LOC** (−71%)

---

## 8. Testing strategy

### Для каждой волны (31-C … 31-H):
1. Создать новый файл mixin в `src/userbot/`
2. Добавить `from .userbot.X import XMixin` + в `class KraabUserbot(…, XMixin, …)`
3. Удалить методы из `userbot_bridge.py`
4. `pytest tests/ -q --timeout=30` — должен оставаться ≤ 8 pre-existing fails
5. `ruff check src/ && ruff format src/`
6. Smoke: `mcp__krab-yung-nagato__krab_status` после рестарта

### Новые тесты требуются для:
- `network_watchdog.py` — `test_network_watchdog.py` (mock DC probe, offline detection)
- `translator_profile.py` — `test_translator_profile_mixin.py` (file I/O round-trip)
- `swarm_dispatch.py` — `test_swarm_team_clients.py` (init/start/stop lifecycle)

### Existing coverage:
- `test_userbot_mixins.py` — проверяет что все mixin-ы подключены к `KraabUserbot`
- `test_userbot_bridge_handlers.py` — проверяет регистрацию handlers

---

## 9. Граф зависимостей `self.*` (ключевые атрибуты)

Наиболее используемые `self.*` из `userbot_bridge.py` (показывают coupling):

| Атрибут | # использований | Инициализируется в |
|---|---:|---|
| `self.client` | 173 | `start()` |
| `self.me` | 42 | `start()` |
| `self._safe_edit` | 22 | method (через self) |
| `self._safe_reply_or_send_new` | 17 | method (через self) |
| `self._idea_tick_state` | 8 | `__init__` |
| `self._swarm_team_clients` | 7 | `_init_swarm_team_clients` |
| `self._workers` | 8 | `_TelegramSendQueue.__init__` |

`self.client` — 173 вхождения — **самый высокий coupling**. При mixin-extraction это не проблема (все mixin-методы получают его через `self`), но означает что большинство mixin-ов зависят от Pyrogram client being alive — тестировать надо с `MagicMock(client)`.

---

## 10. Выводы и top-3 кандидата

### Состояние (2026-05-05)
- `userbot_bridge.py` **6 467 LOC** (было ~6 000 в сессии 4, слегка выросло за счёт новых фич)
- Старый proposal **полностью реализован** — 47 методов перенесены в 7 mixin-файлов
- Следующий уровень: 14 новых mixin-файлов для оставшихся 87 методов

### Top-3 кандидата на split (low-risk first)

1. **`network_watchdog.py`** — 2 метода, 143 LOC, минимальные зависимости. `_probe_telegram_dc` вообще статический. Идеальная первая волна.

2. **`translator_profile.py`** — 9 методов, 122 LOC, чистая файловая система без Pyrogram. Группа атомарна, все методы связаны одной темой.

3. **`telegram_send_utils.py`** — 7 методов/функций, 117 LOC. Статические `_is_*_error` helper-ы + два Pyrogram wrapper-а. Широко используется внутри bridge, поэтому вынос снизит LOC bridge и даст базу для других mixin-ов.

---

*Отчёт создан Wave 31-B (read-only analysis). Реализацию начинать с Wave 31-C.*
