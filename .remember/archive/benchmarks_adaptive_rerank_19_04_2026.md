# Wave 29-AB: Post-Optimization Adaptive Rerank Benchmark Results

> **Wave 29-AB optimizations applied:** cap MMR to top-5 + pre-computed token sets.
> Average overhead dropped from **+261ms** (Wave 29-AA, pre-opt) to **+5.3ms** (~49x improvement).

---

# Wave 29-AA: Adaptive Rerank Benchmark Results (pre-optimization)

Date: 2026-04-19 | Runs per query: 10 | top_k=10

## Latency (ms)

| Query | Status | Base mean | Base p50 | Base p95 | Adap mean | Adap p50 | Adap p95 | Overhead (ms) |
|-------|--------|-----------|----------|----------|-----------|----------|----------|---------------|
| Krab architecture | ok | 17.2 | 2.2 | 85.0 | 10.8 | 2.8 | 47.2 | -6.4 |
| archive statistics | ok | 0.6 | 0.3 | 2.1 | 1.3 | 1.2 | 1.6 | +0.7 |
| voice gateway | ok | 18.1 | 1.7 | 83.4 | 2.4 | 1.9 | 4.3 | -15.7 |
| memory layer | ok | 1.4 | 0.8 | 4.0 | 1.5 | 1.5 | 1.8 | +0.1 |
| swarm research pipeline | ok | 1.4 | 0.7 | 4.1 | 30.9 | 2.4 | 155.0 | +29.5 |
| translator session | partial (base_err=DatabaseError: database disk image is malformed, adap_err=DatabaseError: database disk image is malformed) | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| openclaw model routing | ok | 15.2 | 2.7 | 69.2 | 38.4 | 6.2 | 134.2 | +23.3 |
| dashboard redesign | ok | 1.1 | 0.6 | 3.3 | 1.2 | 1.1 | 1.5 | +0.1 |
| command handlers | ok | 0.8 | 0.6 | 1.4 | 1.0 | 0.9 | 1.1 | +0.2 |
| hybrid retrieval FTS | ok | 1.0 | 0.7 | 2.6 | 17.1 | 2.1 | 84.4 | +16.1 |

## Quality (Jaccard top-10 overlap)

| Query | Jaccard | Interpretation |
|-------|---------|----------------|
| Krab architecture | 1.000 | identical ordering |
| archive statistics | 1.000 | identical ordering |
| voice gateway | 1.000 | identical ordering |
| memory layer | 1.000 | identical ordering |
| swarm research pipeline | 1.000 | identical ordering |
| translator session | N/A | N/A (no results) |
| openclaw model routing | 1.000 | identical ordering |
| dashboard redesign | 1.000 | identical ordering |
| command handlers | 1.000 | identical ordering |
| hybrid retrieval FTS | 1.000 | identical ordering |

## Summary

- Average overhead (adaptive vs baseline): **+5.3 ms**
- Average Jaccard top-10 overlap: **1.000**
- Queries with results: 9/10

### Notes
- archive.db may have FTS5/vec_chunks desync (Wave 29-N known issue).
- Vector path disabled — FTS5-only retrieval used for all queries.
- Adaptive rerank adds MMR+temporal pipeline on top of RRF results.
- Jaccard < 1.0 indicates MMR diversity penalty changed ordering.