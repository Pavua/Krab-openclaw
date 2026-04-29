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

import math
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from structlog import get_logger

logger = get_logger(__name__)

#: Канонический путь production-БД. Тесты патчат через monkeypatch.
ARCHIVE_DB = Path("~/.openclaw/krab_memory/archive.db").expanduser()

#: Classic RRF constant (Cormack et al. 2009) — сглаживает вклад низких рангов.
RRF_K = 60

# ---------------------------------------------------------------------------
# Feature A: Successful Response Retrieval Boost.
# ---------------------------------------------------------------------------

#: Коэффициент при logarithmic positive boost. boost = 1 + log(1+pos) * COEF.
RESPONSE_FEEDBACK_POSITIVE_COEF = 0.3
#: Linear штраф за каждый negative_count. penalty = 1 - neg * PENALTY (clamped).
RESPONSE_FEEDBACK_NEGATIVE_COEF = 0.2
#: Нижний порог penalty: даже сильно "плохие" чанки не уходят полностью в ноль —
#: иначе мы перестаём учиться на негативных примерах.
RESPONSE_FEEDBACK_MIN_MULTIPLIER = 0.1


def _response_feedback_enabled() -> bool:
    """Config-flag: KRAB_RESPONSE_FEEDBACK_BOOST_ENABLED (default on).

    Допустимые off-значения: 0/false/no/off (case-insensitive). Пустая
    переменная → on. Используется только в hybrid_search() — pure-функции
    apply_response_feedback_boost() и compute_*_multiplier() не зависят от env.
    """
    raw = os.environ.get("KRAB_RESPONSE_FEEDBACK_BOOST_ENABLED", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def compute_feedback_multiplier(positive_count: int, negative_count: int) -> float:
    """Чистая формула множителя от positive/negative counters.

    Идемпотентна, монотонна по positive (растёт), монотонна по negative (падает).
    Возвращает 1.0 при отсутствии feedback'а (no-op для не помеченных чанков).
    """
    if positive_count < 0:
        positive_count = 0
    if negative_count < 0:
        negative_count = 0
    boost = 1.0
    if positive_count > 0:
        boost *= 1.0 + math.log(1.0 + positive_count) * RESPONSE_FEEDBACK_POSITIVE_COEF
    if negative_count > 0:
        penalty = 1.0 - negative_count * RESPONSE_FEEDBACK_NEGATIVE_COEF
        boost *= max(RESPONSE_FEEDBACK_MIN_MULTIPLIER, penalty)
    return boost


# ---------------------------------------------------------------------------
# Feature D: Memory Decay (gradual weight loss for old chunks).
# ---------------------------------------------------------------------------

#: Floor для decay multiplier — даже самые старые чанки сохраняют 40% веса,
#: чтобы хорошо подтверждённая древняя память (важные факты) не вымывалась.
DECAY_FLOOR = 0.4
#: Коэффициент скорости угасания: multiplier = max(FLOOR, 1 - COEF * log2(1 + age_days)).
DECAY_COEF = 0.05
#: Recent confirm boost — если validator_confirmed_at < N дней назад, ×1.2.
RECENT_CONFIRM_BOOST = 1.2
RECENT_CONFIRM_WINDOW_DAYS = 7


def _decay_enabled() -> bool:
    """Config-flag: KRAB_MEMORY_DECAY_ENABLED (default on).

    Off-значения: 0/false/no/off (case-insensitive). Пустая → on.
    """
    raw = os.environ.get("KRAB_MEMORY_DECAY_ENABLED", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def compute_decay_multiplier(message_age_days: float) -> float:
    """Чистая формула decay multiplier'а от возраста сообщения в днях.

    Формула: max(DECAY_FLOOR, 1.0 - DECAY_COEF * log2(1 + age_days)).

    Свойства:
      * age=0 → 1.0 (no decay для свежих).
      * монотонна: чем старше, тем меньше (но не ниже DECAY_FLOOR).
      * gradual: log2 — мягкое угасание (день/неделя ~ небольшой штраф,
        месяцы — заметный, годы — упор в floor).
      * negative age (clock skew) трактуется как 0.
    """
    if message_age_days <= 0:
        return 1.0
    raw = 1.0 - DECAY_COEF * math.log2(1.0 + message_age_days)
    return max(DECAY_FLOOR, raw)


def compute_recent_confirm_boost(confirm_age_days: Optional[float]) -> float:
    """Boost ×1.2 если chunk был validator-confirmed в последние N дней.

    None / отрицательный возраст → 1.0 (нет boost).
    """
    if confirm_age_days is None or confirm_age_days < 0:
        return 1.0
    if confirm_age_days <= RECENT_CONFIRM_WINDOW_DAYS:
        return RECENT_CONFIRM_BOOST
    return 1.0


def _parse_iso_timestamp(ts: str | None) -> Optional[datetime]:
    """Парсит ISO-8601 (как из archive.db, с/без 'Z'). None при сбое."""
    if not ts:
        return None
    try:
        s = ts.rstrip("Z")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Idea 15: Time-aware retrieval boost (boost recent, в дополнение к Feature D decay).
# ---------------------------------------------------------------------------

#: Bucket-границы и множители для recency boost. Применяется ПЕРЕД decay:
#: свежие сообщения получают усиление, а более старые попадают в decay-флоу.
RECENCY_BOOST_LAST_HOUR = 1.5  # < 1 часа
RECENCY_BOOST_LAST_DAY = 1.2  # < 24 часов
RECENCY_BOOST_LAST_WEEK = 1.0  # < 7 дней (no-op, отдаём управление decay)
RECENCY_BOOST_HOUR_SECONDS = 3600.0
RECENCY_BOOST_DAY_SECONDS = 86400.0
RECENCY_BOOST_WEEK_SECONDS = 604800.0


def _recency_boost_enabled() -> bool:
    """Config-flag: KRAB_RECENCY_BOOST_ENABLED (default on).

    Off-значения: 0/false/no/off (case-insensitive). Пустая → on.
    """
    raw = os.environ.get("KRAB_RECENCY_BOOST_ENABLED", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def compute_recency_boost(age_seconds: float) -> float:
    """Чистая формула recency boost'а от возраста сообщения в секундах.

    Buckets:
      * < 3600s (1 час)  → 1.5
      * < 86400s (24 ч)  → 1.2
      * < 604800s (7 дн) → 1.0 (no-op — управление передаётся decay)
      * else             → 1.0 (старые чанки ослабляет уже Feature D)

    Свойства:
      * negative age (clock skew) → трактуем как 0 (самый свежий, ×1.5).
      * монотонно невозрастающая по age (внутри активной зоны).
      * идемпотентна.
    """
    if age_seconds < 0:
        age_seconds = 0.0
    if age_seconds < RECENCY_BOOST_HOUR_SECONDS:
        return RECENCY_BOOST_LAST_HOUR
    if age_seconds < RECENCY_BOOST_DAY_SECONDS:
        return RECENCY_BOOST_LAST_DAY
    if age_seconds < RECENCY_BOOST_WEEK_SECONDS:
        return RECENCY_BOOST_LAST_WEEK
    return 1.0


def apply_recency_boost(
    results: list["SearchResult"],
    age_days_map: dict[str, float],
) -> list["SearchResult"]:
    """Применяет recency boost к свежим chunk'ам и пересортирует.

    Args:
        results: вход (после feedback boost, ПЕРЕД decay).
        age_days_map: {chunk_id: age_in_days} — тот же формат, что у apply_decay,
            конвертируется во внутренний секундный масштаб buckets.

    Чанки без записи / с возрастом >= 7д → multiplier=1.0 (no-op, decay сделает
    своё дело). Идемпотентна: повторный вызов с тем же mapping не накапливает.
    """
    if not age_days_map:
        return results
    for result in results:
        age_days = age_days_map.get(result.chunk_id)
        if age_days is None:
            continue
        mult = compute_recency_boost(age_days * 86400.0)
        if mult != 1.0:
            result.rrf_score *= mult
    return sorted(results, key=lambda r: -r.rrf_score)


def apply_decay(
    results: list["SearchResult"],
    age_map: dict[str, float],
    confirm_age_map: dict[str, float] | None = None,
) -> list["SearchResult"]:
    """Применяет decay multiplier (× recent-confirm boost) и пересортирует.

    Args:
        results: вход (после RRF + feedback boost).
        age_map: {chunk_id: age_in_days}.
        confirm_age_map: optional {chunk_id: validator_confirm_age_days}.

    Чанки без записи в age_map → multiplier=1.0 (no-op).
    """
    if not age_map and not confirm_age_map:
        return results
    confirm_age_map = confirm_age_map or {}
    for result in results:
        age = age_map.get(result.chunk_id)
        mult = 1.0
        if age is not None:
            mult *= compute_decay_multiplier(age)
        confirm_age = confirm_age_map.get(result.chunk_id)
        if confirm_age is not None:
            mult *= compute_recent_confirm_boost(confirm_age)
        if mult != 1.0:
            result.rrf_score *= mult
    return sorted(results, key=lambda r: -r.rrf_score)


def _fetch_chunk_ages(
    conn: sqlite3.Connection, chunk_ids: list[str], now: datetime | None = None
) -> dict[str, float]:
    """Возвращает {chunk_id: age_days} по chunks.end_ts.

    end_ts — конец временного окна chunk'а; berём его как proxy "возраста".
    Graceful: при ошибках / отсутствии строк возвращает {} для unknown ids.
    """
    if not chunk_ids:
        return {}
    now = now or datetime.now(timezone.utc)
    placeholders = ",".join("?" * len(chunk_ids))
    try:
        cur = conn.execute(
            f"SELECT chunk_id, end_ts FROM chunks WHERE chunk_id IN ({placeholders});",
            list(chunk_ids),
        )
        out: dict[str, float] = {}
        for chunk_id, end_ts in cur.fetchall():
            dt = _parse_iso_timestamp(end_ts)
            if dt is None:
                continue
            age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
            out[chunk_id] = age_days
        return out
    except sqlite3.OperationalError:
        return {}


def _fetch_validator_confirm_ages(
    conn: sqlite3.Connection, chunk_ids: list[str], now: datetime | None = None
) -> dict[str, float]:
    """{chunk_id: confirm_age_days} если column validator_confirmed_at есть.

    Column опционален (добавляется ALTER TABLE отдельно). При отсутствии —
    возвращает {} silently (graceful — recent-confirm boost просто не применится).
    """
    if not chunk_ids:
        return {}
    now = now or datetime.now(timezone.utc)
    placeholders = ",".join("?" * len(chunk_ids))
    try:
        cur = conn.execute(
            f"SELECT chunk_id, validator_confirmed_at FROM chunks "
            f"WHERE chunk_id IN ({placeholders}) AND validator_confirmed_at IS NOT NULL;",
            list(chunk_ids),
        )
        out: dict[str, float] = {}
        for chunk_id, confirmed_at in cur.fetchall():
            dt = _parse_iso_timestamp(confirmed_at)
            if dt is None:
                continue
            out[chunk_id] = max(0.0, (now - dt).total_seconds() / 86400.0)
        return out
    except sqlite3.OperationalError:
        # Column / table отсутствует — нормальная ситуация, тихо отключаем boost.
        return {}


def apply_response_feedback_boost(
    results: list["SearchResult"],
    feedback_map: dict[str, tuple[int, int]],
) -> list["SearchResult"]:
    """Применяет boost к rrf_score и пересортирует список.

    Args:
        results: вход RRF-results.
        feedback_map: {chunk_id: (positive_count, negative_count)}.

    Возвращает новый отсортированный список (in-place мутация полей rrf_score
    у переданных объектов — это OK, так как они одноразовые view-объекты).
    Чанки без feedback'а получают multiplier=1.0 (rrf_score не меняется).
    """
    if not feedback_map:
        return results
    for result in results:
        pos, neg = feedback_map.get(result.chunk_id, (0, 0))
        if pos == 0 and neg == 0:
            continue
        multiplier = compute_feedback_multiplier(pos, neg)
        result.rrf_score *= multiplier
    return sorted(results, key=lambda r: -r.rrf_score)


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

        # Feature A: Successful Response Retrieval Boost.
        # Дешёвый JOIN по top-N кандидатам (не все 50 — берём wider top чтобы
        # boost мог "вытащить" чанк из глубины). Default-safe: при отсутствии
        # таблицы fetch_response_feedback_for_chunks вернёт {}.
        if combined and _response_feedback_enabled():
            try:
                from .memory_archive import fetch_response_feedback_for_chunks

                # Берём шире чем limit, чтобы boost мог поднять чанк из глубины.
                wider_pool = combined[: max(limit * 3, 30)]
                fb_map = fetch_response_feedback_for_chunks(conn, [r.chunk_id for r in wider_pool])
                if fb_map:
                    combined = apply_response_feedback_boost(combined, fb_map)
            except Exception as exc:  # noqa: BLE001 - boost опционален
                logger.debug(
                    "hybrid_response_feedback_boost_skipped",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        # Feature D + Idea 15: Memory Decay + Recency Boost.
        # Idea 15 идёт ПЕРЕД decay: свежие (<7д) получают усиление, старые
        # отдаются под управление decay'у. age_map переиспользуем — один JOIN.
        if combined and (_decay_enabled() or _recency_boost_enabled()):
            try:
                wider_pool = combined[: max(limit * 3, 30)]
                ids = [r.chunk_id for r in wider_pool]
                age_map = _fetch_chunk_ages(conn, ids)
                confirm_map = _fetch_validator_confirm_ages(conn, ids)
                if age_map and _recency_boost_enabled():
                    combined = apply_recency_boost(combined, age_map)
                if (age_map or confirm_map) and _decay_enabled():
                    combined = apply_decay(combined, age_map, confirm_map)
            except Exception as exc:  # noqa: BLE001 - decay/recency опциональны
                logger.debug(
                    "hybrid_decay_skipped",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        top = combined[:limit]

        # Feature G: расширение топ-K соседями по topic-кластеру.
        # Берём top-3 chunks, запрашиваем дополнительные chunk_ids из тех же
        # кластеров (до 5 шт.), создаём для них SearchResult-стабы с пометкой
        # source='cluster_expand'. Управляется флагом окружения, default off.
        if top and os.environ.get("KRAB_TOPIC_CLUSTER_EXPAND_ENABLED", "0") == "1":
            try:
                from .memory_topic_clusters import topic_cluster_index

                seed_ids = [r.chunk_id for r in top[:3]]
                extras = topic_cluster_index.expand_with_cluster(seed_ids, max_per_cluster=2)[:5]
                if extras:
                    existing_ids = {r.chunk_id for r in top}
                    for cid in extras:
                        if cid in existing_ids:
                            continue
                        stub = SearchResult(chunk_id=cid, rrf_score=0.0, sources=["cluster_expand"])
                        top.append(stub)
                        existing_ids.add(cid)
                    logger.debug(
                        "hybrid_cluster_expand_applied",
                        seeds=len(seed_ids),
                        added=len(extras),
                    )
            except Exception as exc:  # noqa: BLE001 - expand опционален
                logger.debug(
                    "hybrid_cluster_expand_skipped",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

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
