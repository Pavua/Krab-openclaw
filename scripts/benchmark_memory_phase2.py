#!/usr/bin/env python3
"""
Benchmark Memory Phase 2 (C8): FTS-only vs Hybrid на реальной archive.db.

Прогоняет список realistic queries против продакшн-БД
``~/.openclaw/krab_memory/archive.db`` в двух режимах (env-toggle
``KRAB_RAG_PHASE2_ENABLED`` 0/1) и печатает таблицу latency / hits / recall-delta
+ P50/P90/P99 агрегаты.

Использование::

    cd /Users/pablito/Antigravity_AGENTS/Краб
    venv/bin/python scripts/benchmark_memory_phase2.py              # все 20 queries
    venv/bin/python scripts/benchmark_memory_phase2.py --queries 5  # первые 5
    venv/bin/python scripts/benchmark_memory_phase2.py --top-k 20   # top_k=20
    venv/bin/python scripts/benchmark_memory_phase2.py --db /path/to/archive.db

Exit codes:
    0 — benchmark отработал успешно.
    2 — archive.db не найдена.
    3 — runtime-ошибка (impossible import, БД битая).

C8 deliverable: служит baseline'ом для recall/latency tuning.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Позволяем запускать `python scripts/benchmark_memory_phase2.py` из корня репо
# без явного PYTHONPATH=.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Hard-coded queries (mix тем на ru/en).
# ---------------------------------------------------------------------------

DEFAULT_QUERIES: tuple[str, ...] = (
    # Programming / dev.
    "как настроить pyrogram sessions",
    "deadlock sqlite WAL fix",
    "memory phase 2 embedder retrieval",
    # Trading.
    "BTC breakout EMA",
    "funding rate perp futures",
    # Personal / daily.
    "Ольга встреча четверг",
    "пикник в парке Güell",
    # Technical debug.
    "openclaw gateway 403 scope error",
    "LM Studio RAM overflow 36GB",
    "launchagent plist reload",
    # Search / specific names.
    "Mercadona Playwright scraper stealth",
    "Hammerspoon bridge port 10101",
    "Swarm traders coders analysts creative",
    # Web / AI.
    "Brave search API rate limit",
    "Gemini 3 flash translator",
    # Short / ambiguous.
    "docker",
    "coffee",
    # Longer context.
    "how does hybrid retrieval combine FTS5 with vector similarity",
    "почему openclaw нельзя перезапускать через SIGHUP",
    # Russian domain.
    "персональная панель owner :8080 endpoints",
)

DEFAULT_DB = Path("~/.openclaw/krab_memory/archive.db").expanduser()


# ---------------------------------------------------------------------------
# Per-query measurement.
# ---------------------------------------------------------------------------


@dataclass
class QueryRun:
    """Одна пара (mode, query) измерений."""

    mode: str  # "fts" | "hybrid"
    query: str
    latency_ms: float
    fts_hits: int
    vec_hits: int
    merged_hits: int
    final_hits: int


def _instrumented_search(retriever, query: str, top_k: int) -> QueryRun:
    """
    Прогоняет `search(query)` и возвращает QueryRun, подсматривая внутренние
    счётчики через monkey-patch'ей FTS/vec путей.

    mode выводится из env (KRAB_RAG_PHASE2_ENABLED).
    """
    counters = {"fts": 0, "vec": 0, "merged": 0}
    real_fts = retriever._fts_search
    real_vec = retriever._vector_search
    real_mat = retriever._materialize_results

    def spy_fts(conn, q, cid, limit):  # noqa: ANN001
        ids = real_fts(conn, q, cid, limit)
        counters["fts"] = max(counters["fts"], len(ids))
        return ids

    def spy_vec(conn, q, cid, limit):  # noqa: ANN001
        ids = real_vec(conn, q, cid, limit)
        counters["vec"] = len(ids)
        return ids

    def spy_mat(conn, fused, top_k, with_context, decay_fn):  # noqa: ANN001
        counters["merged"] = len(fused)
        return real_mat(conn, fused, top_k, with_context, decay_fn)

    retriever._fts_search = spy_fts  # type: ignore[method-assign]
    retriever._vector_search = spy_vec  # type: ignore[method-assign]
    retriever._materialize_results = spy_mat  # type: ignore[method-assign]

    mode = "hybrid" if os.getenv("KRAB_RAG_PHASE2_ENABLED") == "1" else "fts"
    t0 = time.perf_counter()
    try:
        results = retriever.search(query, top_k=top_k, with_context=0)
    finally:
        # Восстанавливаем оригиналы (retriever переживёт несколько прогонов).
        retriever._fts_search = real_fts  # type: ignore[method-assign]
        retriever._vector_search = real_vec  # type: ignore[method-assign]
        retriever._materialize_results = real_mat  # type: ignore[method-assign]
    latency_ms = (time.perf_counter() - t0) * 1000.0

    return QueryRun(
        mode=mode,
        query=query,
        latency_ms=latency_ms,
        fts_hits=counters["fts"],
        vec_hits=counters["vec"],
        merged_hits=counters["merged"],
        final_hits=len(results),
    )


# ---------------------------------------------------------------------------
# Benchmark runner.
# ---------------------------------------------------------------------------


def run_benchmark(
    db_path: Path,
    queries: Iterable[str],
    top_k: int,
) -> tuple[list[QueryRun], list[QueryRun]]:
    """
    Запускает два прогона (FTS-only + Hybrid) по всем queries.

    Для каждого mode создаётся свежий HybridRetriever — это имитирует
    cold start и исключает кеширование между режимами.
    """
    # Late-import — чтобы early exit (no DB) не тянул model2vec / sqlite_vec.
    try:
        from src.core.memory_archive import ArchivePaths
        from src.core.memory_retrieval import HybridRetriever
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: import failed: {exc}", file=sys.stderr)
        sys.exit(3)

    paths = ArchivePaths(db=db_path, dir=db_path.parent)
    queries_list = list(queries)

    fts_runs: list[QueryRun] = []
    hybrid_runs: list[QueryRun] = []

    # --- Mode 1: FTS-only ---------------------------------------------------
    os.environ["KRAB_RAG_PHASE2_ENABLED"] = "0"
    r_fts = HybridRetriever(archive_paths=paths)
    try:
        # Warmup (JIT sqlite caches, FTS page cache).
        r_fts.search("warmup priming query", top_k=top_k, with_context=0)
        for q in queries_list:
            fts_runs.append(_instrumented_search(r_fts, q, top_k))
    finally:
        r_fts.close()

    # --- Mode 2: Hybrid (FTS + vec) ----------------------------------------
    os.environ["KRAB_RAG_PHASE2_ENABLED"] = "1"
    r_hyb = HybridRetriever(archive_paths=paths)
    try:
        r_hyb.search("warmup priming query", top_k=top_k, with_context=0)
        for q in queries_list:
            hybrid_runs.append(_instrumented_search(r_hyb, q, top_k))
    finally:
        r_hyb.close()

    return fts_runs, hybrid_runs


# ---------------------------------------------------------------------------
# Printing / aggregation.
# ---------------------------------------------------------------------------


def _percentile(values: list[float], p: float) -> float:
    """p ∈ [0, 100]. Linear interpolation между соседними значениями."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _fmt_query(q: str, width: int = 42) -> str:
    return q if len(q) <= width else q[: width - 1] + "…"


def print_table(fts_runs: list[QueryRun], hybrid_runs: list[QueryRun]) -> None:
    """Таблица per-query: query | FTS (hits, ms) | Hybrid (fts+vec → merged, ms)."""
    print()
    print("=" * 130)
    print(f"{'Query':<44} │ {'FTS only':<20} │ {'Hybrid (fts/vec→merged/final)':<40} │ {'Δlat':<8}")
    print("-" * 130)
    for fts_r, hyb_r in zip(fts_runs, hybrid_runs):
        fts_col = f"{fts_r.final_hits:>3} hits {fts_r.latency_ms:>6.1f}ms"
        hyb_col = (
            f"{hyb_r.fts_hits:>3}/{hyb_r.vec_hits:>3}→{hyb_r.merged_hits:>3}"
            f"/{hyb_r.final_hits:>2} {hyb_r.latency_ms:>6.1f}ms"
        )
        delta = hyb_r.latency_ms - fts_r.latency_ms
        delta_col = f"{delta:+6.1f}ms"
        print(f"{_fmt_query(fts_r.query):<44} │ {fts_col:<20} │ {hyb_col:<40} │ {delta_col}")
    print("=" * 130)


def print_summary(fts_runs: list[QueryRun], hybrid_runs: list[QueryRun]) -> None:
    """P50 / P90 / P99 latency + средний recall-boost."""

    def _pct_block(runs: list[QueryRun], label: str) -> None:
        lat = [r.latency_ms for r in runs]
        print(
            f"  {label:<10} "
            f"P50={_percentile(lat, 50):>7.1f}ms  "
            f"P90={_percentile(lat, 90):>7.1f}ms  "
            f"P99={_percentile(lat, 99):>7.1f}ms  "
            f"mean={statistics.mean(lat):>7.1f}ms  "
            f"median_hits={statistics.median([r.final_hits for r in runs]):>4.1f}"
        )

    print()
    print("Summary")
    print("-" * 60)
    _pct_block(fts_runs, "FTS-only:")
    _pct_block(hybrid_runs, "Hybrid:")

    # Recall-boost (hybrid merged − fts-only).
    boosts = [hyb.merged_hits - fts.final_hits for fts, hyb in zip(fts_runs, hybrid_runs)]
    if boosts:
        print(
            f"  Recall Δ:  mean +{statistics.mean(boosts):.2f} merged-vs-fts-only, "
            f"max +{max(boosts)}, min {min(boosts):+d}"
        )

    # Queries где hybrid вернул 0 hits при fts>0 (регрессия!).
    regressions = [
        (fts.query, fts.final_hits, hyb.final_hits)
        for fts, hyb in zip(fts_runs, hybrid_runs)
        if hyb.final_hits < fts.final_hits
    ]
    if regressions:
        print()
        print(f"  Potential regressions ({len(regressions)}): hybrid < fts final_hits")
        for q, f_h, h_h in regressions[:5]:
            print(f"    - {_fmt_query(q, 60)}  fts={f_h} hybrid={h_h}")


# ---------------------------------------------------------------------------
# CLI entry-point.
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Memory Phase 2 retrieval.")
    parser.add_argument(
        "--queries",
        type=int,
        default=len(DEFAULT_QUERIES),
        help=f"Сколько queries из набора прогнать (default: {len(DEFAULT_QUERIES)})",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="top_k для каждого search() (default 10)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Путь к archive.db (default {DEFAULT_DB})",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"ERROR: archive.db не найдена: {args.db}", file=sys.stderr)
        print(
            "Запусти scripts/bootstrap_memory.py сначала или укажи другой --db.",
            file=sys.stderr,
        )
        return 2

    n = max(1, min(args.queries, len(DEFAULT_QUERIES)))
    queries = DEFAULT_QUERIES[:n]

    size_mb = args.db.stat().st_size / (1024 * 1024)
    print(f"Benchmark Memory Phase 2")
    print(f"  DB:      {args.db}  ({size_mb:.0f} MB)")
    print(f"  Queries: {n}")
    print(f"  top_k:   {args.top_k}")
    print()
    print("Running FTS-only pass…", flush=True)
    t0 = time.perf_counter()
    try:
        fts_runs, hybrid_runs = run_benchmark(args.db, queries, top_k=args.top_k)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: benchmark failed: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 3
    total_sec = time.perf_counter() - t0
    print(f"Done in {total_sec:.1f}s total wall-clock.")

    print_table(fts_runs, hybrid_runs)
    print_summary(fts_runs, hybrid_runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
