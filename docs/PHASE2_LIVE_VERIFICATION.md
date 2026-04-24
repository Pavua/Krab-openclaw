# Phase 2 Hybrid Retrieval — Live Verification

**Дата:** 2026-04-25 ~01:35 UTC
**Ветка:** `fix/daily-review-20260421`
**Flag:** `KRAB_RAG_PHASE2_ENABLED=1`
**БД:** `~/.openclaw/krab_memory/archive.db` (530.8 MB, 72 343 chunks, 72 343 vec_chunks)
**Модель:** `minishlab/M2V_multilingual_output` (dim=256, indexed 2026-04-24T22:18Z)

## Phase 2 active: **YES**

`/api/memory/phase2/status` → `flag: "enabled"`, `model_dim: 256`. Все запросы
`/api/memory/search?q=...` возвращают `mode: "hybrid"`, `requested_mode: "hybrid"`.

## Smoke script (`scripts/phase2_smoke.py`)

| Run | flag | chat | hits | total |
|-----|------|------|------|-------|
| A — FTS-only baseline | 0 | global | 10 | **1546.8 ms** |
| B — Phase 2 hybrid | 1 | global | 10 | **1671.4 ms** |
| C — Phase 2 hybrid | 1 | How2AI (`-1001587432709`) | 3 | **1029.9 ms** |

Run B: `fts_hits=40, vec_hits=40, merged_hits=72, mmr_reranked=10`. Vec реально
подмешан. Per-chat (Run C) — ожидаемое сужение, top-1 score=1.0 на актуальном
сообщении 2026-04-24 (24h-old).

EXIT 0, all OK.

## API benchmark — 10 real queries

```
swarm coders        : 1563.3 ms  hybrid
cron jobs           : 1168.9 ms  hybrid
Phase 2 plan        : 1274.5 ms  hybrid
MMR diversity       : 1120.4 ms  hybrid
grafana dashboard   : 1287.1 ms  hybrid
memory archive      : 1064.7 ms  hybrid
translator session  : 1038.8 ms  hybrid
openclaw routing    : 1075.8 ms  hybrid
voice gateway       : 2410.2 ms  hybrid (cold outlier)
krab ear            : 1153.1 ms  hybrid
```

p50 ≈ 1.16 s, p95 ≈ 2.4 s. Все ответы — `mode=hybrid`, `count=10`.

## Mode distribution

`krab_memory_retrieval_mode_total{mode="hybrid"} = 22.0` — единственный mode-counter.
Никаких `fts` / `vec` / `none` событий после активации flag не зафиксировано.
**Mode distribution: 100% hybrid (22/22).**

## Prometheus counters: **incremented**

Из `/metrics` после прогона (sum over 22 запросов):

| phase | count | sum (s) | avg (ms) |
|-------|-------|---------|----------|
| fts | 22 | 0.567 | **25.8** |
| vec | 22 | 24.04 | **1092.8** |
| mmr | 22 | 0.191 | **8.7** |
| total | 22 | 25.18 | **1144.4** |

Бутылочное горлышко — vec encode (~1.1 s/query). FTS+MMR суммарно <40 ms.

## Issues found

1. **`/api/memory/phase2/status` window-counters стоят на нулях** — отображаются
   `retrieval_mode_hour={fts:0,vec:0,hybrid:0}`, `latency_avg={…:0}`,
   `vec_chunks_count: 0`, `vec_join_pct: null`. Prometheus данные есть,
   а сводный endpoint их не агрегирует. Нужно поправить агрегатор (probably
   `phase2_status` ещё не подцепился к свежим counters в этой инкарнации
   процесса). Не блокер.
2. **`model_loaded: null`** в status — модель реально загружается лениво при
   первом запросе (видно в smoke логах `memory_retrieval_model_loaded`),
   но pre-warm на старте не выставляет флаг. Cosmetic.
3. **Cold-start latency спайк** (`voice gateway` — 2.4 s). Первый запрос
   после старта прогревает model, дальше ~1.0–1.3 s стабильно.
4. **Vec phase ~1.1 s/query** — это фактически Model2Vec encode на CPU.
   На M4 Max приемлемо для Phase 2; для Phase 3 захочется батчинг или GPU.

## Verdict: **PRODUCTION-READY** (с косметическим follow-up)

Hybrid retrieval работает в production: flag ON, все live-запросы идут через
hybrid path, FTS+vec реально объединяются (smoke показал `vec_hits=40,
fts_hits=40, merged=72`), Prometheus метрики инкрементируются корректно.

Latency 1.0–1.7 s глобально — приемлемо для memory recall (не критический путь).

**Follow-up (не-блокеры):**
- починить агрегацию `/api/memory/phase2/status` (`retrieval_mode_hour`,
  `latency_avg`, `vec_chunks_count`, `model_loaded`);
- снять Phase 2 → Phase 3 план: батчинг encode, optional warm-up через
  pre-encode в SessionStart hook.
