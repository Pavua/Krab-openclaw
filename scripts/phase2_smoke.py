"""
Phase 2 Smoke / Validation Script.

Проверяет что HybridRetriever + MemoryEmbedder wiring действительно работает
против живой БД `~/.openclaw/krab_memory/archive.db`:

  1. Запускает `search()` с KRAB_RAG_PHASE2_ENABLED=0 → baseline FTS-only.
  2. Запускает `search()` с KRAB_RAG_PHASE2_ENABLED=1 → hybrid (vec+fts+RRF).
  3. Печатает latency, hit counts, top-3 preview для обоих прогонов.
  4. Дополнительно — per-chat фильтр (How2AI chat_id=-1001587432709).

Exit 0 если baseline FTS вернул результаты И hybrid вернул результаты.
Exit 1 если что-то сломано (empty result, import error, crash).

Использование:
    cd /Users/pablito/Antigravity_AGENTS/Краб
    venv/bin/python scripts/phase2_smoke.py

Не коммитит ничего, не меняет runtime — только читает БД.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Добавляем репо в path (скрипт живёт в scripts/).
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.core.memory_archive import ArchivePaths  # noqa: E402
from src.core.memory_retrieval import HybridRetriever  # noqa: E402

QUERY = "тест"
HOW2AI_CHAT_ID = "-1001587432709"


def _fmt_ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f}ms"


def _preview(text: str, n: int = 120) -> str:
    t = (text or "").replace("\n", " ").strip()
    return (t[:n] + "…") if len(t) > n else t


def run_search(label: str, flag_value: str, *, chat_id: str | None = None) -> dict:
    """Один прогон search() с заданным значением feature flag."""
    os.environ["KRAB_RAG_PHASE2_ENABLED"] = flag_value

    paths = ArchivePaths.default()
    print(f"\n=== {label} (KRAB_RAG_PHASE2_ENABLED={flag_value}, chat_id={chat_id}) ===")
    print(
        f"archive.db = {paths.db}  exists={paths.db.exists()}  "
        f"size_mb={paths.db.stat().st_size / 1e6:.1f}"
        if paths.db.exists()
        else "NO DB"
    )

    retriever = HybridRetriever(archive_paths=paths)
    t0 = time.perf_counter()
    try:
        results = retriever.search(QUERY, chat_id=chat_id, top_k=10, with_context=0)
    except Exception as exc:  # noqa: BLE001 — smoke должен сообщить, не падать
        print(f"!! search() RAISED: {type(exc).__name__}: {exc}")
        import traceback as _tb

        _tb.print_exc()
        retriever.close()
        return {"ok": False, "hits": 0, "latency": 0.0, "error": str(exc)}
    elapsed = time.perf_counter() - t0

    print(
        f"hits = {len(results)}  total_latency = {_fmt_ms(elapsed)}  "
        f"vec_available = {retriever._vec_available}"
    )

    for i, r in enumerate(results[:3], start=1):
        print(f"  [{i}] score={r.score:.3f}  chat_id={r.chat_id}  ts={r.timestamp.isoformat()}")
        print(f"      {_preview(r.text_redacted)}")

    retriever.close()
    return {
        "ok": len(results) > 0,
        "hits": len(results),
        "latency": elapsed,
        "vec_available": retriever._vec_available,
    }


def inspect_db() -> None:
    """Печатает краткий статус БД: counts, наличие vec_chunks_meta."""
    import sqlite3

    paths = ArchivePaths.default()
    print(f"\n=== DB INSPECT ===")
    if not paths.db.exists():
        print(f"NO DB at {paths.db}")
        return
    conn = sqlite3.connect(str(paths.db))
    try:
        conn.enable_load_extension(True)
        import sqlite_vec

        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        vec = conn.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
        print(f"chunks = {chunks}  vec_chunks = {vec}")
        try:
            meta = conn.execute("SELECT key, value FROM vec_chunks_meta").fetchall()
            print(f"vec_chunks_meta = {meta}")
        except sqlite3.OperationalError as e:
            print(f"vec_chunks_meta: MISSING ({e})")
    finally:
        conn.close()


def main() -> int:
    inspect_db()

    r0 = run_search("Run A: FTS-only baseline", "0")
    r1 = run_search("Run B: Phase2 hybrid (global)", "1")
    r2 = run_search("Run C: Phase2 hybrid (How2AI chat)", "1", chat_id=HOW2AI_CHAT_ID)

    print("\n=== SUMMARY ===")
    print(f"  A (flag=0, FTS-only): hits={r0['hits']}  latency={_fmt_ms(r0['latency'])}")
    print(
        f"  B (flag=1, hybrid):   hits={r1['hits']}  latency={_fmt_ms(r1['latency'])}  "
        f"vec_available={r1.get('vec_available')}"
    )
    print(f"  C (flag=1, per-chat): hits={r2['hits']}  latency={_fmt_ms(r2['latency'])}")

    ok = r0["ok"] and r1["ok"]
    print(f"\nEXIT = {'0 (OK)' if ok else '1 (FAIL)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
