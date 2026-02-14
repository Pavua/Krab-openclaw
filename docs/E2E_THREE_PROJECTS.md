# E2E Three Projects (Krab + Krab Voice Gateway + Krab Ear)

**Дата:** 2026-02-12
**Цель:** единый live-пакет проверки межпроектной интеграции без ручной рутины.

## Что проверяется

1. Доступность сервисов:
- OpenClaw: `GET /health`
- Local LM: `GET /v1/models`
- Voice Gateway: `GET /health`
- Krab Ear backend: `GET /health`

2. Voice lifecycle (если Voice Gateway доступен):
- `POST /v1/sessions` (create)
- `PATCH /v1/sessions/{id}` (translation mode)
- `GET /v1/sessions/{id}/diagnostics`
- `DELETE /v1/sessions/{id}`
- `GET /v1/sessions/{id}` -> ожидается `404`

3. Итог:
- `overall_ok=true`, если доступен AI backend (`OpenClaw` или `Local LM`) и voice lifecycle проходит (когда gateway online).

## Как запускать

1. One-click:
- двойной клик: `scripts/run_live_ecosystem_e2e.command`

2. Через терминал:
```bash
python scripts/live_ecosystem_e2e.py
```

## Что получается на выходе

1. Печать полного JSON в stdout.
2. Файл отчета:
- `artifacts/ops/live_ecosystem_e2e_<UTC>.json`

## Переменные окружения

- `OPENCLAW_BASE_URL` (default: `http://127.0.0.1:18789`)
- `LM_STUDIO_URL` (default: `http://127.0.0.1:1234`)
- `VOICE_GATEWAY_URL` (default: `http://127.0.0.1:8090`)
- `VOICE_GATEWAY_API_KEY` (optional)
- `KRAB_EAR_BACKEND_URL` (default: `http://127.0.0.1:8765`)

## Интерпретация результата

1. `degradation=normal`:
- OpenClaw доступен.

2. `degradation=degraded_to_local_fallback`:
- OpenClaw недоступен, но локальный канал работает.

3. `degradation=critical_no_ai_backend`:
- OpenClaw и локальный канал оба недоступны.

4. `voice_lifecycle.skipped=true`:
- Voice Gateway offline, lifecycle не запускался.
