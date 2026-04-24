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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

from structlog import get_logger

from src.core.memory_adaptive_rerank import rerank_adaptive
from src.core.memory_archive import ArchivePaths, open_archive
from src.core.memory_mmr import mmr_is_enabled, mmr_rerank, mmr_rerank_texts
from src.core.memory_retrieval_scores import record_scores
from src.core.sentry_perf import set_tag as _sentry_tag
from src.core.sentry_perf import start_span as _sentry_span
from src.core.sentry_perf import start_transaction as _sentry_txn

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# C6: Prometheus инструментация (module-level хелперы).
# ---------------------------------------------------------------------------


def _inc_mode(mode: str) -> None:
    """Инкрементирует counter krab_memory_retrieval_mode_total{mode=...}.

    Silent no-op если prometheus_client недоступен или метрика упала на init.
    """
    try:
        from src.core.prometheus_metrics import _memory_retrieval_mode_total

        if _memory_retrieval_mode_total is not None:
            _memory_retrieval_mode_total.labels(mode=mode).inc()
    except Exception:  # noqa: BLE001 - инструментация best-effort
        pass


def _observe_phase(phase: str, seconds: float) -> None:
    """Observe латентность phase в histogram krab_memory_retrieval_latency_seconds.

    Silent no-op если prometheus_client недоступен.
    """
    try:
        from src.core.prometheus_metrics import _memory_retrieval_latency_seconds

        if _memory_retrieval_latency_seconds is not None:
            _memory_retrieval_latency_seconds.labels(phase=phase).observe(seconds)
    except Exception:  # noqa: BLE001 - инструментация best-effort
        pass


def _compute_mode(vec_hits: int, fts_hits: int) -> str:
    """vec>0 & fts>0 → hybrid; vec>0 only → vec; fts>0 only → fts; else none."""
    if vec_hits > 0 and fts_hits > 0:
        return "hybrid"
    if vec_hits > 0:
        return "vec"
    if fts_hits > 0:
        return "fts"
    return "none"


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
    weights: list[float] | None = None,
) -> dict[str, float]:
    """
    Классический Reciprocal Rank Fusion с опциональными per-source весами.

    Принимает N списков chunk_id (уже отсортированных от лучшего к худшему)
    и возвращает словарь {chunk_id: fused_score}. Не требует нормализации
    исходных scores — только ранги.

    weights: опциональный список весов по одному на ranked_list. Формула —
    `fused[chunk_id] += weight / (k + rank)`. None → все веса 1.0
    (backward-compat). Некорректная длина → игнорируем и считаем по 1.0.
    """
    if weights is not None and len(weights) != len(ranked_lists):
        # Silently fall back to equal weights — сохраняем legacy-контракт.
        weights = None
    fused: dict[str, float] = {}
    for idx, lst in enumerate(ranked_lists):
        w = weights[idx] if weights is not None else 1.0
        for rank, chunk_id in enumerate(lst, start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + w / (k + rank)
    return fused


def _rrf_vector_weight() -> float:
    """Env: KRAB_RAG_RRF_VECTOR_WEIGHT (default 1.0, clamp 0.0..5.0)."""
    try:
        w = float(os.getenv("KRAB_RAG_RRF_VECTOR_WEIGHT", "1.0"))
    except ValueError:
        return 1.0
    return max(0.0, min(5.0, w))


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
        model_dim: int | None = 256,
    ) -> None:
        self._paths = archive_paths or ArchivePaths.default()
        self._model_name = model_name
        self._model_dim = model_dim
        self._rrf_k = rrf_k
        self._now = now or (lambda: datetime.now(timezone.utc))

        # Lazy-init: ни БД, ни модель не трогаем в конструкторе.
        self._conn: sqlite3.Connection | None = None
        self._model: object | None = None  # Model2Vec.StaticModel, late import
        self._vec_available: bool = False
        # Последний query — нужен для cosine MMR в _materialize_results().
        self._last_query: str = ""

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

        # Sentry Performance Monitoring: wrap whole retrieval в transaction.
        # Graceful — если sentry_sdk не установлен, это no-op.
        with _sentry_txn(op="memory.retrieval", name="hybrid_search"):
            _sentry_tag("chat_id", str(chat_id) if chat_id else "none")
            _sentry_tag("decay_mode", decay_mode)
            return self._search_impl(
                query=query,
                chat_id=chat_id,
                top_k=top_k,
                with_context=with_context,
                decay_mode=decay_mode,
                owner_only=owner_only,
            )

    def _search_impl(
        self,
        query: str,
        chat_id: Optional[str],
        top_k: int,
        with_context: int,
        decay_mode: str,
        owner_only: bool,
    ) -> list[SearchResult]:
        """Фактическая логика retrieval — вынесена из `search()`, чтобы её
        можно было обернуть в sentry transaction без inflating indentation.
        """
        # C6: инструментация per-phase latency + mode counter.
        _total_start = time.perf_counter()

        # Сохраняем для cosine MMR в _materialize_results().
        self._last_query = query

        conn = self._ensure_connection()
        if conn is None:
            logger.debug("memory_retrieval_no_db", path=str(self._paths.db))
            _observe_phase("total", time.perf_counter() - _total_start)
            _inc_mode("none")
            return []

        # Query expansion (P2 carry-over, opt-in).
        # Короткие запросы (< N токенов) расширяются через Gemini Flash:
        # 3 перефразировки, RRF над union. Fallback на [query] при любой ошибке.
        queries = self._maybe_expand_query(query)

        # FTS5 — минимум, который мы обязаны выдать.
        _fts_start = time.perf_counter()
        with _sentry_span(op="memory.fts", description="bm25 search"):
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
                    _observe_phase("fts", time.perf_counter() - _fts_start)
                    _observe_phase("total", time.perf_counter() - _total_start)
                    _inc_mode("none")
                    return []
                # RRF между FTS-списками разных перефразировок.
                exp_fused = reciprocal_rank_fusion(*fts_lists, k=self._rrf_k)
                fts_ids = [cid for cid, _ in sorted(exp_fused.items(), key=lambda kv: -kv[1])]
                fts_ids = fts_ids[: top_k * 4]
        _observe_phase("fts", time.perf_counter() - _fts_start)

        if not fts_ids:
            _observe_phase("total", time.perf_counter() - _total_start)
            _inc_mode("none")
            return []

        query_for_vector = queries[0] if queries else query

        # Vector — опционально, если доступен.
        vec_ids: list[str] = []
        _vec_start = time.perf_counter()
        with _sentry_span(op="memory.vec", description="sqlite-vec KNN"):
            if self._vec_available and self._model_name:
                try:
                    vec_ids = self._vector_search(conn, query_for_vector, chat_id, limit=top_k * 4)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "memory_retrieval_vec_failed",
                        error=str(exc),
                        fallback="fts_only",
                    )
        _observe_phase("vec", time.perf_counter() - _vec_start)
        # mode tag — для фильтрации в Sentry UI (hybrid vs fts-only).
        _sentry_tag("mode", "hybrid" if vec_ids else "fts")

        # Fusion. FTS list всегда weight=1.0; vec list — env-настраиваемый вес
        # (C3 Memory Phase 2). Backward-compat: default weights → прежнее поведение.
        if vec_ids:
            fused = reciprocal_rank_fusion(
                fts_ids,
                vec_ids,
                k=self._rrf_k,
                weights=[1.0, _rrf_vector_weight()],
            )
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

        # C6: total latency + mode counter + structured summary log.
        total_elapsed = time.perf_counter() - _total_start
        _observe_phase("total", total_elapsed)
        mode = _compute_mode(len(vec_ids), len(fts_ids))
        _inc_mode(mode)
        logger.debug(
            "memory_retrieval_summary",
            query_len=len(query),
            vec_hits=len(vec_ids),
            fts_hits=len(fts_ids),
            merged_hits=len(fused),
            mmr_reranked=len(results),
            mode=mode,
            total_ms=round(total_elapsed * 1000, 1),
        )

        # Phase 2 shadow-reads: fire-and-forget сравнение FTS-only vs Hybrid.
        # Когда KRAB_RAG_PHASE2_ENABLED=0 (production), но KRAB_RAG_PHASE2_SHADOW=1
        # — запускаем vector search в background ТОЛЬКО для логирования. Результат
        # пользователю НЕ возвращается. Это безопасный способ собрать 24-48h данные
        # (recall delta, latency, anomalies) до реального toggle Phase 2.
        if (
            os.getenv("KRAB_RAG_PHASE2_SHADOW", "0") == "1"
            and os.getenv("KRAB_RAG_PHASE2_ENABLED", "0") != "1"
        ):
            try:
                top5_fts = [r.message_id for r in results[:5]]
                # fts_latency_ms уже посчитан выше как время первого phase.
                fts_latency_ms = round((_vec_start - _fts_start) * 1000, 1)
                self._schedule_shadow_compare(
                    conn=conn,
                    query=query,
                    chat_id=chat_id,
                    fts_ids=fts_ids,
                    top5_fts=top5_fts,
                    top_k=top_k,
                    with_context=with_context,
                    decay_mode=decay_mode,
                    fts_latency_ms=fts_latency_ms,
                )
            except Exception as exc:  # noqa: BLE001 - shadow best-effort
                logger.warning("memory_phase2_shadow_schedule_failed", error=str(exc))

        return results

    def _schedule_shadow_compare(
        self,
        conn: sqlite3.Connection,
        query: str,
        chat_id: Optional[str],
        fts_ids: list[str],
        top5_fts: list[str],
        top_k: int,
        with_context: int,
        decay_mode: str,
        fts_latency_ms: float,
    ) -> None:
        """Планирует shadow-сравнение через asyncio.create_task (fire-and-forget).

        Если event loop не запущен — выполняет sync inline (в тестах / sync callers).
        """
        coro = self._shadow_compare(
            conn=conn,
            query=query,
            chat_id=chat_id,
            fts_ids=fts_ids,
            top5_fts=top5_fts,
            top_k=top_k,
            with_context=with_context,
            decay_mode=decay_mode,
            fts_latency_ms=fts_latency_ms,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            loop.create_task(coro)
        else:
            # Нет активного loop'а — выполняем синхронно (тесты, CLI).
            try:
                asyncio.run(coro)
            except Exception as exc:  # noqa: BLE001
                logger.warning("memory_phase2_shadow_sync_run_failed", error=str(exc))

    async def _shadow_compare(
        self,
        conn: sqlite3.Connection,
        query: str,
        chat_id: Optional[str],
        fts_ids: list[str],
        top5_fts: list[str],
        top_k: int,
        with_context: int,
        decay_mode: str,
        fts_latency_ms: float,
    ) -> None:
        """Shadow-версия vector + RRF — только для логирования.

        Временно выставляет KRAB_RAG_PHASE2_ENABLED=1, вызывает _vector_search(),
        делает RRF с теми же весами и сравнивает top-5 с FTS-only результатом.
        НЕ трогает self._last_query / возвращаемые results.
        """
        try:
            if not self._vec_available or not self._model_name:
                return

            _vec_start = time.perf_counter()
            # _vector_search() сам проверяет KRAB_RAG_PHASE2_ENABLED — временно
            # включаем для shadow вызова.
            prev_flag = os.environ.get("KRAB_RAG_PHASE2_ENABLED", "0")
            os.environ["KRAB_RAG_PHASE2_ENABLED"] = "1"
            try:
                vec_ids_shadow = self._vector_search(conn, query, chat_id, limit=top_k * 4)
            finally:
                if prev_flag == "0":
                    os.environ["KRAB_RAG_PHASE2_ENABLED"] = prev_flag
            vec_latency_ms = round((time.perf_counter() - _vec_start) * 1000, 1)

            if vec_ids_shadow:
                fused_shadow = reciprocal_rank_fusion(
                    fts_ids,
                    vec_ids_shadow,
                    k=self._rrf_k,
                    weights=[1.0, _rrf_vector_weight()],
                )
            else:
                fused_shadow = reciprocal_rank_fusion(fts_ids, k=self._rrf_k)

            # Top-5 hybrid: сортируем fused по score и маппим chunk_id на message_id
            # (берём первое message_id из chunk_messages).
            top5_hybrid_chunk_ids = [
                cid for cid, _ in sorted(fused_shadow.items(), key=lambda kv: -kv[1])[:5]
            ]
            top5_hybrid: list[str] = []
            if top5_hybrid_chunk_ids:
                placeholders = ",".join("?" * len(top5_hybrid_chunk_ids))
                try:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        f"""
                        SELECT chunk_id, MIN(message_id) AS mid FROM chunk_messages
                        WHERE chunk_id IN ({placeholders})
                        GROUP BY chunk_id;
                        """,  # noqa: S608
                        top5_hybrid_chunk_ids,
                    ).fetchall()
                    mid_by_cid = {r["chunk_id"]: r["mid"] for r in rows}
                    top5_hybrid = [mid_by_cid.get(cid, cid) for cid in top5_hybrid_chunk_ids]
                except sqlite3.OperationalError:
                    top5_hybrid = list(top5_hybrid_chunk_ids)

            logger.info(
                "memory_phase2_shadow_compare",
                query_preview=query[:60],
                chat_id=str(chat_id) if chat_id else None,
                fts_hits=len(fts_ids),
                vec_hits=len(vec_ids_shadow),
                shadow_merged=len(fused_shadow),
                would_change_top5=top5_fts != top5_hybrid,
                latency_fts_ms=fts_latency_ms,
                latency_vec_ms=vec_latency_ms,
                model_mode="shadow",
            )
        except Exception as exc:  # noqa: BLE001 - shadow failure is non-fatal
            logger.warning(
                "memory_phase2_shadow_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

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

        # C7: embedding version guard. Если vec_chunks_meta существует и
        # model_name/model_dim не совпадают с текущими — вектора
        # невалидны (Model2Vec поменялся), падаем в FTS-only до rebuild_all().
        if self._vec_available:
            self._vec_available = self._check_vec_meta_compat(conn)

        self._conn = conn
        return conn

    def _check_vec_meta_compat(self, conn: sqlite3.Connection) -> bool:
        """
        C7: сверяет текущую embedding-модель с записью в vec_chunks_meta.

        Возвращает:
          * True — metadata отсутствует (свежая БД без embeddings) ИЛИ
            совпадает с текущими (model_name/model_dim).
          * False — mismatch (меняли модель, вектора невалидны) ИЛИ
            таблицы vec_chunks_meta нет вовсе (legacy-schema без C7 DDL —
            безопасно fallback'аться в FTS, пока embedder не запишет meta).
        """
        try:
            meta_rows = conn.execute(
                "SELECT key, value FROM vec_chunks_meta WHERE key IN ('model_name','model_dim');"
            ).fetchall()
        except sqlite3.OperationalError:
            # Таблицы ещё нет (legacy-БД: open_archive() не вызывает
            # create_schema() для существующих БД, поэтому _DDL_VEC_CHUNKS_META
            # до первого embedder-прогона не применяется). Создаём идемпотентно
            # и обрабатываем как "пустую meta": первый embed_all_unindexed()
            # заполнит её. Это разблокирует vector path для БД, где векторы
            # уже пре-индексированы bootstrap-скриптом до C7.
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS vec_chunks_meta (
                        key   TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    ) WITHOUT ROWID;
                    """
                )
                conn.commit()
                logger.debug("memory_vec_meta_created", action="legacy_db_upgrade")
                return True
            except sqlite3.Error as exc:
                logger.debug(
                    "memory_vec_meta_create_failed",
                    error=str(exc),
                    action="fallback_to_fts_only",
                )
                return False

        if not meta_rows:
            # Таблица есть, но ещё пуста — embedder не прогонялся.
            # Вектора если и есть, не сверяемы — оставляем vec path включённым;
            # первое успешное embed_all_unindexed() заполнит meta.
            return True

        meta = {str(k): str(v) for k, v in meta_rows}
        stored_name = meta.get("model_name")
        stored_dim_raw = meta.get("model_dim")
        try:
            stored_dim = int(stored_dim_raw) if stored_dim_raw is not None else 0
        except (TypeError, ValueError):
            stored_dim = 0

        if stored_name and self._model_name and stored_name != self._model_name:
            logger.warning(
                "memory_vec_model_mismatch",
                stored=stored_name,
                current=self._model_name,
                action="fallback_to_fts_only",
            )
            return False
        if stored_dim and self._model_dim and stored_dim != self._model_dim:
            logger.warning(
                "memory_vec_dim_mismatch",
                stored=stored_dim,
                current=self._model_dim,
                action="fallback_to_fts_only",
            )
            return False
        return True

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
        Vector KNN через sqlite-vec: `vec_chunks.vector MATCH ? AND k = ?`.

        Поведение:
          * Feature-flag `KRAB_RAG_PHASE2_ENABLED` (env, default "0"). При
            `!= "1"` возвращает []. Проверка per-call — toggle без restart'а.
          * Lazy-load Model2Vec через `_ensure_model()`. Если модель
            недоступна — []
          * Per-chat фильтр реализован через subquery: KNN MATCH не
            поддерживает дополнительный WHERE напрямую, поэтому берём
            `limit * 3` соседей по вектору и фильтруем по `chat_id`
            во внешнем SELECT.
          * Любой `sqlite3.OperationalError` → warning + []. Retriever
            продолжает работу в FTS-only режиме.
          * C7 guard: `self._vec_available` выставляется в `_ensure_connection()`
            по результату `_check_vec_meta_compat()` (сверка model_name/dim с
            vec_chunks_meta). `search()` gate'ит вызов `_vector_search()` по
            этому флагу — при embedding-mismatch путь автоматически обходится.
        """
        if os.getenv("KRAB_RAG_PHASE2_ENABLED", "0") != "1":
            return []

        model = self._ensure_model()
        if model is None:
            return []

        # Late-import serialize_f32 — тот же путь, что в memory_embedder'е.
        try:
            from src.core.memory_embedder import serialize_f32
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory_vec_search_import_failed", error=str(exc))
            return []

        try:
            q_vec = model.encode([query])[0]  # type: ignore[attr-defined]
            q_blob = serialize_f32(q_vec)
        except Exception as exc:  # noqa: BLE001 - encode best-effort
            logger.warning("memory_vec_search_encode_failed", error=str(exc))
            return []

        try:
            if chat_id is not None:
                # KNN-поиск по вектору с запасом, затем фильтр по chat_id.
                # `limit * 3` — типичный recall-boost для per-chat режима.
                sql = """
                    SELECT c.chunk_id
                    FROM chunks AS c
                    WHERE c.id IN (
                        SELECT rowid FROM vec_chunks
                        WHERE vector MATCH ? AND k = ?
                    )
                      AND c.chat_id = ?
                    ORDER BY c.id;
                """
                rows = conn.execute(sql, (q_blob, limit * 3, chat_id)).fetchall()
                return [r[0] for r in rows][:limit]
            sql = """
                SELECT c.chunk_id, v.distance
                FROM vec_chunks AS v
                JOIN chunks AS c ON c.id = v.rowid
                WHERE v.vector MATCH ? AND k = ?
                ORDER BY v.distance;
            """
            rows = conn.execute(sql, (q_blob, limit)).fetchall()
            return [r[0] for r in rows]
        except sqlite3.OperationalError as exc:
            logger.warning("memory_vec_search_failed", error=str(exc))
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
        # C4: сохраняем chunk_id рядом с SearchResult — нужен для lookup в vec_chunks.
        chunk_id_by_sr: dict[int, str] = {}
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
            chunk_id_by_sr[id(sr)] = chunk_id
            enriched.append((sr, decayed))

        # Пересортировка по decayed, min-max нормализация в [0, 1].
        scored = {id(sr): score for sr, score in enriched}
        normed = normalize_scores_0_1(scored)

        final: list[SearchResult] = []
        chunk_id_by_msg_id: dict[str, str] = {}
        for sr, _ in enriched:
            new_sr = SearchResult(
                message_id=sr.message_id,
                chat_id=sr.chat_id,
                text_redacted=sr.text_redacted,
                timestamp=sr.timestamp,
                score=normed[id(sr)],
                context_before=sr.context_before,
                context_after=sr.context_after,
            )
            cid = chunk_id_by_sr.get(id(sr))
            if cid is not None:
                chunk_id_by_msg_id[new_sr.message_id] = cid
            final.append(new_sr)
        final.sort(key=lambda r: r.score, reverse=True)

        # MMR diversity re-ranking (P2 carry-over). Убирает near-duplicate chunks
        # из top-K. Backward-compat: при KRAB_RAG_MMR_ENABLED=0 — не применяется.
        # C6: per-phase latency histogram (phase=mmr) вокруг всего блока.
        # Sentry span: op="memory.mmr" для UI performance фильтров.
        _mmr_start = time.perf_counter()
        _mmr_span_cm = _sentry_span(op="memory.mmr", description="MMR rerank")
        _mmr_span_cm.__enter__()
        if mmr_is_enabled() and len(final) > 1:
            try:
                doc_ids = [r.message_id for r in final]
                doc_texts = [r.text_redacted for r in final]
                rrf_scores_list = [r.score for r in final]
                # chunk_id для каждого doc (для lookup в vec_chunks).
                chunk_ids_for_mmr: list[str | None] = [
                    chunk_id_by_msg_id.get(mid) for mid in doc_ids
                ]

                # C4: пре-вычисленные эмбеддинги из vec_chunks вместо on-the-fly encode.
                # Ожидаемое ускорение MMR 50-100ms → 5-10ms (10× speedup).
                ordered_ids: list[str] = []
                model = self._ensure_model()
                d_vecs: list[list[float] | None] = [None] * len(doc_ids)
                if (
                    model is not None
                    and self._last_query
                    and self._vec_available
                    and any(c is not None for c in chunk_ids_for_mmr)
                ):
                    try:
                        # struct-unpack — обратная операция к serialize_f32.
                        import struct as _struct

                        def _deser(blob: bytes) -> list[float]:
                            return list(_struct.unpack(f"<{len(blob) // 4}f", blob))

                        valid_ids = [c for c in chunk_ids_for_mmr if c is not None]
                        if valid_ids:
                            placeholders = ",".join("?" * len(valid_ids))
                            rows = conn.execute(
                                f"SELECT c.chunk_id, v.vector "  # noqa: S608
                                f"FROM vec_chunks v JOIN chunks c ON c.id = v.rowid "
                                f"WHERE c.chunk_id IN ({placeholders});",
                                valid_ids,
                            ).fetchall()
                            vec_by_chunk: dict[str, list[float]] = {}
                            for cid, vec_blob in rows:
                                if vec_blob:
                                    vec_by_chunk[cid] = _deser(bytes(vec_blob))
                            for i, cid in enumerate(chunk_ids_for_mmr):
                                if cid is not None and cid in vec_by_chunk:
                                    d_vecs[i] = vec_by_chunk[cid]
                    except Exception as exc:  # noqa: BLE001 - cache read best-effort
                        logger.warning(
                            "memory_mmr_vec_cache_failed",
                            error=str(exc),
                            error_type=type(exc).__name__,
                        )

                cache_hit_rate = (
                    sum(1 for v in d_vecs if v is not None) / len(d_vecs) if d_vecs else 0.0
                )

                if model is not None and self._last_query and cache_hit_rate >= 0.5:
                    try:
                        q_vec = model.encode([self._last_query])[0].tolist()  # type: ignore[attr-defined]
                        # Для missing векторов — encode on-the-fly только нужные (edge case).
                        missing_idx = [i for i, v in enumerate(d_vecs) if v is None]
                        if missing_idx:
                            missing_texts = [doc_texts[i] for i in missing_idx]
                            missing_encoded = model.encode(missing_texts)  # type: ignore[attr-defined]
                            for idx, enc in zip(missing_idx, missing_encoded):
                                d_vecs[idx] = enc.tolist()
                        ordered_ids = mmr_rerank(
                            q_vec,
                            d_vecs,  # type: ignore[arg-type]
                            doc_ids,
                            rrf_scores=rrf_scores_list,
                            top_k=top_k,
                        )
                        logger.debug(
                            "memory_mmr_mode",
                            mode="cosine_cached",
                            cache_hit=round(cache_hit_rate, 2),
                            docs=len(doc_texts),
                        )
                    except MemoryError:
                        raise
                    except (AttributeError, ImportError, ModuleNotFoundError) as exc:
                        logger.warning(
                            "memory_mmr_cosine_model_unavailable",
                            error=str(exc),
                            error_type=type(exc).__name__,
                            fallback="jaccard",
                        )
                        ordered_ids = []
                    except ValueError as exc:
                        logger.warning(
                            "memory_mmr_cosine_shape_error",
                            error=str(exc),
                            error_type=type(exc).__name__,
                            fallback="jaccard",
                        )
                        ordered_ids = []
                    except Exception as exc:  # noqa: BLE001 - cosine best-effort
                        logger.warning(
                            "memory_mmr_cosine_failed",
                            error=str(exc),
                            error_type=type(exc).__name__,
                            fallback="jaccard",
                        )
                        ordered_ids = []
                elif model is not None and self._last_query:
                    # cache_hit < 0.5 — fallback на on-the-fly encode (старый путь).
                    try:
                        q_vec = model.encode([self._last_query])[0].tolist()  # type: ignore[attr-defined]
                        d_vecs_full = [v.tolist() for v in model.encode(doc_texts)]  # type: ignore[attr-defined]
                        ordered_ids = mmr_rerank(
                            q_vec,
                            d_vecs_full,
                            doc_ids,
                            rrf_scores=rrf_scores_list,
                            top_k=top_k,
                        )
                        logger.debug(
                            "memory_mmr_mode",
                            mode="cosine_encode",
                            cache_hit=round(cache_hit_rate, 2),
                            docs=len(doc_texts),
                        )
                    except MemoryError:
                        raise
                    except (AttributeError, ImportError, ModuleNotFoundError) as exc:
                        logger.warning(
                            "memory_mmr_cosine_model_unavailable",
                            error=str(exc),
                            error_type=type(exc).__name__,
                            fallback="jaccard",
                        )
                        ordered_ids = []
                    except ValueError as exc:
                        logger.warning(
                            "memory_mmr_cosine_shape_error",
                            error=str(exc),
                            error_type=type(exc).__name__,
                            fallback="jaccard",
                        )
                        ordered_ids = []
                    except Exception as exc:  # noqa: BLE001 - cosine best-effort
                        logger.warning(
                            "memory_mmr_cosine_failed",
                            error=str(exc),
                            error_type=type(exc).__name__,
                            fallback="jaccard",
                        )
                        ordered_ids = []

                if not ordered_ids:
                    ordered_ids = mmr_rerank_texts(
                        query=self._last_query or "",
                        doc_ids=doc_ids,
                        doc_texts=doc_texts,
                        rrf_scores=rrf_scores_list,
                        top_k=top_k,
                    )
                    logger.debug(
                        "memory_mmr_mode",
                        mode="jaccard_fallback",
                        reason="no_model_or_empty_query",
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
        _observe_phase("mmr", time.perf_counter() - _mmr_start)
        try:
            _mmr_span_cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass

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
