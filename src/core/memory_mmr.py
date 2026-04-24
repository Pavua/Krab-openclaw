"""
MMR (Maximal Marginal Relevance) diversity re-ranking для hybrid retrieval.

Задача — после RRF fusion, до top-K cutoff, убрать near-duplicate chunks
(семантически почти одинаковые фрагменты из одной беседы), повысив coverage
разных тем в итоговой выдаче.

Формула:
    MMR(d) = λ * sim(q, d) − (1 − λ) * max_{d' ∈ S} sim(d, d')
где:
    - q — embedding запроса,
    - d — кандидат,
    - S — уже выбранные документы,
    - sim — cosine similarity,
    - λ (lambda_) — баланс relevance/diversity (0.7 default).

Config:
    KRAB_RAG_MMR_ENABLED=1         — включено по умолчанию.
    KRAB_RAG_MMR_LAMBDA=0.7        — relevance weight (1.0 = только relevance).

Public API:
    mmr_rerank(query_vec, doc_vecs, doc_ids, rrf_scores, top_k, lambda_) -> list[str]
    mmr_is_enabled() -> bool
    mmr_lambda() -> float
"""

from __future__ import annotations

import math
import os
from typing import Sequence

from structlog import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Конфигурация через env.
# ---------------------------------------------------------------------------


def mmr_is_enabled() -> bool:
    """MMR включён? Default on."""
    return os.getenv("KRAB_RAG_MMR_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")


def mmr_lambda(default: float = 0.7) -> float:
    """λ (relevance weight). Clamp в [0.0, 1.0]. Default 0.7."""
    raw = os.getenv("KRAB_RAG_MMR_LAMBDA")
    if raw is None:
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    if val < 0.0:
        return 0.0
    if val > 1.0:
        return 1.0
    return val


# ---------------------------------------------------------------------------
# Векторная математика.
# ---------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    """Набор уникальных токенов (lowercased) — для Jaccard-fallback."""
    if not text:
        return set()
    return {t for t in text.lower().split() if t}


def _jaccard(a: str, b: str) -> float:
    """Jaccard similarity по токенам — используется как fallback без embeddings."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity двух векторов. При нулевой норме → 0.0."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ---------------------------------------------------------------------------
# MMR алгоритм.
# ---------------------------------------------------------------------------


def mmr_rerank(
    query_vec: Sequence[float] | None,
    doc_vecs: Sequence[Sequence[float] | None],
    doc_ids: Sequence[str],
    rrf_scores: Sequence[float] | None = None,
    top_k: int = 10,
    lambda_: float | None = None,
) -> list[str]:
    """
    Возвращает doc_ids отранжированные по MMR, максимум top_k.

    Args:
        query_vec: embedding запроса. Если None — relevance-часть берётся
            из rrf_scores (fallback без query embedding).
        doc_vecs: embedding'и документов (same order as doc_ids). None → считаем
            "недоступным" и кандидат получает sim=0 к остальным (максимальное
            diversity), а relevance — fallback из rrf_scores.
        doc_ids: стабильные идентификаторы (chunk_id).
        rrf_scores: опциональный fallback для relevance если query_vec=None.
        top_k: сколько вернуть.
        lambda_: λ ∈ [0, 1]. None → mmr_lambda() из env.

    Backward-compat: при len(doc_ids) <= 1 — возвращает input как есть.
    """
    n = len(doc_ids)
    if n == 0:
        return []
    if top_k <= 0:
        return []
    if n == 1:
        return [doc_ids[0]]

    lam = mmr_lambda() if lambda_ is None else max(0.0, min(1.0, lambda_))

    # Предвычисляем relevance для каждого документа.
    # Приоритет: cosine(q, d) → rrf_scores → 0.0.
    rel: list[float] = []
    have_query = query_vec is not None and len(query_vec) > 0
    for i in range(n):
        if have_query and doc_vecs[i] is not None:
            rel.append(_cosine(query_vec, doc_vecs[i]))  # type: ignore[arg-type]
        elif rrf_scores is not None and i < len(rrf_scores):
            rel.append(float(rrf_scores[i]))
        else:
            rel.append(0.0)

    selected: list[int] = []
    remaining: set[int] = set(range(n))

    # Первый выбор — чистый argmax relevance.
    first = max(remaining, key=lambda i: rel[i])
    selected.append(first)
    remaining.remove(first)

    while remaining and len(selected) < top_k:
        best_idx: int | None = None
        best_score = -math.inf
        for i in remaining:
            # Max similarity к уже выбранным.
            max_sim = 0.0
            if doc_vecs[i] is not None:
                for j in selected:
                    if doc_vecs[j] is None:
                        continue
                    sim = _cosine(doc_vecs[i], doc_vecs[j])  # type: ignore[arg-type]
                    if sim > max_sim:
                        max_sim = sim
            mmr_score = lam * rel[i] - (1.0 - lam) * max_sim
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i
        if best_idx is None:
            break
        selected.append(best_idx)
        remaining.remove(best_idx)

    return [doc_ids[i] for i in selected]


def mmr_rerank_texts(
    query: str,
    doc_ids: Sequence[str],
    doc_texts: Sequence[str],
    rrf_scores: Sequence[float],
    top_k: int = 10,
    lambda_: float | None = None,
) -> list[str]:
    """
    Fallback-вариант MMR без embeddings: использует Jaccard-similarity
    по токенам как прокси для cosine. Релевантность — из rrf_scores.

    Применяется в hybrid retrieval, когда Model2Vec недоступен: всё равно
    убирает грубые near-duplicate (те, что делят большинство токенов).
    """
    n = len(doc_ids)
    if n == 0 or top_k <= 0:
        return []
    if n == 1:
        return [doc_ids[0]]

    lam = mmr_lambda() if lambda_ is None else max(0.0, min(1.0, lambda_))

    # Relevance — RRF score как нормализованная оценка (caller уже нормализовал).
    rel: list[float] = []
    for i in range(n):
        if i < len(rrf_scores):
            rel.append(float(rrf_scores[i]))
        else:
            rel.append(0.0)

    selected: list[int] = []
    remaining: set[int] = set(range(n))

    first = max(remaining, key=lambda i: rel[i])
    selected.append(first)
    remaining.remove(first)

    while remaining and len(selected) < top_k:
        best_idx: int | None = None
        best_score = -math.inf
        for i in remaining:
            max_sim = 0.0
            for j in selected:
                sim = _jaccard(doc_texts[i], doc_texts[j])
                if sim > max_sim:
                    max_sim = sim
            mmr_score = lam * rel[i] - (1.0 - lam) * max_sim
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i
        if best_idx is None:
            break
        selected.append(best_idx)
        remaining.remove(best_idx)

    return [doc_ids[i] for i in selected]
