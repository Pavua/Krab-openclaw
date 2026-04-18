#!/usr/bin/env python3
"""
Benchmark: adaptive rerank overhead vs baseline (Wave 29-AA).

Измеряет:
  1. Latency: p50, p95, p99, mean (ms) на каждый query, baseline vs adaptive.
  2. Quality: Jaccard overlap top-10 IDs между baseline и adaptive.

При poломанном archive.db (FTS5/vec_chunks desync) — корректно перехватывает
исключения и продолжает сбор статистики по тем запросам, которые работают.

Запуск:
    PATH=/opt/homebrew/bin:$PATH venv/bin/python scripts/bench_adaptive_rerank.py

Результаты: stdout + .remember/benchmarks_adaptive_rerank_19_04_2026.md
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

# Корень проекта в sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.memory_retrieval import HybridRetriever  # noqa: E402

# Типичные запросы из реальной эксплуатации Краба
QUERIES = [
    "Krab architecture",
    "archive statistics",
    "voice gateway",
    "memory layer",
    "swarm research pipeline",
    "translator session",
    "openclaw model routing",
    "dashboard redesign",
    "command handlers",
    "hybrid retrieval FTS",
]

RUNS_PER_QUERY = 10
TOP_K = 10


def percentile(data: list[float], p: float) -> float:
    """Простой percentile без numpy."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = (len(sorted_data) - 1) * p / 100.0
    lo = int(idx)
    hi = min(lo + 1, len(sorted_data) - 1)
    frac = idx - lo
    return sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo])


def jaccard(set_a: set, set_b: set) -> float:
    """Jaccard similarity между двумя множествами ID."""
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 1.0
    return len(set_a & set_b) / len(union)


def bench_query(retriever: HybridRetriever, query: str, runs: int) -> tuple[list[float], list[str], str | None]:
    """
    Прогоняет query N раз, возвращает (latencies_ms, top_ids_last_run, error_msg).
    error_msg — None если всё ОК, иначе описание ошибки.
    """
    latencies: list[float] = []
    last_ids: list[str] = []

    for i in range(runs):
        try:
            t0 = time.perf_counter()
            results = retriever.search(query, top_k=TOP_K, with_context=0)
            elapsed = (time.perf_counter() - t0) * 1000.0
            latencies.append(elapsed)
            if i == runs - 1:
                last_ids = [r.message_id for r in results]
        except Exception as exc:
            return latencies, last_ids, f"{type(exc).__name__}: {exc}"

    return latencies, last_ids, None


def run_benchmark() -> str:
    """Запускает benchmark и возвращает markdown-таблицу с результатами."""

    print("=== Wave 29-AA: Adaptive Rerank Benchmark ===\n")

    # Создаём два retriever'а один раз — модель грузится при первом вызове search(),
    # повторные вызовы уже идут из кэша в памяти.
    print("Warming up retrievers (model load, first connection)...")
    os.environ.pop("MEMORY_ADAPTIVE_RERANK_ENABLED", None)
    retriever_base = HybridRetriever()

    os.environ["MEMORY_ADAPTIVE_RERANK_ENABLED"] = "1"
    retriever_adap = HybridRetriever()
    os.environ.pop("MEMORY_ADAPTIVE_RERANK_ENABLED", None)

    # Прогрев обоих (загрузка Model2Vec + открытие БД)
    warmup_q = QUERIES[0]
    try:
        os.environ.pop("MEMORY_ADAPTIVE_RERANK_ENABLED", None)
        retriever_base.search(warmup_q, top_k=TOP_K, with_context=0)
    except Exception:
        pass
    try:
        os.environ["MEMORY_ADAPTIVE_RERANK_ENABLED"] = "1"
        retriever_adap.search(warmup_q, top_k=TOP_K, with_context=0)
    except Exception:
        pass
    finally:
        os.environ.pop("MEMORY_ADAPTIVE_RERANK_ENABLED", None)

    print("Warmup done. Starting benchmark...\n")

    rows: list[dict] = []

    for query in QUERIES:
        print(f"Query: {query!r}")

        # --- Baseline (adaptive rerank OFF) ---
        os.environ.pop("MEMORY_ADAPTIVE_RERANK_ENABLED", None)
        base_lats, base_ids, base_err = bench_query(retriever_base, query, RUNS_PER_QUERY)

        # --- Adaptive rerank ON ---
        os.environ["MEMORY_ADAPTIVE_RERANK_ENABLED"] = "1"
        adap_lats, adap_ids, adap_err = bench_query(retriever_adap, query, RUNS_PER_QUERY)
        os.environ.pop("MEMORY_ADAPTIVE_RERANK_ENABLED", None)

        # Статус
        if base_err or adap_err:
            status = f"partial (base_err={base_err}, adap_err={adap_err})"
        elif not base_lats:
            status = "db_malformed_skipped"
        else:
            status = "ok"

        # Jaccard overlap top-K
        jac = jaccard(set(base_ids), set(adap_ids)) if (base_ids or adap_ids) else None

        def _stats(lats: list[float]) -> dict:
            if not lats:
                return {"mean": None, "p50": None, "p95": None, "p99": None}
            return {
                "mean": statistics.mean(lats),
                "p50": percentile(lats, 50),
                "p95": percentile(lats, 95),
                "p99": percentile(lats, 99),
            }

        b = _stats(base_lats)
        a = _stats(adap_lats)

        # Overhead (delta mean)
        delta_mean = (a["mean"] - b["mean"]) if (a["mean"] is not None and b["mean"] is not None) else None

        rows.append({
            "query": query,
            "status": status,
            "base_mean": b["mean"],
            "base_p50": b["p50"],
            "base_p95": b["p95"],
            "base_p99": b["p99"],
            "adap_mean": a["mean"],
            "adap_p50": a["p50"],
            "adap_p95": a["p95"],
            "adap_p99": a["p99"],
            "delta_mean_ms": delta_mean,
            "jaccard": jac,
        })

        # Краткий print на stdout
        if base_err:
            print(f"  baseline: ERROR — {base_err}")
        elif not base_lats:
            print("  baseline: db_malformed, skipped")
        else:
            print(f"  baseline mean={b['mean']:.1f}ms p50={b['p50']:.1f}ms p95={b['p95']:.1f}ms")

        if adap_err:
            print(f"  adaptive: ERROR — {adap_err}")
        elif not adap_lats:
            print("  adaptive: db_malformed, skipped")
        else:
            print(f"  adaptive mean={a['mean']:.1f}ms p50={a['p50']:.1f}ms p95={a['p95']:.1f}ms")

        if delta_mean is not None:
            print(f"  overhead: {delta_mean:+.1f}ms mean | jaccard={jac:.3f}")
        print()

    retriever_base.close()
    retriever_adap.close()
    return _build_markdown(rows)


def _fmt(v: float | None, fmt: str = ".1f") -> str:
    if v is None:
        return "N/A"
    return format(v, fmt)


def _build_markdown(rows: list[dict]) -> str:
    lines: list[str] = []
    lines.append("# Wave 29-AA: Adaptive Rerank Benchmark Results")
    lines.append("")
    lines.append(f"Date: 2026-04-19 | Runs per query: {RUNS_PER_QUERY} | top_k={TOP_K}")
    lines.append("")
    lines.append("## Latency (ms)")
    lines.append("")
    lines.append("| Query | Status | Base mean | Base p50 | Base p95 | Adap mean | Adap p50 | Adap p95 | Overhead (ms) |")
    lines.append("|-------|--------|-----------|----------|----------|-----------|----------|----------|---------------|")

    for r in rows:
        q = r["query"][:30]
        status = r["status"]
        bm = _fmt(r["base_mean"])
        bp50 = _fmt(r["base_p50"])
        bp95 = _fmt(r["base_p95"])
        am = _fmt(r["adap_mean"])
        ap50 = _fmt(r["adap_p50"])
        ap95 = _fmt(r["adap_p95"])
        delta = _fmt(r["delta_mean_ms"], "+.1f") if r["delta_mean_ms"] is not None else "N/A"
        lines.append(f"| {q} | {status} | {bm} | {bp50} | {bp95} | {am} | {ap50} | {ap95} | {delta} |")

    lines.append("")
    lines.append("## Quality (Jaccard top-10 overlap)")
    lines.append("")
    lines.append("| Query | Jaccard | Interpretation |")
    lines.append("|-------|---------|----------------|")
    for r in rows:
        jac = r["jaccard"]
        if jac is None:
            interp = "N/A (no results)"
            jac_str = "N/A"
        elif jac >= 0.9:
            interp = "identical ordering"
            jac_str = f"{jac:.3f}"
        elif jac >= 0.6:
            interp = "mostly same, minor reorder"
            jac_str = f"{jac:.3f}"
        elif jac >= 0.3:
            interp = "moderate diversity boost"
            jac_str = f"{jac:.3f}"
        else:
            interp = "significant reranking"
            jac_str = f"{jac:.3f}"
        lines.append(f"| {r['query'][:30]} | {jac_str} | {interp} |")

    # Сводка
    ok_rows = [r for r in rows if r["delta_mean_ms"] is not None]
    if ok_rows:
        avg_overhead = statistics.mean(r["delta_mean_ms"] for r in ok_rows)
        avg_jaccard = statistics.mean(r["jaccard"] for r in ok_rows if r["jaccard"] is not None)
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- Average overhead (adaptive vs baseline): **{avg_overhead:+.1f} ms**")
        lines.append(f"- Average Jaccard top-{TOP_K} overlap: **{avg_jaccard:.3f}**")
        lines.append(f"- Queries with results: {len(ok_rows)}/{len(rows)}")
        skipped = [r for r in rows if r["status"] == "db_malformed_skipped"]
        if skipped:
            lines.append(f"- Skipped (db malformed): {len(skipped)} queries")
        lines.append("")
        lines.append("### Notes")
        lines.append("- archive.db may have FTS5/vec_chunks desync (Wave 29-N known issue).")
        lines.append("- Vector path disabled — FTS5-only retrieval used for all queries.")
        lines.append("- Adaptive rerank adds MMR+temporal pipeline on top of RRF results.")
        lines.append("- Jaccard < 1.0 indicates MMR diversity penalty changed ordering.")

    return "\n".join(lines)


def main() -> int:
    md = run_benchmark()

    # Сохраняем в .remember/
    out_path = ROOT / ".remember" / "benchmarks_adaptive_rerank_19_04_2026.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"Results saved: {out_path}")
    print()
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
