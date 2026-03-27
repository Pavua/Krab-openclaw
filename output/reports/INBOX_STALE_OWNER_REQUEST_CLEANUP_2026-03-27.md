# Inbox Stale Owner Request Cleanup — 2026-03-27

## Что проверялось

- Устаревшие `open owner_request` в persisted inbox засоряли `api/health/lite`.
- Исторически это были два item-а от `2026-03-19`:
  - `incoming:312322764:10848`
  - `incoming:312322764:10897`

## Что изменено

- `scripts/cleanup_old_inbox_items.py` переведён из одноразовой миграции в reusable CLI.
- Default policy теперь консервативная:
  - `kind=owner_request`
  - `status=open`
  - `older_than_days=3`
- Более рискованные cleanup-сценарии требуют явных флагов:
  - `--kind`
  - `--message-id`
  - `--item-id`

## Unit-проверка

- `python3 -m py_compile scripts/cleanup_old_inbox_items.py tests/unit/test_cleanup_old_inbox_items.py`
- `pytest -q tests/unit/test_cleanup_old_inbox_items.py tests/unit/test_inbox_service.py -q`
- Результат: `23 passed, 1 warning`

## Live dry-run

Команда:

```bash
./venv/bin/python scripts/cleanup_old_inbox_items.py --dry-run
```

Результат:

- найдено ровно `2` stale item-а
- оба относятся к `owner_request`
- message_id: `10848`, `10897`

## Live apply

Команда:

```bash
./venv/bin/python scripts/cleanup_old_inbox_items.py
```

Результат:

- `Архивировано: 2 items (ошибок: 0)`
- повторная верификация утилиты: `целевые stale items закрыты`

## Прямой state proof

- `incoming:312322764:10848` -> `status=cancelled`
- `incoming:312322764:10897` -> `status=cancelled`
- В `workflow_events` записан новый head-event:
  - `action=bulk_updated`
  - `actor=system-cleanup`
  - `status=cancelled`
  - `note=archived during stale inbox cleanup`

## Health-lite proof

Сразу после cleanup `api/health/lite` ещё мог кратко отдавать старый `inbox_summary` из короткого TTL-кеша.

После повторного запроса через пару секунд:

- `open_items=4`
- `pending_owner_requests=2`

То есть в operational truth остались только свежие owner-request, а старый debt удалён.
