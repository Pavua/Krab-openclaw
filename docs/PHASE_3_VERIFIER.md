# Phase 3 — Local Draft Verifier (Operational Guide)

Modules: `src/core/local_draft_verifier.py` (S57 P3.1), dashboard
`src/modules/web_routers/health_router.py` (S58), wire-in
`src/openclaw_client.py:3056-3096`. Foundation для Phase 4
(`docs/PHASE_4_CONFIDENCE_GATED_ROUTING.md`).

## 1. Overview

После того как **local primary** (Gemma MLX / LM Studio) генерирует draft,
verifier с вероятностью **P=0.2** запускает fire-and-forget Vertex Gemini Flash
call, который ставит draft'у `quality_score` 0–10. Divergence = `10 − score`
логируется отдельным событием для downstream Prometheus / dashboards.

- **Зачем**: foundation для confidence-gated routing (Phase 4) — без выборки
  качества local share расширять вслепую нельзя.
- **Стоимость**: ~$0.001 / sample (Gemini 2.5 Flash через caramel-anvil bonus
  credits, see Wave 66).
- **Production status**: enabled в S62 main flow, постоянно гоняет sample на
  каждом local primary draft.
- **Hot-path impact**: 0. `asyncio.create_task(...)` без await; user response
  уже отправлен. Verifier exceptions ловятся, fire-and-forget never raises.

## 2. Activation

```bash
# .env
KRAB_LOCAL_DRAFT_VERIFY_ENABLED=1            # main toggle (default 0)
KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE=0.2      # Bernoulli, clamp [0,1]
KRAB_LOCAL_DRAFT_VERIFY_MODEL=google-vertex/gemini-2.5-flash
KRAB_LOCAL_DRAFT_VERIFY_TIMEOUT_SEC=30
```

**Prerequisites**

1. Local primary активен: route выдаёт `lm-studio-local/*` или
   `mlx-local-kv4/*` (см. `KRAB_LONG_CONTEXT_PROVIDER`).
2. Vertex Gemini Flash доступен (`KRAB_GOOGLE_DIRECT_VERTEX_PREFERRED=1`,
   project `caramel-anvil-492816-t5`).
3. `KRAB_GOOGLE_DIRECT_BYPASS_ENABLED=1` (Wave 18-B) — verifier ходит через
   `integrations/google_genai_direct.complete_direct`.

**Verification**

```bash
# Env флаг
curl -sS http://127.0.0.1:8080/api/admin/local-draft-verifier-stats | jq '.enabled, .sample_rate'
# Live log marker
grep local_draft_verify_started ~/.openclaw/krab_runtime_state/krab_main.log | tail
```

## 3. Dashboard endpoint

`GET /api/admin/local-draft-verifier-stats` — envelope JSON, parsed из
`krab_main.log` за последние 24h (60s TTL cache).

```json
{
  "ok": true,
  "enabled": true,
  "sample_rate": 0.2,
  "stats": {
    "total_verified_24h": 73,
    "divergence_histogram": {"0-2": 51, "3-5": 18, "6-8": 3, "9-10": 1},
    "last_10_samples": [
      {"ts": "2026-05-18 03:17:39", "model": "mlx-local-kv4/gemma-3-27b-it",
       "score": 2, "request_id": "..."}
    ],
    "mean_score": 1.84,
    "median_score": 1.0
  },
  "warnings": []
}
```

- Histogram buckets: `0-2`, `3-5`, `6-8`, `9-10` (см. `_bucket_score`).
- `mean_score` / `median_score` — divergence-side (0 = perfect). `None` при
  пустой выборке.
- Cache TTL = 60s, key хранится в `_LDV_CACHE`. Force refresh: `cache_ttl_sec=0`
  в python API. Warnings включают `log_file_missing` / `log_read_error`.

## 4. Log markers

| Event | Уровень | Когда |
|---|---|---|
| `local_draft_verify_skipped` | debug | reason ∈ {env_disabled, empty_input, not_sampled} |
| `local_draft_verify_started` | info | sampling hit; fields: local_model, verify_model, sample_rate, response_chars |
| `local_draft_verify_ok` | info | verifier returned; fields: elapsed_ms, quality_score, issues |
| `local_draft_verify_divergence_score` | info | **key metric**, парсится dashboard'ом; fields: quality_score, divergence_score |
| `local_draft_verify_failed` | warning | cloud call exception; fields: error, error_type, elapsed_ms |

## 5. Interpretation

| Divergence | Quality | Meaning |
|---|---|---|
| 0–2 | 8–10 | Excellent — local draft неотличим от cloud-grade |
| 3–5 | 5–7 | Acceptable — мелкие пробелы (стиль, форматирование, factual edge) |
| 6–8 | 2–4 | Concerning — investigate prompts / categories |
| 9–10 | 0–1 | Failure — narrow scope, выключать category |

## 6. Decision table (cross-ref Phase 4)

После накопления **≥50 samples**:

| % с divergence ≥ 6 | Action |
|---|---|
| < 5% | Expand local share (расширить task_type whitelist, raise threshold) |
| 5–15% | Maintain — текущий конфиг адекватен, continue sampling |
| > 15% | Narrow scope — отключить категорию из `KRAB_MLX_LOCAL_TASK_TYPES` |

Подробный rollout / rollback см. `docs/PHASE_4_CONFIDENCE_GATED_ROUTING.md`.

## 7. Failure modes

- **Vertex Gemini Flash unavailable** — `complete_direct` бросает → caught →
  `local_draft_verify_failed` log, ничего не падает. Dashboard продолжает
  показывать предыдущие samples.
- **Network slow / timeout** — default `KRAB_LOCAL_DRAFT_VERIFY_TIMEOUT_SEC=30`.
  Превышение → `error_type=TimeoutError` в logged event.
- **Parse error** — `_parse_verify_result` возвращает `quality_score: None`,
  событие `local_draft_verify_ok` пишется, но `divergence_score` не
  генерируется (dashboard sample не учитывается).
- **Empty input** — verifier skip с reason `empty_input` (защита от пустых
  prompts на streaming-failures).

## 8. Troubleshooting

**Q: Почему 0 samples за 24h?**

1. `is_verifier_enabled()` — `KRAB_LOCAL_DRAFT_VERIFY_ENABLED=1` в `.env`?
2. Local primary вообще трогался? `grep local_primary_bypass_ok krab_main.log`.
3. `sample_rate > 0`? `curl .../local-draft-verifier-stats | jq .sample_rate`.
4. Vertex доступен? `grep local_draft_verify_failed krab_main.log | tail`.
5. Log path override? `echo $KRAB_LOG_FILE`.

**Q: Как протестировать вручную?**

```python
import asyncio
from src.core.local_draft_verifier import verify_local_draft

asyncio.run(verify_local_draft(
    user_prompt="What is 2+2?",
    local_response="2+2 equals 4.",
    local_model="mlx-local-kv4/gemma-3-27b-it",
    chat_id="manual_test",
    request_id="manual_001",
))
```

Затем `grep manual_001 krab_main.log`.

**Q: Quality scores выглядят занижено / завышено?**

- Проверить verify_model — Flash должен быть `gemini-2.5-flash`, не Pro.
- Sample size: при <30 samples mean нестабилен.
- Domain skew: если 80% запросов — это translator, scores будут смещены
  относительно общего chat-mix.
- Verifier prompt — system instruction в `_build_verify_prompt`; tuning
  требует A/B и обновления docs.
