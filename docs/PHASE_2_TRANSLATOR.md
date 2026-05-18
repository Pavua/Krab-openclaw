# Phase 2 — Local Translator (LM Studio)

Phase 2 routes `translate_text` requests to local LM Studio instead of Vertex Gemini Flash.
Live in production since S52 P2 (`16fd019`), end-to-end verified in S54.

## Architecture

Entry point: `src/core/translator_engine.py:_translate_via_lmstudio()`.

```
translate_text(source, target_lang)
    │
    ├─► env KRAB_LOCAL_TRANSLATOR_ENABLED=1 ?
    │       no → fallback to cloud (Vertex Gemini Flash)
    │       yes ↓
    │
    ├─► LM Studio :1234 available ?
    │       no → fallback to cloud
    │       yes ↓
    │
    ├─► POST /v1/chat/completions
    │       model=KRAB_LOCAL_TRANSLATOR_MODEL
    │       system="You are a precise translator..."
    │       user=source
    │
    └─► cache via translation_cache (singleton, TTL 1h)
```

Cache singleton lives in `translator_engine` module scope; tests use the
`translation_cache_fixture` (see S55 C `180be75`) to reset between runs.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `KRAB_LOCAL_TRANSLATOR_ENABLED` | `1` | Master switch for Phase 2 |
| `KRAB_LOCAL_TRANSLATOR_MODEL` | `gemma-4-26b-a4b-it@4bit` | LM Studio model id |
| `KRAB_LOCAL_TRANSLATOR_URL` | `http://127.0.0.1:1234` | LM Studio endpoint |
| `KRAB_LOCAL_TRANSLATOR_TIMEOUT_SEC` | `15.0` | HTTP timeout for translate call |
| `KRAB_LOCAL_TRANSLATOR_CACHE_TTL_SEC` | `3600` | Translation cache TTL |

## Production verification (S54)

EN → RU sample:

```bash
$ curl -sS http://127.0.0.1:8080/api/translate \
    -d '{"text":"the weather is nice today","target_lang":"ru"}'
{"translation":"сегодня хорошая погода","via":"lm_studio","latency_ms":847}
```

ES → RU sample:

```bash
$ curl -sS http://127.0.0.1:8080/api/translate \
    -d '{"text":"hola, como estas","target_lang":"ru"}'
{"translation":"привет, как дела","via":"lm_studio","latency_ms":612}
```

## Latency

Observed in production for ~10-word translations:

| Path | p50 | p95 | Notes |
|---|---|---|---|
| LM Studio (Phase 2) | ~700ms | ~1.0s | Local Gemma 4 26B 4bit |
| Vertex Gemini Flash | ~900ms | ~1.4s | Cloud, includes network RTT |

First-token after model load is slower (~3-5s); LM Studio keeps the model warm
between requests.

## Cost savings

Every `translate_text` call previously consumed Vertex Gemini Flash tokens
(caramel-anvil-492816-t5 bonus credits, ~$0.075/M input / $0.30/M output).
Phase 2 moves 100% of these calls to local — zero marginal cost.

Estimated savings: **2-4k translate calls/week × ~200 tokens avg = ~$0.20/week**
on Vertex, or roughly **$10/year** plus preserved Vertex quota headroom for
higher-value calls (rerank, Gemini direct, paid tier safety).

## Failure modes

- LM Studio process down → automatic fallback to Vertex (logged as
  `translate_local_fallback reason=lm_studio_unreachable`).
- Model not loaded → same fallback path via `is_lm_studio_available()` health
  probe (Wave 41 singleton client lifecycle).
- Timeout exceeded → fallback + Sentry warning.

## See also

- `src/core/translator_engine.py` — implementation
- `src/core/translator_session_state.py` — RT session state
- `docs/CLAUDE_OWNER_PANEL_API.md` — `/admin/translator` admin page (Wave 174)
- Commit `16fd019` — feat(translator): route translate_text to local LM Studio (S52 P2)
