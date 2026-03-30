---
name: krab-web-runtime-endpoints-auditor
description: "Аудировать truthful web runtime endpoints и связанные owner UI API проекта `/Users/pablito/Antigravity_AGENTS/Краб`, включая `runtime_handoff`, `ops_runtime_snapshot`, ACL и model-autoswitch endpoints. Использовать, когда нужно проверить, что web API отражает реальный runtime state, write-endpoints делают то, что обещают, а owner panel на `:8080` не врёт из-за stale payload или неверной сериализации."
---

# Krab Web Runtime Endpoints Auditor

Используй этот навык для проверки API-правды между backend и owner UI. Он нужен, когда endpoint жив, но payload всё равно может быть ложным или неполным.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Ключевые endpoints

- `GET /api/runtime/handoff`
- `GET /api/ops/runtime_snapshot`
- `GET /api/userbot/acl/status`
- `POST /api/userbot/acl/update`
- `GET /api/openclaw/model-autoswitch/status`
- `GET /api/policy`

## Ключевые файлы

- `/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_app.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_web_app_runtime_endpoints.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/e2e/test_web_panel_openclaw_health.py`

## Рабочий цикл

1. Считать payload endpoint-а.
2. Сопоставить его с runtime truth или со скриптом-источником.
3. Проверить, как этот payload потребляется в owner UI.
4. Если endpoint write-capable, подтвердить побочный эффект отдельно.

## Полезные тесты

```bash
pytest tests/unit/test_web_app_runtime_endpoints.py -q
pytest tests/e2e/test_web_panel_openclaw_health.py -q
```

## Ограничения

- Не считать `200 OK` доказательством truthful payload.
- Не проверять только UI без проверки API-источника.
- Если endpoint кэшируется, проверять invalidation и повторный fetch.
