# Inbox Stale Open Truth — 2026-03-28

## Контекст
- Цель: перестать считать старые `open owner_request` частью свежей owner-очереди.
- Живой кейс: `incoming:312322764:11427` от `p0lrd`, созданный `2026-03-27T17:10:11+00:00`, оставался `open` почти сутки.

## Что изменено
- В summary inbox добавлен отдельный bucket `stale_open`.
- Для owner-request/owner-mention появились отдельные счётчики:
  - `stale_open_owner_requests`
  - `stale_open_owner_mentions`
- В owner UI добавлены bulk-действия:
  - `Cancel stale open`
  - `Done stale open`
- В backend добавлены endpoints:
  - `GET /api/inbox/stale-open`
  - `POST /api/inbox/stale-open/remediate`

## Unit verification
- `./venv/bin/pytest -q tests/unit/test_inbox_service.py tests/unit/test_web_app_runtime_endpoints.py tests/unit/test_web_panel_bootstrap_order.py -q`
- Результат: `163 passed, 1 warning`

## Live verification
1. Выполнен controlled restart через one-click launcher.
2. После старта backend показал truthful summary:
   - `fresh_open_items=0`
   - `stale_open_items=1`
   - `new_owner_requests=0`
   - `stale_open_owner_requests=1`
3. `GET /api/inbox/stale-open?kind=owner_request&limit=10` вернул ровно один stale-open item:
   - `incoming:312322764:11427`
4. Через owner UI нажата bulk-кнопка `Cancel stale open`.
5. После remediation backend вернул:
   - `open_items=0`
   - `fresh_open_items=0`
   - `stale_open_items=0`
   - `pending_owner_requests=0`
   - `stale_open_owner_requests=0`
6. Persisted state в `~/.openclaw/krab_runtime_state/inbox_state.json` зафиксировал для `incoming:312322764:11427`:
   - `status=cancelled`
   - `last_action_actor=owner-ui`
   - `last_action_note=owner_ui_bulk_stale_open_cancelled`
   - `last_action_status=cancelled`

## Вывод
- Legacy-open item-ы больше не маскируются под свежие входящие.
- Owner UI теперь truthfully отделяет живой inbox от исторического хвоста и умеет безопасно его разруливать.
