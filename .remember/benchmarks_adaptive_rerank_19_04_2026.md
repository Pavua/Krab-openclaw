# Wave 29-AA: Adaptive Rerank Benchmark Results

Date: 2026-04-19 | Runs per query: 10 | top_k=10

## Latency (ms)

| Query | Status | Base mean | Base p50 | Base p95 | Adap mean | Adap p50 | Adap p95 | Overhead (ms) |
|-------|--------|-----------|----------|----------|-----------|----------|----------|---------------|
| Krab architecture | ok | 32.4 | 1.9 | 169.9 | 138.2 | 63.3 | 444.9 | +105.8 |
| archive statistics | ok | 0.9 | 0.5 | 2.7 | 131.8 | 105.2 | 331.4 | +130.9 |
| voice gateway | ok | 2.3 | 1.7 | 5.1 | 395.7 | 339.7 | 930.0 | +393.3 |
| memory layer | ok | 1.8 | 1.2 | 4.6 | 425.4 | 415.5 | 658.8 | +423.6 |
| swarm research pipeline | ok | 1.6 | 0.7 | 5.6 | 258.3 | 242.4 | 556.8 | +256.7 |
| translator session | partial (base_err=DatabaseError: database disk image is malformed, adap_err=DatabaseError: database disk image is malformed) | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| openclaw model routing | ok | 39.9 | 1.8 | 194.9 | 321.9 | 356.5 | 433.9 | +282.0 |
| dashboard redesign | ok | 21.0 | 1.3 | 105.4 | 220.5 | 242.2 | 377.2 | +199.5 |
| command handlers | ok | 21.8 | 1.1 | 102.8 | 119.5 | 146.8 | 213.9 | +97.6 |
| hybrid retrieval FTS | ok | 1.3 | 0.7 | 3.7 | 460.9 | 520.1 | 755.0 | +459.6 |

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

- Average overhead (adaptive vs baseline): **+261.0 ms**
- Average Jaccard top-10 overlap: **1.000**
- Queries with results: 9/10

### Notes
- archive.db may have FTS5/vec_chunks desync (Wave 29-N known issue).
- Vector path disabled — FTS5-only retrieval used for all queries.
- Adaptive rerank adds MMR+temporal pipeline on top of RRF results.
- Jaccard < 1.0 indicates MMR diversity penalty changed ordering.