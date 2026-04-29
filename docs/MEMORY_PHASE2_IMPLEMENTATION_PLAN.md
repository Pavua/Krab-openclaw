# Memory Phase 2 — Implementation Plan

**Status:** Design complete via code-architect agent, 2026-04-24. Ready for execution.

## Ключевые корректировки к первоначальному пониманию

1. **`memory_hybrid_reranker._semantic_search()` (строки 116-156) уже содержит РАБОЧИЙ vector search код** с `vec_chunks MATCH ? AND k = ?`. Не нужно писать с нуля — перенести в `HybridRetriever._vector_search()`.
2. **`MemoryEmbedder` thread-safety уже реализован** (Fix #3, commit `ad7e453`) через `threading.local`.
3. **72k векторов уже в `vec_chunks`** с правильным rowid после W20 repair. Bootstrap `embed_all_unindexed()` вернёт ~100ms (idempotent).
4. **MMR сейчас делает on-the-fly encode 10 docs** (~50-100ms) — Phase 2 даст **5-10× speedup** через vec_chunks cache.

## 8-этапный план (8 коммитов)

| # | Commit | LOC | Зависимость |
|---|---|---|---|
| C1 | `fix(memory): _vector_search() stub → real implementation` | ~35 | — |
| C2 | `feat(memory): bootstrap embed hook on startup` | ~30 | — (parallel to C1) |
| C3 | `feat(memory): RRF vector weight parametrization` | ~20 | C1 |
| C4 | `perf(memory): MMR vec-cache from vec_chunks` | ~40 | C1 |
| C5 | `feat(memory): dedicated ThreadPoolExecutor for embedder` | ~25 | — (parallel) |
| C6 | `feat(memory): Prometheus metrics retrieval mode + latency` | ~50 | C1 |
| C7 | `feat(memory): embedding version guard + rollback flag` | ~30 | C1, C2 |
| C8 | `test(memory): Phase 2 activation tests + benchmark` | ~200 | все |

**Minimum viable subset:** C1 + C2. Остальные — quality improvements.
**Critical path:** C1 → C7 → C2 (безопасная очерёдность).

## Rollback план

1. `KRAB_RAG_PHASE2_ENABLED=0` default — отключает vector path мгновенно
2. Git revert C1 → FTS-only навсегда
3. `archive.db` не трогается — `vec_chunks` остаётся, просто не используется

## Performance Expectations

| Метрика | До (FTS-only) | После (Hybrid) |
|---|---|---|
| Recall@5 на семантических | ~40-60% | ~70-85% |
| Recall@10 | ~55-70% | ~80-90% |
| Latency P50 total | 15-25ms | 20-35ms |
| **MMR latency** | **50-100ms** | **5-10ms** (10× speedup) |
| Bootstrap cold embed | N/A | ~1-2 sec (72k уже embedded) |

## Prerequisite (MUST do first)

Проверить vec_chunks MATCH syntax в реальной БД v0.1.9:
```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
venv/bin/python -c "
import sqlite3, sqlite_vec, struct
from pathlib import Path
conn = sqlite3.connect(str(Path.home() / '.openclaw/krab_memory/archive.db'))
conn.enable_load_extension(True); sqlite_vec.load(conn); conn.enable_load_extension(False)
q = struct.pack('<256f', *([0.1]*256))
r = conn.execute('SELECT rowid, distance FROM vec_chunks WHERE vector MATCH ? AND k = 5', (q,)).fetchall()
print('KNN OK:', r[:3])
"
```

## Ключевые файлы (absolute paths)

| File | Commits | Op |
|---|---|---|
| `src/core/memory_retrieval.py` | C1, C3, C4, C6, C7 | modify |
| `src/bootstrap/runtime.py` | C2 | modify |
| `src/core/memory_embedder.py` | C5, C7 | modify |
| `src/core/memory_archive.py` | C7 | modify (DDL) |
| `src/core/prometheus_metrics.py` | C6 | modify |
| `src/core/memory_indexer_worker.py` | C5 | modify |
| `tests/unit/test_memory_retrieval.py` | C8 | modify |
| `tests/unit/test_memory_phase2_integration.py` | C8 | create |
| `scripts/benchmark_memory_phase2.py` | C8 | create |

Полная детализированная архитектура в транскрипте architect agent от 2026-04-24 (tool use id `aa366a9c195897754`).
