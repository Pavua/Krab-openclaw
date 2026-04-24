"""
Hybrid retriever для Memory Layer (Track E).

Основной downstream API для Track B: `!archive`, `llm_flow.py` context injection,
`swarm_research_pipeline.py`. Импортируется из `src.core.memory_adapter` как
реальная реализация поверх stub.

Архитектура:
  - `HybridRetriever.__init__()` — ленивые поля: ни БД, ни модель не загружаются
    сразу. Это позволяет импортировать модуль даже без установленных
    `sqlite-vec` / `model2vec` (для dev-окружений без архива).
  - `search()` — три этапа:
      1. FTS5 BM25 на `messages_fts`
      2. Vector similarity через `sqlite-vec` на `vec_chunks` (если доступен)
      3. Reciprocal Rank Fusion с k=60 + adaptive decay
  - Graceful degradation: при отсутствии БД / модели / extension — возвращает
    пустой список и логирует warning (не падает в рантайме).
  - Threading: retriever — synchronous, но safe to use в asyncio через
    `asyncio.to_thread()` из вызывающего кода.

## API контракт (зафиксирован Session 8, Track B)

```python
class HybridRetriever:
    def search(
        self,
        query: str,
        chat_id: Optional[str] = None,
        top_k: int = 10,
        with_context: int = 2,
        decay_mode: str = "auto",
        owner_only: bool = True,
    ) -> list[SearchResult]: ...

@dataclass(frozen=True)
class SearchResult:
    message_id: str
    chat_id: str
    text_redacted: str
    timestamp: datetime
    score: float
    context_before: list[str]
    context_after: list[str]
```

## Текущий статус (Phase 2 skeleton)

В этом коммите:
  - полный скелет класса, работающий `search()` на FTS5-only (без векторного пути);
  - RRF-функция с нормализацией scores в 0..1;
  - adaptive decay с auto-detection исторических / recency маркеров;
  - graceful fallback: без sqlite-vec / model2vec → FTS5-only;
  - без sqlite-vec / FTS5 → возвращает [] + логирует.

До production нужно:
  - [Phase 2] подключить sqlite-vec.load(conn) и vec_chunks MATCH query;
  - [Phase 2] Model2Vec lazy-load с mmap-подобным поведением (auto-unload idle);
  - [Phase 4] incremental reindex воркер, invalidate на edit.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

from structlog import get_logger

from src.core.memory_adaptive_rerank import rerank_adaptive
from src.core.memory_archive import ArchivePaths, open_archive
from src.core.memory_mmr import mmr_is_enabled, mmr_rerank_texts
from src.core.memory_retrieval_scores import record_scores

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# API types.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchResult:
    """Результат hybrid retrieval, совместим с SearchResult в memory_adapter."""

    message_id: str
    chat_id: str
    text_redacted: str
    timestamp: datetime
    score: float
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Decay-функции.
# ---------------------------------------------------------------------------

HISTORICAL_MARKERS = (
    # Русские — прошлое.
    "раньше",
    "тогда",
    "в прошлом",
    "год назад",
    "два года",
    "в 2023",
    "в 2024",
    "когда-то",
    "давно",
    "ранее",
    # Английские.
    "before",
    "ago",
    "last year",
    "previously",
    "earlier",
    "in 2023",
    "in 2024",
    "back in",
    "long ago",
)
RECENT_MARKERS = (
    "сейчас",
    "today",
    "на этой неделе",
    "this week",
    "вчера",
    "yesterday",
)


def decay_none(_age_days: float) -> float:
    return 1.0


def decay_gentle(age_days: float) -> float:
    # Полураспад ~100 дней.
    return 1.0 / (1.0 + 0.01 * max(0.0, age_days))


def decay_aggressive(age_days: float) -> float:
    # Полураспад ~20 дней.
    return 1.0 / (1.0 + 0.05 * max(0.0, age_days))


DECAY_MODES = {
    "none": decay_none,
    "gentle": decay_gentle,
    "aggressive": decay_aggressive,
}


def detect_decay_mode(query: str) -> str:
    """Авто-выбор decay по query. Порядок: historical → recent → default."""
    q = query.lower()
    if any(m in q for m in HISTORICAL_MARKERS):
        return "none"
    if any(m in q for m in RECENT_MARKERS):
        return "aggressive"
    return "gentle"


# ---------------------------------------------------------------------------
# RRF fusion.
# ---------------------------------------------------------------------------


def reciprocal_rank_fusion(
    *ranked_lists: list[str],
    k: int = 60,
) -> dict[str, float]:
    """
    Классический Reciprocal Rank Fusion.

    Принимает N списков chunk_id (уже отсортированных от лучшего к худшему)
    и возвращает словарь {chunk_id: fused_score}. Не требует нормализации
    исходных scores — только ранги.
    """
    fused: dict[str, float] = {}
    for lst in ranked_lists:
        for rank, chunk_id in enumerate(lst, start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return fused


def normalize_scores_0_1(scores: dict[str, float]) -> dict[str, float]:
    """Min-max нормализация до диапазона [0, 1] для UI и logging."""
    if not scores:
        return {}
    values = list(scores.values())
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


# ---------------------------------------------------------------------------
# HybridRetriever.
# ---------------------------------------------------------------------------


class HybridRetriever:
    """
    Главный retrieval-класс Memory Layer.

    Args:
        archive_paths: пути к БД и директории. None → дефолт
            (`~/.openclaw/krab_memory/archive.db`).
        model_name: идентификатор Model2Vec модели. None → будет использоваться
            FTS5-only режим (векторный путь отключён).
        rrf_k: параметр RRF. Default 60 — стандарт из оригинальной статьи.
        now: фабрика "сейчас" для тестов. По умолчанию `datetime.now(UTC)`.
    """

    def __init__(
        self,
        archive_paths: ArchivePaths | None = None,
        model_name: str | None = "minishlab/M2V_multilingual_output",
        rrf_k: int = 60,
        now: Optional[callable] = None,  # type: ignore[type-arg]
    ) -> None:
        self._paths = archive_paths or ArchivePaths.default()
        self._model_name = model_name
        self._rrf_k = rrf_k
        self._now = now or (lambda: datetime.now(timezone.utc))

        # Lazy-init: ни БД, ни модель не трогаем в конструкторе.
        self._conn: sqlite3.Connection | None = None
        self._model: object | None = None  # Model2Vec.StaticModel, late import
        self._vec_available: bool = False

    # ------------------------------------------------------------------
    # Публичный API.
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        chat_id: Optional[str] = None,
        top_k: int = 10,
        with_context: int = 2,
        decay_mode: str = "auto",
        owner_only: bool = True,
    ) -> list[SearchResult]:
        """
        Гибридный поиск FTS5 + vector + RRF + decay.

        owner_only — зарезервирован для будущего ACL (когда Memory Layer
        будет обслуживать несколько пользователей/агентов). Пока всегда True
        на API-уровне и не влияет на запрос.
        """
        query = (query or "").strip()
        if not query:
            return []

        conn = self._ensure_connection()
        if conn is None:
            logger.debug("memory_retrieval_no_db", path=str(self._paths.db))
            return []

        # Query expansion (P2 carry-over, opt-in).
        # Короткие запросы (< N токенов) расширяются через Gemini Flash:
        # 3 перефразировки, RRF над union. Fallback на [query] при любой ошибке.
        queries = self._maybe_expand_query(query)

        # FTS5 — минимум, который мы обязаны выдать.
        if len(queries) <= 1:
            fts_ids = self._fts_search(
                conn, queries[0] if queries else query, chat_id, limit=top_k * 4
            )
        else:
            fts_lists: list[list[str]] = []
            for q in queries:
                ids = self._fts_search(conn, q, chat_id, limit=top_k * 4)
                if ids:
                    fts_lists.append(ids)
            if not fts_lists:
                return []
            # RRF между FTS-списками разных перефразировок.
            exp_fused = reciprocal_rank_fusion(*fts_lists, k=self._rrf_k)
            fts_ids = [cid for cid, _ in sorted(exp_fused.items(), key=lambda kv: -kv[1])]
            fts_ids = fts_ids[: top_k * 4]

        if not fts_ids:
            return []

        query_for_vector = queries[0] if queries else query

        # Vector — опционально, если доступен.
        vec_ids: list[str] = []
        if self._vec_available and self._model_name:
            try:
                vec_ids = self._vector_search(conn, query_for_vector, chat_id, limit=top_k * 4)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "memory_retrieval_vec_failed",
                    error=str(exc),
                    fallback="fts_only",
                )

        # Fusion.
        if vec_ids:
            fused = reciprocal_rank_fusion(fts_ids, vec_ids, k=self._rrf_k)
        else:
            fused = reciprocal_rank_fusion(fts_ids, k=self._rrf_k)

        # Записываем RRF scores для Prometheus percentiles.
        if fused:
            record_scores(list(fused.values()))

        # Decay.
        effective_mode = decay_mode
        if effective_mode == "auto":
            effective_mode = detect_decay_mode(query)
        decay_fn = DECAY_MODES.get(effective_mode, decay_gentle)

        # Собираем SearchResult'ы.
        results = self._materialize_results(
            conn,
            fused=fused,
            top_k=top_k,
            with_context=with_context,
            decay_fn=decay_fn,
        )

        # Opt-in: адаптивный реранкинг (Phase 3). Безопасный fallback — existing pipeline.
        if os.getenv("MEMORY_ADAPTIVE_RERANK_ENABLED", "0") == "1" and results:
            try:
                chunks = [
                    {
                        "id": r.message_id,
                        "score": r.score,
                        "text": r.text_redacted,
                        "metadata": {"timestamp": r.timestamp.timestamp()},
                    }
                    for r in results
                ]
                reranked = rerank_adaptive(chunks, query=query)
                # Восстанавливаем порядок SearchResult по новому скору.
                score_by_id = {c["id"]: c["score"] for c in reranked}
                results = sorted(
                    results, key=lambda r: score_by_id.get(r.message_id, 0.0), reverse=True
                )
                logger.debug(
                    "memory_adaptive_rerank_applied", query_len=len(query), count=len(results)
                )
                try:
                    from src.core.prometheus_metrics import _ADAPTIVE_RERANK_COUNTER

                    _ADAPTIVE_RERANK_COUNTER[0] += 1
                except Exception:
                    pass
            except Exception as exc:  # noqa: BLE001
                import traceback as _tb

                logger.warning(
                    "memory_adaptive_rerank_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    traceback=_tb.format_exc(),
                    fallback="hybrid_reranker",
                )

        # Opt-in: LLM re-ranking финальная стадия (Chado §6 P1).
        if os.getenv("KRAB_RAG_LLM_RERANK_ENABLED", "0") == "1" and results:
            try:
                from src.core.memory_llm_rerank import Candidate, llm_rerank  # lazy

                candidates = [
                    Candidate(
                        chunk_id=r.message_id,
                        text=r.text_redacted,
                        rrf_score=r.score,
                    )
                    for r in results
                ]
                # Подключаем Gemini-провайдер если доступен (Chado §6 P1).
                _rerank_provider = None
                try:
                    from src.core.gemini_rerank_provider import default_provider as _grp_default

                    _rerank_provider = _grp_default()
                except Exception:  # noqa: BLE001
                    pass
                reranked_cands = asyncio.get_event_loop().run_until_complete(
                    llm_rerank(query, candidates, top_k=top_k, provider=_rerank_provider)
                )
                # Восстанавливаем порядок SearchResult по chunk_id.
                order = {c.chunk_id: i for i, c in enumerate(reranked_cands)}
                results = sorted(results, key=lambda r: order.get(r.message_id, 9999))
                logger.debug("memory_llm_rerank_applied", count=len(results))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "memory_llm_rerank_hook_failed",
                    error=str(exc),
                    fallback="pre_rerank_order",
                )

        return results

    def _maybe_expand_query(self, query: str) -> list[str]:
        """
        Возвращает [original] или [original + LLM-перефразировки].

        Fallback на [query] при disabled-flag, ошибке или активном event loop
        (нельзя запускать sync run_until_complete внутри async-кода).
        """
        try:
            from src.core.memory_llm_query_expansion import expand_query_llm, is_enabled
        except Exception:  # noqa: BLE001
            return [query]
        if not is_enabled():
            return [query]
        try:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
            if loop.is_running():
                # В живом loop'е expansion не делаем — caller может вызвать async-версию.
                return [query]
            return loop.run_until_complete(expand_query_llm(query)) or [query]
        except Exception as exc:  # noqa: BLE001
            logger.debug("memory_query_expansion_invoke_failed", error=str(exc))
            return [query]

    def close(self) -> None:
        """Закрывает БД-подключение. Безопасно при повторном вызове."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    # ------------------------------------------------------------------
    # Lazy-init.
    # ------------------------------------------------------------------

    def _ensure_connection(self) -> sqlite3.Connection | None:
        """
        Открывает connection если ещё нет и БД существует.
        Пробует подгрузить sqlite-vec — без него работаем в FTS5-only.
        """
        if self._conn is not None:
            return self._conn
        if not self._paths.db.exists():
            return None
        try:
            conn = open_archive(self._paths, read_only=False, create_if_missing=False)
        except (sqlite3.Error, FileNotFoundError) as exc:
            logger.warning("memory_retrieval_open_failed", error=str(exc))
            return None

        # Попытка загрузить sqlite-vec (optional).
        try:
            import sqlite_vec  # type: ignore[import-not-found]

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            self._vec_available = True
            logger.debug("memory_retrieval_vec_loaded")
        except Exception as exc:  # noqa: BLE001 - extension optional
            self._vec_available = False
            logger.debug("memory_retrieval_vec_unavailable", error=str(exc))

        self._conn = conn
        return conn

    def _ensure_model(self) -> object | None:
        """Late-import Model2Vec. Возвращает model или None если недоступна."""
        if self._model is not None:
            return self._model
        if not self._model_name:
            return None
        try:
            from model2vec import StaticModel  # type: ignore[import-not-found]

            self._model = StaticModel.from_pretrained(self._model_name)
            logger.info("memory_retrieval_model_loaded", name=self._model_name)
            return self._model
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "memory_retrieval_model_load_failed",
                error=str(exc),
                fallback="fts_only",
            )
            self._model_name = None  # disable повторных попыток
            return None

    # ------------------------------------------------------------------
    # FTS5 путь.
    # ------------------------------------------------------------------

    def _fts_search(
        self,
        conn: sqlite3.Connection,
        query: str,
        chat_id: str | None,
        limit: int,
    ) -> list[str]:
        """Возвращает chunk_ids в порядке bm25-ранга (лучший первый)."""
        # Экранируем FTS5 MATCH — простейший способ, убираем служебные
        # символы. Для полной безопасности в Phase 2 — подключим tokenizer-aware
        # escape через bm25() подход.
        safe_query = _escape_fts5(query)
        if not safe_query:
            return []

        if chat_id is None:
            sql = """
                SELECT c.chunk_id
                FROM messages_fts AS f
                JOIN chunks AS c ON c.rowid = f.rowid
                WHERE f.text_redacted MATCH ?
                ORDER BY f.rank
                LIMIT ?;
            """
            params: tuple = (safe_query, limit)
        else:
            sql = """
                SELECT c.chunk_id
                FROM messages_fts AS f
                JOIN chunks AS c ON c.rowid = f.rowid
                WHERE f.text_redacted MATCH ?
                  AND c.chat_id = ?
                ORDER BY f.rank
                LIMIT ?;
            """
            params = (safe_query, chat_id, limit)

        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            logger.warning("memory_retrieval_fts_error", error=str(exc))
            return []
        return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # Vector путь (stub-готов, активируется в Phase 2).
    # ------------------------------------------------------------------

    def _vector_search(
        self,
        conn: sqlite3.Connection,
        query: str,
        chat_id: str | None,
        limit: int,
    ) -> list[str]:
        """
        Пока no-op (sqlite-vec загружен, но vec_chunks таблица не создана).
        После Phase 2 индексации — полноценный `vector MATCH ? ORDER BY distance`.
        """
        model = self._ensure_model()
        if model is None:
            return []
        # В Phase 2 реализация:
        #   q_vec = model.encode([query])[0]
        #   rows = conn.execute(
        #       "SELECT rowid FROM vec_chunks "
        #       "WHERE vector MATCH ? ORDER BY distance LIMIT ?",
        #       (serialize_f32(q_vec), limit),
        #   ).fetchall()
        #   → JOIN chunks ON rowid для получения chunk_id
        return []

    # ------------------------------------------------------------------
    # Сборка результатов.
    # ------------------------------------------------------------------

    def _materialize_results(
        self,
        conn: sqlite3.Connection,
        fused: dict[str, float],
        top_k: int,
        with_context: int,
        decay_fn,
    ) -> list[SearchResult]:
        """Из {chunk_id: raw_score} → `top_k` SearchResult с decay и context."""
        if not fused:
            return []

        # Сортируем и берём top 2x, чтобы после decay был запас на пересортировку.
        candidates = sorted(fused.items(), key=lambda kv: -kv[1])[: top_k * 2]
        candidate_ids = [c for c, _ in candidates]

        chunk_rows = self._fetch_chunks(conn, candidate_ids)
        if not chunk_rows:
            return []

        now = self._now()
        enriched: list[tuple[SearchResult, float]] = []
        for chunk_id, row in chunk_rows.items():
            raw_score = fused.get(chunk_id, 0.0)
            ts = _parse_iso(row["start_ts"])
            age_days = (now - ts).total_seconds() / 86400.0 if ts else 0.0
            decayed = raw_score * decay_fn(age_days)

            first_msg_id, ctx_before, ctx_after = self._fetch_context(conn, chunk_id, with_context)
            sr = SearchResult(
                message_id=first_msg_id or chunk_id,
                chat_id=row["chat_id"],
                text_redacted=row["text_redacted"],
                timestamp=ts or now,
                score=decayed,  # нормализуем ниже
                context_before=ctx_before,
                context_after=ctx_after,
            )
            enriched.append((sr, decayed))

        # Пересортировка по decayed, min-max нормализация в [0, 1].
        scored = {id(sr): score for sr, score in enriched}
        normed = normalize_scores_0_1(scored)

        final = [
            SearchResult(
                message_id=sr.message_id,
                chat_id=sr.chat_id,
                text_redacted=sr.text_redacted,
                timestamp=sr.timestamp,
                score=normed[id(sr)],
                context_before=sr.context_before,
                context_after=sr.context_after,
            )
            for sr, _ in enriched
        ]
        final.sort(key=lambda r: r.score, reverse=True)

        # MMR diversity re-ranking (P2 carry-over). Убирает near-duplicate chunks
        # из top-K. Backward-compat: при KRAB_RAG_MMR_ENABLED=0 — не применяется.
        if mmr_is_enabled() and len(final) > 1:
            try:
                doc_ids = [r.message_id for r in final]
                doc_texts = [r.text_redacted for r in final]
                rrf_scores_list = [r.score for r in final]
                ordered_ids = mmr_rerank_texts(
                    query="",
                    doc_ids=doc_ids,
                    doc_texts=doc_texts,
                    rrf_scores=rrf_scores_list,
                    top_k=top_k,
                )
                if ordered_ids:
                    by_id = {r.message_id: r for r in final}
                    final = [by_id[i] for i in ordered_ids if i in by_id]
            except Exception as exc:  # noqa: BLE001 - MMR не должен ломать retrieval.
                logger.warning(
                    "memory_mmr_rerank_failed",
                    error=str(exc),
                    fallback="rrf_order",
                )

        return final[:top_k]

    # ------------------------------------------------------------------
    # БД-помощники.
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_chunks(conn: sqlite3.Connection, chunk_ids: Iterable[str]) -> dict[str, sqlite3.Row]:
        """Достаёт chunks по списку chunk_id одним запросом."""
        ids = list(chunk_ids)
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT chunk_id, chat_id, start_ts, end_ts, text_redacted
            FROM chunks
            WHERE chunk_id IN ({placeholders});
            """,
            ids,
        ).fetchall()
        return {r["chunk_id"]: r for r in rows}

    @staticmethod
    def _fetch_context(
        conn: sqlite3.Connection, chunk_id: str, with_context: int
    ) -> tuple[str | None, list[str], list[str]]:
        """
        Возвращает (first_message_id, before_texts, after_texts) — до `with_context`
        соседних chunks в том же чате по временной оси.
        """
        conn.row_factory = sqlite3.Row

        # Найдём сам chunk.
        target = conn.execute(
            "SELECT chat_id, start_ts, rowid FROM chunks WHERE chunk_id = ?;",
            (chunk_id,),
        ).fetchone()
        if target is None:
            return None, [], []

        # Первое message_id в chunk'е — для отображения "якоря" в UI.
        first_row = conn.execute(
            """
            SELECT message_id FROM chunk_messages
            WHERE chunk_id = ?
            ORDER BY message_id
            LIMIT 1;
            """,
            (chunk_id,),
        ).fetchone()
        first_msg_id = first_row["message_id"] if first_row else None

        if with_context <= 0:
            return first_msg_id, [], []

        before = conn.execute(
            """
            SELECT text_redacted FROM chunks
            WHERE chat_id = ? AND start_ts < ?
            ORDER BY start_ts DESC
            LIMIT ?;
            """,
            (target["chat_id"], target["start_ts"], with_context),
        ).fetchall()
        after = conn.execute(
            """
            SELECT text_redacted FROM chunks
            WHERE chat_id = ? AND start_ts > ?
            ORDER BY start_ts ASC
            LIMIT ?;
            """,
            (target["chat_id"], target["start_ts"], with_context),
        ).fetchall()
        # before идёт от свежего к старому — разворачиваем для чтения сверху вниз.
        return (
            first_msg_id,
            [r["text_redacted"] for r in reversed(before)],
            [r["text_redacted"] for r in after],
        )


# ---------------------------------------------------------------------------
# Внутренние утилиты.
# ---------------------------------------------------------------------------


def _escape_fts5(query: str) -> str:
    """
    Простой escape для FTS5 MATCH: убираем символы, которые FTS5 интерпретирует
    как операторы (., -, ^, (, ), ", AND/OR в верхнем регистре).
    Для MVP достаточно — query_expansion в Phase 2 введёт tokenizer-aware.
    """
    # Убираем специальные FTS5 операторы, чтобы пользовательские запросы
    # не падали на syntax error.
    cleaned = "".join(ch if ch.isalnum() or ch in " -_" else " " for ch in query)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return ""
    # OR между словами (не AND): хотим, чтобы запрос из 5 слов возвращал
    # результаты, содержащие хотя бы одно из них. BM25 сам отранжирует.
    # Кавычки отключают интерпретацию самих токенов как FTS5 операторов.
    parts = [f'"{tok}"' for tok in cleaned.split() if tok]
    return " OR ".join(parts)


def _parse_iso(ts: str | None) -> datetime | None:
    """ISO-8601 → aware datetime. None-safe."""
    if not ts:
        return None
    try:
        # Поддерживаем "...Z" суффикс и tz-naive строки (считаем UTC).
        s = ts.rstrip("Z")
        parsed = datetime.fromisoformat(s)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None
