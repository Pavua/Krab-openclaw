# Unified Model Picker Guide

Quick guide по 7 provider groups на `/admin/models` — какая модель когда выбирается,
чем отличаются похожие, и как работает `set_primary` / `test_ping` / fallback chain.

Targeting: owner-панель `http://127.0.0.1:8080/admin/models` (Wave 144 architecture,
расширенная Waves 230/232/235/239/240).

---

## 1. Что такое provider groups

До Wave 144 страница `/admin/models` показывала плоский список ~50 моделей без группировки.
Wave 144 ввёл **7 provider groups** — каждая group это набор моделей одной природы
(один SDK, одна квота, один transport). Это даёт owner'у:

- **Mental model**: cloud vs CLI vs local
- **Быстрый сравнительный выбор** внутри одной группы (Vertex Gemini 3 Pro vs 3 Flash)
- **Quota awareness**: group-level индикаторы paid/free, rate limits, daily caps
- **Discovery hooks**: LM Studio (Wave 239) и MLX (Wave 240) теперь auto-refresh

Архитектура: `src/core/model_groups.py` + `_render_admin_models_html()` в `web_app.py`.

---

## 2. 7 groups — обзор

| Group | Source | Tools | Latency | Cost | Когда выбирать |
|---|---|---|---|---|---|
| **Vertex AI** | google-cloud Vertex SDK | да | 1–4s | €/day по billing | Production-grade, Google quota, Gemini 3 + Claude через Vertex |
| **Anthropic** | direct anthropic SDK | да | 1–3s | $/1M tok | Claude API напрямую (если ключ активен) |
| **Codex CLI** | `codex-cli` subprocess | да | 5–30s | quota daily | Code generation, длинные patches |
| **Gemini CLI** | `gemini-cli` subprocess | да | 4–20s | quota daily | Бесплатный fallback на Gemini через OAuth |
| **LM Studio Local** | http :1234 / discovery | частично | 0.3–3s | 0 | Эксперимент, off-cloud работа, 84 моделей discovery (Wave 239) |
| **MLX KV4 Local** | http :8088 RotorQuant | частично | 0.2–1.5s | 0 | Production local stack, Gemma OptiQ, dynamic list (Wave 240) |
| **OpenClaw** | gateway :18789 routing | да | varies | varies | Smart routing — default `openclaw/main` |

### Vertex AI

Модели: `vertex/gemini-3-pro-preview`, `vertex/gemini-3-flash-preview`, `vertex/claude-opus-4-7`, etc.

Когда что:
- **3 Pro** — основной чат, tools, long context (1M)
- **3 Flash** — translator, быстрые reply, batch ops
- **Claude via Vertex** — когда нужен Claude, но нет direct Anthropic квоты

### Anthropic

Direct API. Используется редко — обычно Claude идёт через Vertex (один billing).
Если `ANTHROPIC_API_KEY` есть — модели появляются в группе.

### Codex CLI

Subprocess `codex-cli` через OAuth. Multi-account rotation в `~/.codex_accounts/`.
Лучшее для **code generation, длинных patches, agentic workflows**. Daily quota.

### Gemini CLI

Subprocess `gemini-cli` через OAuth (Google free tier). Fallback когда Vertex paid quota исчерпана.

### LM Studio Local

Локальный inference на M4 Max. **Wave 239: auto-discovery 84 моделей** (60s cache).
Хорошо для experimentation — qwen3-4b-instruct, gemma-3-12b-it, phi-4. Tool support — частичный.

### MLX KV4 Local

RotorQuant stack на :8088, dynamic list (Wave 240, 30s cache).
**Production local**: `mlx-local-kv4/gemma-4-26b`, Gemma OptiQ varianты. KV4 quantization — ниже RAM, выше throughput.

### OpenClaw

Smart gateway routing через `:18789`. Default alias `openclaw/main` → текущий primary
через routing config. **Recommended default для чата с tools.**

---

## 3. Best model for — quick lookup

| Задача | Рекомендация | Почему |
|---|---|---|
| Чат с инструментами (default) | `openclaw/main` или `vertex/gemini-3-pro-preview` | Smart routing + tools + 1M context |
| Translator (быстро) | `vertex/gemini-3-flash-preview` | Latency приоритет, качество достаточное |
| Быстрый локальный без tools | `mlx-local-kv4/gemma-4-26b` (:8088) | 0.2s first token, 0 cost |
| Эксперимент с small model | `lm-studio/qwen3-4b-instruct` | Быстро crank через 84 моделей |
| Code generation | `codex-cli/gpt-5.5` | Лучшее качество patches, но 5–30s |
| Free Gemini fallback | `gemini-cli/gemini-3-pro` | OAuth free tier, нет paid billing |
| Voice TTS | — | TTS engine, не зависит от model picker |
| Vision (OCR/image analysis) | `vertex/gemini-3-pro-preview` | Multimodal + best quality |
| Long context (>200k) | `vertex/gemini-3-pro-preview` (1M) | Vertex держит 1M; CLI subprocess рискует timeout |
| Batch / cron jobs | `vertex/gemini-3-flash-preview` | Cost-efficient на массовых запросах |

---

## 4. Performance reference

Замеры на M4 Max 36GB (Sessions 38–44, измерено через `test_ping` Wave 232).

| Model | First token | Full response | Tokens/sec | Cost | RAM (local) |
|---|---|---|---|---|---|
| `vertex/gemini-3-pro-preview` | 0.8s | 2–4s | 80–150 | ~$1.25/1M in | — |
| `vertex/gemini-3-flash-preview` | 0.3s | 1–2s | 200–350 | ~$0.10/1M in | — |
| `vertex/claude-opus-4-7` | 1.2s | 3–6s | 50–90 | ~$15/1M in | — |
| `anthropic/claude-opus-4-7` | 1.1s | 3–5s | 50–90 | ~$15/1M in | — |
| `codex-cli/gpt-5.5` | 3–8s | 10–30s | varies | daily quota | — |
| `gemini-cli/gemini-3-pro` | 2–5s | 5–20s | varies | OAuth free | — |
| `lm-studio/qwen3-4b-instruct` | 0.2s | 0.5–2s | 90–140 | 0 | ~3 GB |
| `lm-studio/gemma-3-12b-it` | 0.5s | 1.5–4s | 30–55 | 0 | ~8 GB |
| `mlx-local-kv4/gemma-4-26b` | 0.2s | 0.8–1.5s | 60–90 | 0 | ~14 GB (KV4) |
| `openclaw/main` (Vertex 3 Pro) | 0.9s | 2–4s | 80–150 | ~$1.25/1M in | — |

**Memory hint:** не запускать 2+ local моделей одновременно — RAM overflow.
Тестировать ONE AT A TIME (см. `feedback_lmstudio_testing` memory).

---

## 5. `set_primary` mechanics (Wave 230)

Кликаешь "Set primary" на карточке модели → POST на `/api/admin/models/set_primary`
с `model_id`. Что происходит:

1. **Запись** в `~/.openclaw/krab_runtime_state/active_model.json`
   `{"primary_model_id": "...", "set_at": "...", "set_by": "owner_panel"}`
2. **ENV check**: если `KRAB_PRIMARY_MODEL_ID` задан — он overrides file (force override).
3. **Routing reload**: ProviderManager перечитывает active_model на следующем request
   (lazy, не нужен restart).
4. **Wave 235 fix**: запись async через `aiofiles` — не блокирует event loop под нагрузкой
   (раньше sync write мог дать 50–200ms pause на slow disk).

Проверка текущего primary: `cat ~/.openclaw/krab_runtime_state/active_model.json`
или `curl http://127.0.0.1:8080/api/model/status | jq .primary`.

---

## 6. `test_ping` mechanics (Wave 232)

Кликаешь "🏓 Test" на карточке → POST `/api/admin/models/test_ping` с `model_id`.

Что измеряется:
- **Реальный probe**: отправляется `"ping, respond with 'ok'"` через тот же transport,
  что использует runtime (не fake call).
- **First token latency** (TTFB stream)
- **Full response latency**
- **Tokens/sec** (если provider возвращает usage)
- **Health flag** (`ok` / `degraded` / `failed`)

Результат показывается inline на карточке в виде badge (зелёный/жёлтый/красный)
+ tooltip с числами. Кешируется на 5 минут (повторный клик в течение 5 мин — cached).

Где смотреть: на самой карточке после клика; full history — `/api/admin/models/ping_history`.

---

## 7. Fallback chain (Waves 47, 67, 86, 217)

Если primary fails — автоматический fallback по chain:

```
primary → vertex/gemini-3-flash-preview → gemini-cli → lm-studio local → MLX local → error
```

**Wave 47**: extended fallback chain с 📡 model footer в response (виден conkretная модель).

**Wave 67 — paid_gemini_guard**: paid AI Studio (`generativelanguage.googleapis.com` с
billing) blocked в fallback — слишком дорого если бы default fallback ушёл туда.
Vertex paid OK (там другой billing с budgets).

**Wave 86 — pressure_aware_select**: runtime считает recent error rate, latency p95,
rate limit headroom. При high pressure (>3 errors / 60s или p95 >10s) — skip primary,
сразу прыг на flash/local.

**Wave 217**: fallback chain теперь учитывает model_groups — если Vertex group целиком
deграда (e.g., billing quota), все Vertex модели в группе помечаются `degraded`, и
chain пропускает их без попытки.

Логи fallback: `route_switch_log` (Wave 48-B) — `tail ~/.openclaw/krab_runtime_state/route_switches.jsonl`.

---

## 8. Discovery refresh (Waves 239/240)

### LM Studio (Wave 239)
- Endpoint: `http://127.0.0.1:1234/v1/models`
- Cache: 60 секунд (in-memory)
- Auto-refresh: при загрузке `/admin/models` если cache stale
- Manual refresh: кнопка "🔄" в заголовке группы LM Studio (если есть)

### MLX :8088 (Wave 240)
- Endpoint: `http://127.0.0.1:8088/v1/models`
- Cache: 30 секунд
- Dynamic list — модели подгружаются как RotorQuant их exposes
- Manual refresh: кнопка "🔄" в заголовке группы MLX

### Vertex / Anthropic / CLI
Не используют discovery — модели зашиты в `model_groups.py` (curated list).
Чтобы добавить новую — PR в `model_groups.py` + reload gateway.

---

## Cheat sheet

```
chat + tools  → openclaw/main
fast translate → vertex/gemini-3-flash-preview
local fast    → mlx-local-kv4/gemma-4-26b
experiment    → lm-studio/<any>
code patch    → codex-cli/gpt-5.5
free fallback → gemini-cli/gemini-3-pro
long context  → vertex/gemini-3-pro-preview (1M)
```

Set primary → панель `/admin/models` → клик "Set primary" → ready.
Diagnose → клик "🏓 Test" → читать badge.
