"""
Hybrid re-ranker для Memory Layer retrieval.

Combines FTS5 BM25 rankings + Model2Vec semantic scores через
Reciprocal Rank Fusion (RRF) — стандартный подход для hybrid search.

Formula: score(chunk) = sum(1 / (k + rank_in_source)) для каждого source,
где k=60 (classic RRF constant).

Архитектура:
  * `_fts_search` — BM25 через messages_fts (FTS5 external content over chunks).
  * `_semantic_search` — cosine similarity через sqlite-vec vec_chunks (MATCH/KNN);
    если vec_chunks недоступна — возвращаем [] и RRF деградирует до FTS-only.
  * `rrf_combine` — чистая функция, легко тестируется без БД.
  * `hybrid_search` — публичный API, возвращает топ-K `SearchResult` с enriched text.

Отличия от `memory_retrieval.HybridRetriever`:
  * Проще API (string-based chunk_id, без context/decay/chat filter).
  * Exposes fts_rank/semantic_score/sources в результате — для dashboard/API.
  * RRF как первоклассный комбинатор, не fused в общий pipeline.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from structlog import get_logger

logger = get_logger(__name__)

#: Канонический путь production-БД. Тесты патчат через monkeypatch.
ARCHIVE_DB = Path("~/.openclaw/krab_memory/archive.db").expanduser()

#: Classic RRF constant (Cormack et al. 2009) — сглаживает вклад низких рангов.
RRF_K = 60


@dataclass
class SearchResult:
    """Результат hybrid поиска с расшифровкой вклада каждого source."""

    chunk_id: str
    text: str = ""
    fts_rank: Optional[float] = None
    semantic_score: Optional[float] = None
    rrf_score: float = 0.0
    sources: list[str] = field(default_factory=list)


def _fts_search(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[tuple[str, float]]:
    """FTS5 BM25 поиск. Возвращает [(chunk_id, abs_rank), ...] от лучшего к худшему.

    messages_fts.rank — отрицательное число (меньше = лучше); abs для читаемости.
    """
    safe = _escape_fts5(query)
    if not safe:
        return []
    try:
        cur = conn.execute(
            """
            SELECT c.chunk_id, f.rank
            FROM messages_fts AS f
            JOIN chunks AS c ON c.rowid = f.rowid
            WHERE f.text_redacted MATCH ?
            ORDER BY f.rank
            LIMIT ?;
            """,
            (safe, limit),
        )
        return [(row[0], abs(float(row[1]))) for row in cur.fetchall()]
    except sqlite3.OperationalError as exc:
        logger.warning("hybrid_fts_failed", error=str(exc), query=query[:80])
        return []


def _load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Пытается загрузить sqlite-vec extension. True если ок, False если нет."""
    try:
        import sqlite_vec  # type: ignore[import-not-found]

        conn.enable_load_extension(True)
        try:
            sqlite_vec.load(conn)
        finally:
            conn.enable_load_extension(False)
        return True
    except Exception as exc:  # noqa: BLE001 — extension строго optional
        logger.debug("hybrid_sqlite_vec_unavailable", error=str(exc))
        return False


def _encode_query(query: str) -> Optional[bytes]:
    """Late-load Model2Vec и возвращает query-embedding как float32 bytes.

    None при любом сбое (нет модели, нет numpy, exception в encode).
    """
    try:
        from model2vec import StaticModel  # type: ignore[import-not-found]

        from src.core.memory_embedder import DEFAULT_MODEL_NAME, serialize_f32
    except Exception as exc:  # noqa: BLE001
        logger.warning("hybrid_semantic_import_failed", error=str(exc))
        return None
    try:
        model = StaticModel.from_pretrained(DEFAULT_MODEL_NAME)
        vec = model.encode([query])[0]
        return serialize_f32(vec)
    except Exception as exc:  # noqa: BLE001
        logger.warning("hybrid_semantic_encode_failed", error=str(exc))
        return None


def _semantic_search(
    conn: sqlite3.Connection, query: str, limit: int = 50
) -> list[tuple[str, float]]:
    """Vector KNN через sqlite-vec. [(chunk_id, similarity), ...] от лучшего к худшему.

    similarity = 1 - (cosine_distance / 2), нормализация в [0, 1].
    При отсутствии vec_chunks / extension / модели — возвращает [].
    """
    if not _load_sqlite_vec(conn):
        return []
    # Проверяем, что vec_chunks существует (после Phase 2 encoding).
    try:
        conn.execute("SELECT 1 FROM vec_chunks LIMIT 1;").fetchone()
    except sqlite3.OperationalError:
        logger.debug("hybrid_vec_chunks_missing")
        return []

    q_blob = _encode_query(query)
    if q_blob is None:
        return []

    # Observe sqlite-vec MATCH latency (HNSW migration trigger при p95 > 100ms).
    _vec_hist = None
    try:
        from src.core.prometheus_metrics import _vec_query_duration_seconds

        _vec_hist = _vec_query_duration_seconds
    except Exception:  # noqa: BLE001 - prometheus_client optional
        _vec_hist = None

    try:
        if _vec_hist is not None:
            ctx = _vec_hist.labels(k=str(limit)).time()
        else:
            from contextlib import nullcontext

            ctx = nullcontext()
        with ctx:
            cur = conn.execute(
                """
                SELECT c.chunk_id, v.distance
                FROM vec_chunks AS v
                JOIN chunks AS c ON c.rowid = v.rowid
                WHERE v.vector MATCH ?
                  AND k = ?
                ORDER BY v.distance;
                """,
                (q_blob, limit),
            )
            rows = cur.fetchall()
        results = []
        for chunk_id, dist in rows:
            sim = max(0.0, 1.0 - float(dist) / 2.0)
            results.append((chunk_id, sim))
        return results
    except sqlite3.OperationalError as exc:
        logger.warning("hybrid_vec_search_failed", error=str(exc))
        return []


def rrf_combine(
    fts_results: list[tuple[str, float]],
    semantic_results: list[tuple[str, float]],
    k: int = RRF_K,
) -> list[SearchResult]:
    """Reciprocal Rank Fusion: комбинирует два ранкинга в один score.

    Для каждого chunk_id: rrf_score = Σ 1/(k + rank_in_source).
    Возвращает SearchResult'ы отсортированные по rrf_score desc.
    """
    by_id: dict[str, SearchResult] = {}

    for rank, (chunk_id, fts_score) in enumerate(fts_results, start=1):
        if chunk_id not in by_id:
            by_id[chunk_id] = SearchResult(chunk_id=chunk_id)
        by_id[chunk_id].fts_rank = fts_score
        by_id[chunk_id].rrf_score += 1.0 / (k + rank)
        by_id[chunk_id].sources.append("fts")

    for rank, (chunk_id, sim_score) in enumerate(semantic_results, start=1):
        if chunk_id not in by_id:
            by_id[chunk_id] = SearchResult(chunk_id=chunk_id)
        by_id[chunk_id].semantic_score = sim_score
        by_id[chunk_id].rrf_score += 1.0 / (k + rank)
        by_id[chunk_id].sources.append("semantic")

    return sorted(by_id.values(), key=lambda r: -r.rrf_score)


def hybrid_search(query: str, limit: int = 10) -> list[SearchResult]:
    """Публичный API: hybrid FTS + semantic через RRF.

    Поведение:
      * пустой query / отсутствующая БД → [];
      * FTS5 path обязателен, semantic path — опционален;
      * итог — топ-K SearchResult'ов с `text` вытащенным из chunks.
    """
    if not query or not query.strip():
        return []

    if not ARCHIVE_DB.exists():
        logger.warning("hybrid_archive_db_missing", path=str(ARCHIVE_DB))
        return []

    conn = sqlite3.connect(f"file:{ARCHIVE_DB}?mode=ro", uri=True)
    try:
        fts = _fts_search(conn, query, limit=50)
        sem = _semantic_search(conn, query, limit=50)
        combined = rrf_combine(fts, sem)
        top = combined[:limit]

        if top:
            placeholders = ",".join("?" * len(top))
            rows = conn.execute(
                f"SELECT chunk_id, text_redacted FROM chunks WHERE chunk_id IN ({placeholders});",
                [r.chunk_id for r in top],
            ).fetchall()
            text_by_id = {cid: txt for cid, txt in rows}
            for r in top:
                r.text = text_by_id.get(r.chunk_id, "")
        return top
    finally:
        conn.close()


def _escape_fts5(query: str) -> str:
    """Простой escape для FTS5 MATCH: убираем операторы, OR'им токены."""
    cleaned = "".join(ch if ch.isalnum() or ch in " -_" else " " for ch in query)
    parts = [f'"{tok}"' for tok in cleaned.split() if tok]
    return " OR ".join(parts)
