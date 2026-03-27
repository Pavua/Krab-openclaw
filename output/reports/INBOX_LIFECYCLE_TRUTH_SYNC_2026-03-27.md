# Inbox Lifecycle Truth Sync — 2026-03-27

## Проблема

В owner-visible inbox оставались не только реальные pending owner-request, но и два хвоста, которые искажали operational truth:

1. `relay_request`
   - `relay:312322764:11402`
   - relay уже был доставлен владельцу, но item оставался `open`

2. legacy `proactive_action`
   - `proactive:watch_trigger:route_model_changed:2026-03-12T05:05:00+00:00`
   - это memory-only historical trace, который не должен был жить как активная owner-задача

## Кодовые изменения

### 1. Relay lifecycle

В `src/userbot_bridge.py` добавлен helper:

- `_acknowledge_open_relay_requests_for_chat(chat_id, ...)`

Поведение:

- при следующем directed owner message в тот же чат
- все `open relay_request` с тем же `chat_id`
- автоматически переводятся в `done`

Это убирает stale relay-хвост без ручного закрытия через UI.

### 2. Proactive watch lifecycle

В `src/core/proactive_watch.py` изменена политика `proactive_action`:

- `gateway_down`
- `scheduler_backlog_created`

создают `open proactive_action`.

А recovery-события:

- `gateway_recovered`
- `scheduler_backlog_cleared`

больше не создают новый `open` item, а закрывают исходный trace через:

- `inbox_service.set_status_by_dedupe(...)`

## Unit-проверка

Команда:

```bash
pytest -q tests/unit/test_userbot_inbox_flow.py tests/unit/test_proactive_watch.py tests/unit/test_proactive_inbox_trace.py tests/unit/test_scheduler.py -q
```

Результат:

- `26 passed`

## Live runtime sync

Для уже существующего исторического хвоста выполнен truthful cleanup:

```python
inbox_service.set_status_by_dedupe(
    "relay:312322764:11402",
    status="done",
    actor="system-cleanup",
    note="owner_followed_up_after_relay",
)

inbox_service.set_status_by_dedupe(
    "proactive:watch_trigger:route_model_changed:2026-03-12T05:05:00+00:00",
    status="done",
    actor="system-cleanup",
    note="legacy_non_actionable_proactive_trace",
)
```

## Live proof

После короткого TTL-кеша:

- `GET /api/inbox/status`
- `GET /api/health/lite`

оба показывают:

- `open_items=2`
- `attention_items=0`
- `pending_owner_requests=2`

То есть в owner inbox остались только реально открытые owner-request, без relay/proactive мусора.
