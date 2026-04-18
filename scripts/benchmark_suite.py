#!/usr/bin/env python3
"""
Krab Performance Benchmark Suite.

Measures key operation latencies:
1. FTS5 query (archive.db) — baseline memory search
2. Semantic/hybrid search (Model2Vec + vec_chunks via HybridReranker)
3. Hybrid RRF combine (pure function, no I/O)
4. PII redact on sample text
5. Chunk encoding simulation (ChunkBuilder.add_message)
6. MemoryEmbedder model load (cold, once)
7. FTS5 escape helper (micro-benchmark)
8. PIIRedactor construction cost

Usage:
    python scripts/benchmark_suite.py                    # run all
    python scripts/benchmark_suite.py --module fts       # filter by name
    python scripts/benchmark_suite.py --iterations 200
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Core bench harness
# ---------------------------------------------------------------------------

def bench(name: str, func, iterations: int = 100) -> dict | None:
    """Run func N times, return {p50, p95, p99, mean, max} in ms."""
    timings: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        try:
            func()
        except Exception as exc:  # noqa: BLE001
            print(f"  [SKIP] {name}: {exc}")
            return None
        timings.append((time.perf_counter() - t0) * 1000)

    s = sorted(timings)
    n = len(s)
    return {
        "name": name,
        "iterations": iterations,
        "p50": statistics.median(timings),
        "p95": s[int(0.95 * n)] if n >= 20 else s[-1],
        "p99": s[int(0.99 * n)] if n >= 100 else s[-1],
        "mean": statistics.mean(timings),
        "max": s[-1],
    }


def _print_row(result: dict | None, label: str) -> None:
    if result is None:
        print(f"{label:<42} (skipped — module unavailable or DB missing)")
    else:
        r = result
        print(
            f"{r['name']:<42} "
            f"{r['p50']:>7.3f}ms "
            f"{r['p95']:>7.3f}ms "
            f"{r['p99']:>7.3f}ms "
            f"{r['mean']:>7.3f}ms "
            f"{r['max']:>7.3f}ms"
        )


# ---------------------------------------------------------------------------
# Individual benchmarks
# ---------------------------------------------------------------------------

def bench_fts(iterations: int) -> dict | None:
    """FTS5 BM25 search via messages_fts → chunks join."""
    import sqlite3

    db = Path("~/.openclaw/krab_memory/archive.db").expanduser()
    if not db.exists():
        return None
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        conn.execute("SELECT 1 FROM messages_fts LIMIT 1").fetchone()
    except Exception:  # noqa: BLE001
        conn.close()
        return None

    def query() -> None:
        conn.execute(
            "SELECT chunks.chunk_id, chunks.text_redacted "
            "FROM messages_fts "
            "JOIN chunks ON chunks.rowid = messages_fts.rowid "
            "WHERE messages_fts MATCH 'krab' "
            "LIMIT 5"
        ).fetchall()

    result = bench("FTS5 search (BM25 MATCH 'krab')", query, iterations)
    conn.close()
    return result


def bench_fts_escape(iterations: int) -> dict | None:
    """FTS5 escape helper — pure string processing, should be sub-0.01ms."""
    try:
        from src.core.memory_hybrid_reranker import _escape_fts5
    except ImportError:
        return None

    def op() -> None:
        _escape_fts5("hello \"world\" OR test AND foo*")

    return bench("FTS5 escape helper (pure)", op, iterations)


def bench_rrf_combine(iterations: int) -> dict | None:
    """Pure RRF combine — no I/O, measures fusion math overhead."""
    try:
        from src.core.memory_hybrid_reranker import rrf_combine
    except ImportError:
        return None

    fts_ranks = [(f"chunk_{i}", float(i)) for i in range(50)]
    sem_ranks = [(f"chunk_{49 - i}", float(i) * 0.9) for i in range(50)]

    def op() -> None:
        rrf_combine(fts_ranks, sem_ranks, k=60)

    return bench("RRF combine (50+50 candidates)", op, iterations)


def bench_hybrid_search(iterations: int) -> dict | None:
    """Full hybrid search: FTS5 + semantic RRF (requires archive.db + vec_chunks)."""
    db = Path("~/.openclaw/krab_memory/archive.db").expanduser()
    if not db.exists():
        return None
    try:
        from src.core.memory_hybrid_reranker import hybrid_search
    except ImportError:
        return None

    n = max(iterations // 10, 5)

    def op() -> None:
        hybrid_search("telegram message краб", limit=5)

    return bench("Hybrid search (FTS+RRF, warm)", op, n)


def bench_pii_redact(iterations: int) -> dict | None:
    """PIIRedactor.redact() on realistic mixed-PII text."""
    try:
        from src.core.memory_pii_redactor import PIIRedactor
    except ImportError:
        return None

    redactor = PIIRedactor()
    sample = (
        "Привет! Мой email test@example.com, телефон +7 999 123 45 67, "
        "карта 4532015112830366, крипто bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh, "
        "API key sk-ant-api03-ABCDEF1234567890abcdef-XXXXXXXXXX"
    )

    def op() -> None:
        redactor.redact(sample)

    return bench("PII redact (mixed PII text)", op, iterations)


def bench_pii_redactor_init(iterations: int) -> dict | None:
    """PIIRedactor.__init__() cost — regex compilation."""
    try:
        from src.core.memory_pii_redactor import PIIRedactor
    except ImportError:
        return None

    def op() -> None:
        PIIRedactor()

    return bench("PIIRedactor init (regex compile)", op, max(iterations // 10, 5))


def bench_chunk_builder(iterations: int) -> dict | None:
    """ChunkBuilder.add_message() — pure chunking logic, no I/O."""
    try:
        import datetime

        from src.core.memory_chunking import ChunkBuilder, Message
    except ImportError:
        return None

    text = "Краб получил сообщение от пользователя и обработал его через OpenClaw gateway. " * 5

    def op() -> None:
        cb = ChunkBuilder()
        msg = Message(
            message_id="bench_1",
            chat_id="bench_chat",
            sender_id="bench_user",
            text=text,
            timestamp=datetime.datetime.now(),
        )
        cb.add(msg)
        cb.flush()

    return bench("ChunkBuilder (add_message + flush)", op, iterations)


def bench_memory_retrieval_fts(iterations: int) -> dict | None:
    """HybridRetriever.search() — FTS-only path (no Model2Vec)."""
    db = Path("~/.openclaw/krab_memory/archive.db").expanduser()
    if not db.exists():
        return None
    try:
        from src.core.memory_retrieval import HybridRetriever
    except ImportError:
        return None

    retriever = HybridRetriever()
    n = max(iterations // 5, 10)

    def op() -> None:
        retriever.search("краб команда", top_k=5)

    return bench("HybridRetriever.search (FTS-only)", op, n)


# ---------------------------------------------------------------------------
# Benchmark registry
# ---------------------------------------------------------------------------

BENCHMARKS: list[tuple[str, object]] = [
    ("fts", bench_fts),
    ("fts_escape", bench_fts_escape),
    ("rrf_combine", bench_rrf_combine),
    ("hybrid_search", bench_hybrid_search),
    ("pii_redact", bench_pii_redact),
    ("pii_init", bench_pii_redactor_init),
    ("chunk_builder", bench_chunk_builder),
    ("retrieval_fts", bench_memory_retrieval_fts),
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Krab Performance Benchmark Suite")
    parser.add_argument(
        "--iterations", type=int, default=100,
        help="Number of iterations per benchmark (default: 100)",
    )
    parser.add_argument(
        "--module", type=str, default=None,
        help="Filter benchmarks by name substring (e.g. 'fts', 'pii')",
    )
    args = parser.parse_args()

    targets = BENCHMARKS
    if args.module:
        targets = [(n, f) for n, f in BENCHMARKS if args.module in n]
        if not targets:
            print(f"No benchmarks match filter '{args.module}'. Available: {[n for n, _ in BENCHMARKS]}")
            sys.exit(1)

    header = f"{'Benchmark':<42} {'p50':>9} {'p95':>9} {'p99':>9} {'mean':>9} {'max':>9}"
    sep = "-" * 95
    print(f"\nKrab Performance Benchmark Suite  (iterations={args.iterations})")
    print(sep)
    print(header)
    print(sep)

    results = []
    for key, fn in targets:
        result = fn(args.iterations)
        _print_row(result, key)
        if result:
            results.append(result)

    print(sep)
    if results:
        print(f"\n  Fastest: {min(results, key=lambda r: r['p50'])['name']}  "
              f"(p50={min(results, key=lambda r: r['p50'])['p50']:.3f}ms)")
        print(f"  Slowest: {max(results, key=lambda r: r['p50'])['name']}  "
              f"(p50={max(results, key=lambda r: r['p50'])['p50']:.3f}ms)")
    print()


if __name__ == "__main__":
    main()
