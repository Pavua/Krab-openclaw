# Inbox Owner UI Truth Sync — 2026-03-27

## Что проверялось

- owner panel на `http://127.0.0.1:8080` должна читать truthful inbox summary из runtime;
- inbox badge/meta должны различать `new` и `processing`, а не только общий `pending_owner_requests`;
- live action-кнопка `Done` должна менять persisted inbox-state, а не быть декоративной.

## Что исправлено

- `src/web/index.html`
  - inbox-фильтр статусов переведён на реальные runtime-статусы: `open`, `acked`, `done`, `cancelled`, `approved`, `rejected`;
  - inbox-фильтр kinds переведён на реальные kinds: `owner_request`, `owner_mention`, `owner_task`, `approval_request`, `relay_request`, `proactive_action`;
  - badge/meta теперь читают `/api/inbox/status` и показывают split `open / new / processing`;
  - карточки используют `item.item_id` и `created_at_utc`;
  - action-path переключён с legacy `resolved` на реальные backend-статусы `done/cancelled/approved/rejected`.
- `tests/unit/test_web_panel_bootstrap_order.py`
  - добавлена статическая регрессия на truthful inbox summary и живой `item_id/status` action-path.

## Проверки

### 1. Unit / static regression

```bash
./venv/bin/pytest -q tests/unit/test_web_panel_bootstrap_order.py tests/unit/test_inbox_service.py -q
```

Результат:

- `26 passed, 1 warning`

### 2. Runtime truth после controlled restart

```bash
curl -s http://127.0.0.1:8080/api/health/lite
curl -s http://127.0.0.1:8080/api/inbox/status
```

Подтверждено:

- `fresh_open_items=1`
- `acked_items=2`
- `stale_processing_items=2`
- `new_owner_requests=1`
- `processing_owner_requests=2`
- `stale_processing_owner_requests=2`

### 3. Live browser verification

Playwright открыл owner panel и подтвердил inbox-блок:

- badge: `1`
- meta: `3 open · 1 new · 2 processing · 2 stale · owner req 3 (1/2) stale 2`
- при фильтре `acked` две реальные карточки показали статус `PROCESSING · STALE`
- timestamp на карточках переключился в `stale since ...`
- у stale `owner_request` остались рабочие кнопки `Done` и `Cancel`

Артефакт:

- `output/playwright/inbox-truthful-summary-focused-20260327-1954.png`
- `output/playwright/inbox-stale-processing-focused-20260327-2004.png`

### 4. Live action-path verification

Через браузер нажата живая кнопка `Done` на stale owner-request:

- dedupe: `incoming:312322764:11428`
- item_id: `7d2d5379f063`

После клика:

- UI сразу обновился до `3 open · 1 new · 2 processing`
- `/api/inbox/status` показал:
  - `open_items=3`
  - `fresh_open_items=1`
  - `acked_items=2`
- `~/.openclaw/krab_runtime_state/inbox_state.json` зафиксировал:
  - `status=done`
  - `last_action_actor=owner-ui`
  - workflow event `action=done`

## Вывод

Owner UI inbox теперь синхронизирован с runtime truth по трём критичным направлениям:

1. Честно показывает split между новыми и уже обрабатываемыми owner-request.
2. Отдельно маркирует реально застрявшие `acked` item-ы как `stale processing`.
3. Реально управляет persisted inbox item-ами через backend, а не рисует декоративные кнопки по legacy-схеме.
