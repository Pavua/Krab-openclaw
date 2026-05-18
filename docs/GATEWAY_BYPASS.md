# Gateway Bypass — Local Primary Models

Local primary models (`lm-studio-local/*`, `mlx-local-kv4/*`) skip the entire
OpenClaw Gateway chain and go directly to the local backend (LM Studio :1234
or MLX :8088). Live since S53 P4 (`cb962f3`).

This is distinct from the emergency `KRAB_OPENCLAW_BYPASS_ENABLED` switch
(see `docs/OPENCLAW_BYPASS_GUIDE.md`) — Gateway Bypass only triggers for
specific model namespaces and runs alongside normal cloud routing.

## When bypass triggers

The bypass fires automatically when the selected primary model id has one
of these namespace prefixes:

| Prefix | Backend | Default port |
|---|---|---|
| `lm-studio-local/` | LM Studio | `:1234` |
| `mlx-local-kv4/` | MLX local | `:8088` |

Example:

- `lm-studio-local/gemma-4-26b-a4b-it@4bit` → LM Studio direct
- `mlx-local-kv4/gemma-3-12b-it-4bit` → MLX direct
- `google/gemini-3-pro-preview` → normal Gateway path (no bypass)
- `codex-cli/gpt-5.5` → normal Gateway path (no bypass)

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `KRAB_LOCAL_PRIMARY_BYPASS_ENABLED` | `1` | Master switch |
| `KRAB_LOCAL_PRIMARY_BYPASS_LOG_IDLE_INTERVAL_SEC` | `60` | Rate limit for idle skip log (S55 D) |
| `MLX_LOCAL_KV4_URL` | `http://127.0.0.1:8088` | MLX endpoint override |
| `KRAB_LM_STUDIO_URL` | `http://127.0.0.1:1234` | LM Studio endpoint override |

## S55 D — Idle observability

S55 D (`3f827aa`) added rate-limited skip logging so we can see when bypass
**would** have fired but didn't (e.g. primary is cloud, request takes Gateway
path). Marker:

```
INFO local_primary_bypass_idle_skip reason=primary_is_cloud model=codex-cli/gpt-5.5
```

Rate-limited to one log per `reason` per
`KRAB_LOCAL_PRIMARY_BYPASS_LOG_IDLE_INTERVAL_SEC` seconds (default 60s) to
avoid log spam.

The companion success marker is `local_primary_bypass_ok`:

```
INFO local_primary_bypass_ok model=lm-studio-local/gemma-4-26b-a4b-it@4bit \
     resolved_url=http://127.0.0.1:1234 latency_ms=412
```

## Verification

### test_ping endpoint

```bash
curl -sS -X POST http://127.0.0.1:8080/api/admin/model/test_ping \
    -H 'content-type: application/json' \
    -d '{"model":"lm-studio-local/gemma-4-26b-a4b-it@4bit"}' | python3 -m json.tool
```

Expected output:

```json
{
    "ok": true,
    "resolved_url": "http://127.0.0.1:1234",
    "response": "pong!",
    "latency_ms": 12200
}
```

`resolved_url` must point at the local backend, not the Gateway (`:18789`).
First call includes model-load time; subsequent calls drop to ~200-500ms.

### Log markers

```bash
LOG=~/.openclaw/krab_runtime_state/krab_main.log
grep "$(date '+%Y-%m-%d')" $LOG | grep -E "local_primary_bypass_(ok|idle_skip)" | tail
```

### Live chat test

1. Switch primary to a local model:
   ```bash
   curl -X POST http://127.0.0.1:8080/api/admin/model/switch \
       -d '{"model":"lm-studio-local/gemma-4-26b-a4b-it@4bit"}'
   ```
2. Send a Telegram message to Krab.
3. Confirm `local_primary_bypass_ok` increments in the log.

## Latency vs Gateway

Approximate end-to-end roundtrip (S55 measurement, ~10-word prompt):

| Path | p50 | Notes |
|---|---|---|
| Gateway (`codex-cli/gpt-5.5`) | ~3.5s | Subprocess + cloud RTT |
| Gateway (`google/gemini-3-pro-preview`) | ~2.2s | Vertex direct |
| Bypass (`lm-studio-local/gemma-4-26b-a4b-it@4bit`) | ~1.8s | LM Studio direct |
| Bypass (`mlx-local-kv4/*`) | ~1.5s | MLX direct, even faster |

Bypass saves 200-700ms by skipping Gateway HTTP hop + routing layer +
semaphore queue. Most savings come from eliminating the OpenClaw chain
overhead, not from the LLM call itself.

## See also

- `src/openclaw_client.py` — `_direct_lm_fallback`, bypass entry point
- `src/core/model_manager.py` — namespace prefix stripping (S53 P3.5)
- `docs/OPENCLAW_BYPASS_GUIDE.md` — emergency full bypass (different feature)
- Commit `cb962f3` — feat(openclaw): Gateway bypass для local primary (S53 P4)
- Commit `3f827aa` — idle observability (S55 D)
