# Krab Ear — Cross-Project Distributed Tracing (Sentry)

Этот документ — инструкция для **Krab Ear project** (репо `/Users/pablito/Antigravity_AGENTS/Krab Ear`). В Main Krab со стороны клиента (`src/integrations/krab_ear_client.py`) уже пробрасывается `sentry-trace` / `baggage` в каждый вызов Ear backend. Чтобы trace был склеен в Sentry UI между `python-fastapi` (Main) и `krab-ear-backend`, Ear должен принимать эти headers и `continue_trace(...)`.

## Цель

Когда ты смотришь issue в Sentry project **python-fastapi** (Main Krab), ты видишь linked issue в **krab-ear-backend** — и наоборот. Один trace = один spinning chain: `Main Krab /api/voice/handle` → `Ear /transcribe` → `Whisper encode`.

## Шаги (Ear side)

### 1. Install

```bash
pip install 'sentry-sdk[fastapi]>=2.0'
```

### 2. Sentry init

В Ear backend (`KrabEar/backend/service.py` или общий bootstrap):

```python
import os
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.asyncio import AsyncioIntegration

dsn = os.getenv("SENTRY_DSN_EAR", "").strip()
if dsn:
    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("KRAB_ENV", "production"),
        traces_sample_rate=0.1,
        integrations=[
            FastApiIntegration(transaction_style="url"),
            AsyncioIntegration(),
        ],
        send_default_pii=False,
    )
    sentry_sdk.set_tag("service", "krab-ear")
```

`FastApiIntegration(transaction_style="url")` автоматически читает `sentry-trace` и `baggage` из incoming HTTP request и continueет trace. Никаких ручных `continue_trace(...)` не нужно для FastAPI-маршрутов.

### 3. IPC path (unix socket)

Krab Ear по умолчанию работает как IPC backend через unix socket. Main Krab шлёт trace в `params._trace` JSON-RPC-запроса:

```json
{"id": "health", "method": "ping", "params": {"_trace": {"sentry-trace": "...", "baggage": "..."}}}
```

На принимающей стороне (Ear `service.py`), оберни обработку запроса в `continue_trace`:

```python
import sentry_sdk

def handle_request(req: dict) -> dict:
    trace = (req.get("params") or {}).get("_trace") or {}
    # continue_trace принимает dict-like с sentry-trace и baggage
    with sentry_sdk.continue_trace(trace):
        with sentry_sdk.start_transaction(op="ipc.handle", name=req.get("method", "unknown")):
            # ... дальше обычный обработчик метода
            return dispatch(req)
```

### 4. Env

```bash
# .env (Ear side)
SENTRY_DSN_EAR=https://<ingest>@o<id>.ingest.sentry.io/<project-id>
SENTRY_ORG_SLUG=po-zm
# (информационно) linked projects:
# SENTRY_LINKED_PROJECTS=python-fastapi,krab-ear-agent,krab-ear-backend
```

## Expected result

После первого end-to-end voice transcribe из Main в Ear в Sentry UI (Performance → Trace View) ты увидишь:

```
Trace root: Main Krab /api/voice/handle   [project: python-fastapi]
  └─ Main Krab span "ear.http call_ear_backend"  [project: python-fastapi]
       └─ Ear /transcribe                         [project: krab-ear-backend]
            └─ Whisper encode                     [project: krab-ear-backend]
```

В issue-view любой стороны будет блок **Related Issues** с линком в соседний проект по общему `trace_id`.

## Filter by service

В Sentry Discover / Issues:

- `tag:service:krab-main` — только Main
- `tag:service:krab-ear` — только Ear
- `tag:target_service:krab-ear` — только span'ы из Main, которые вызывают Ear (удобно для latency dashboards межсервисных вызовов)

## Что НЕ меняем на Main-стороне

Main Krab уже делает всё нужное — не трогай `krab_ear_client.py`. Если изменишь формат `_trace` в params, обнови и клиент, и этот документ синхронно.
