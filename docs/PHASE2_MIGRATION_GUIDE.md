# Phase 2 Hybrid Memory — Migration Guide

**Target audience:** Krab operator enabling Phase 2 Hybrid Retrieval safely in production.

**Prerequisites:** Session 21 code merged (commits C1–C8 on `fix/daily-review-20260421`).
Archive DB must contain populated `vec_chunks` (72k+ vectors). Verify with:

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
venv/bin/python scripts/phase2_smoke.py
```

Expected: `KNN OK` + vec_chunks count ≈ 72k. If empty, run `scripts/encode_memory_phase2.py` first.

## Step-by-step activation

### 1. Shadow-reads day (optional, recommended)

Runs hybrid queries in parallel with FTS but **returns FTS results to callers** — pure telemetry, zero user impact.

**One-shot activation** (recommended) — добавит env-флаг, рестартит Krab, прогонит smoke `/api/memory/search` и подсчитает `memory_phase2_shadow_compare` в `logs/krab_launchd.out.log`:

```bash
scripts/shadow_reads_enable.sh
```

Или вручную:

```bash
echo "KRAB_RAG_PHASE2_SHADOW=1" >> /Users/pablito/Antigravity_AGENTS/Краб/.env
# Restart Krab
/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command
sleep 5
/Users/pablito/Antigravity_AGENTS/new\ start_krab.command
```

Wait 24h. Logs collect empirical data: recall delta, latency delta, fallback frequency.

```bash
venv/bin/python scripts/analyze_shadow_logs.py
# → outputs Recall@5/10 delta, P50/P95 latency delta, fallback-to-FTS count
```

Review output. If Recall@5 delta ≥ +15% and P95 latency delta ≤ +20ms → proceed.
If latency regression > 50ms or vec_hits = 0 → investigate before enabling.

### 2. Enable Phase 2

```bash
# Remove shadow flag, set enabled flag
sed -i '' '/KRAB_RAG_PHASE2_SHADOW/d' /Users/pablito/Antigravity_AGENTS/Краб/.env
echo "KRAB_RAG_PHASE2_ENABLED=1" >> /Users/pablito/Antigravity_AGENTS/Краб/.env

# Restart
launchctl kickstart -k gui/$(id -u)/ai.krab.core
```

**First query after restart:** cold-start ~100ms (Model2Vec pre-warmed from `cc9829b`).
**Subsequent queries:** P50 20–35ms, MMR 5–10ms.

### 3. Verify

```bash
# Check diag summary
curl -s http://127.0.0.1:8080/api/dashboard/summary | jq '.memory'

# In Telegram: send !diag to Krab
# Expected section:
#   Memory Phase 2: ✅ enabled
#   vec_chunks: 72834
#   last retrieval mode: hybrid

# Prometheus
curl -s http://127.0.0.1:8080/metrics | grep krab_memory_retrieval_mode_total
# Expect: krab_memory_retrieval_mode_total{mode="hybrid"} growing over time
#         krab_memory_retrieval_mode_total{mode="fts"} ≈ flat (only queries with sqlite-vec unavailable)
```

Optionally tune RRF vector weight if hybrid over-weights semantic matches:

```bash
echo "KRAB_RAG_RRF_VEC_WEIGHT=0.8" >> .env   # default 1.0; lower = more BM25 influence
```

### 4. Rollback if issues

Instant rollback (no data loss — `archive.db` untouched):

```bash
# Option A: set flag to 0
sed -i '' 's/KRAB_RAG_PHASE2_ENABLED=1/KRAB_RAG_PHASE2_ENABLED=0/' .env

# Option B: remove the line
sed -i '' '/KRAB_RAG_PHASE2_ENABLED/d' .env

launchctl kickstart -k gui/$(id -u)/ai.krab.core
```

Full code rollback (last resort): `git revert e14e457` reverts C1 — FTS-only permanently.
`vec_chunks` remains in DB, just unused. No migration needed on re-enable.

## Troubleshooting

### `vec_hits = 0 always` (hybrid returns only FTS results)

**Cause:** `vec_chunks_meta` missing or mis-joined — hybrid path filters out vec results with no meta row.

**Fix:**
```bash
venv/bin/python scripts/phase2_smoke.py
# Check: "vec_chunks_meta rows: X / vec_chunks rows: Y"
# If X << Y → rebuild meta:
venv/bin/python scripts/memory_phase2_migration.py --rebuild-meta
```

### Cold latency > 500ms on first query after restart

**Cause:** Model2Vec not pre-warmed (bootstrap hook failed silently).

**Fix:** Check bootstrap log:
```bash
tail -200 ~/.openclaw/krab_runtime_state/logs/krab.log | grep -iE "model2vec|pre-warm|embedder"
# Expected: "Model2Vec pre-warmed: vec_dim=256 in XXms"
# If missing: check Model2Vec model file exists at ~/.cache/huggingface/… and has correct dim
```

Force re-download: `rm -rf ~/.cache/huggingface/hub/models--minishlab--potion-base-8M && restart`.

### `hybrid мёртвый, but flag=1` (flag enabled, still `mode="fts"` in metrics)

**Cause:** `_vec_available=False` at retriever init. Usually sqlite-vec extension failed to load, or memory model dim ≠ 256.

**Diagnostics:**
```bash
venv/bin/python -c "
import sqlite3, sqlite_vec
conn = sqlite3.connect(':memory:')
conn.enable_load_extension(True)
sqlite_vec.load(conn)
print('sqlite-vec version:', conn.execute('SELECT vec_version()').fetchone())
"
# If fails → reinstall: venv/bin/pip install --upgrade sqlite-vec
```

Check dim mismatch:
```bash
venv/bin/python -c "
import sqlite3
c = sqlite3.connect('/Users/pablito/.openclaw/krab_memory/archive.db')
print(c.execute('SELECT name, sql FROM sqlite_master WHERE name=\"vec_chunks\"').fetchone())
# Expect: CREATE VIRTUAL TABLE ... USING vec0(vector FLOAT[256])
"
```
If dim ≠ 256 → full re-encode required: `scripts/encode_memory_phase2.py --force`.

### P95 latency grows over time (memory leak suspicion)

**Cause:** MMR vec-cache not evicting. Inspect cache size:
```bash
curl -s http://127.0.0.1:8080/api/memory/stats | jq '.mmr_cache_bytes'
```
If > 500MB, set `KRAB_RAG_MMR_CACHE_MAX=10000` (default 50000 entries) and restart.

## Related docs

- `docs/SESSION_21_FINAL_REPORT.md` — full Phase 2 context, benchmark expectations
- `docs/MEMORY_PHASE2_IMPLEMENTATION_PLAN.md` — 8-commit architectural plan
- `docs/MEMORY_PHASE2_ACTIVATION.md` — Session 20 carry-over notes
- `scripts/benchmark_memory_phase2.py` — standalone benchmark harness
