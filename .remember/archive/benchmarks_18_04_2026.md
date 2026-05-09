# Krab Performance Baseline (2026-04-18)

## Environment
- Python 3.13, M4 Max 36GB
- archive.db: malformed vec_chunks (hybrid/semantic paths skipped — Phase 2 re-index pending)
- Krab worktree: `claude/fervent-goldstine-2947a2`
- Suite: `scripts/benchmark_suite.py --iterations 100`

## Results

```
Krab Performance Benchmark Suite  (iterations=100)
-----------------------------------------------------------------------------------------------
Benchmark                                        p50       p95       p99      mean       max
-----------------------------------------------------------------------------------------------
FTS5 search (BM25 MATCH 'krab')              0.104ms   1.085ms   8.295ms   0.338ms   8.295ms
FTS5 escape helper (pure)                    0.004ms   0.005ms   0.014ms   0.004ms   0.014ms
RRF combine (50+50 candidates)               0.054ms   0.080ms   0.897ms   0.074ms   0.897ms
hybrid_search                              (skipped — vec_chunks malformed)
PII redact (mixed PII text)                  0.095ms   0.693ms   1.143ms   0.184ms   1.143ms
PIIRedactor init (regex compile)             0.001ms   0.009ms   0.009ms   0.002ms   0.009ms
ChunkBuilder (add_message + flush)           0.003ms   0.024ms   0.076ms   0.006ms   0.076ms
HybridRetriever.search (FTS-only)          (skipped — vec_chunks malformed)
-----------------------------------------------------------------------------------------------
  Fastest: PIIRedactor init (regex compile)  (p50=0.001ms)
  Slowest: FTS5 search (BM25 MATCH 'krab')  (p50=0.104ms)
```

## Notes
- `hybrid_search` и `HybridRetriever.search` пропущены: `database disk image is malformed`
  на vec_chunks — нужен re-index (`scripts/encode_memory_phase2.py`) в Session 13
- FTS5 p99=8ms — spike от page-cache miss (первый холодный запрос); warm p50=0.1ms стабилен
- RRF combine (pure math) стабилен: p50=0.054ms, p99<1ms

## Targets (Session 13+)
| Benchmark              | Baseline p50 | Target        | Notes                          |
|------------------------|-------------|---------------|--------------------------------|
| FTS5 search            | 0.104ms     | <0.2ms warm   | уже в норме, следить за p99    |
| FTS5 escape (pure)     | 0.004ms     | <0.01ms       | достигнуто                     |
| RRF combine            | 0.054ms     | <0.1ms        | достигнуто                     |
| Hybrid search (warm)   | —           | <100ms        | ждёт re-index vec_chunks       |
| PII redact             | 0.095ms     | <0.5ms        | достигнуто                     |
| PIIRedactor init       | 0.001ms     | <0.1ms        | достигнуто                     |
| ChunkBuilder           | 0.003ms     | <0.1ms        | достигнуто                     |

## Action items
- [ ] Re-index vec_chunks (`scripts/encode_memory_phase2.py`) → разблокирует hybrid/semantic бенчи
- [ ] Добавить `bench_model2vec_encode` после Model2Vec bootstrap p0lrd
- [ ] Добавить `bench_context_augmenter` (MemoryContextAugmenter.augment) после Session 13
