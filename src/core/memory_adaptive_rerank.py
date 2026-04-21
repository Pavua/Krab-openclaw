"""
Adaptive re-ranking для Memory Layer Phase 3.

Strategies (stackable):
1. MMR (Maximal Marginal Relevance) — diversity penalty
2. Temporal decay — recent chunks boosted
3. Source trust weights — confirmed/reviewed chunks preferred
4. Query-complexity adaptation — простые queries → FTS-first, complex → semantic-first

Usage:
    from src.core.memory_adaptive_rerank import rerank_adaptive
    reranked = rerank_adaptive(chunks, query="how to...", strategy="mmr+temporal")
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .logger import get_logger

logger = get_logger(__name__)


@dataclass
class ScoredChunk:
    # Структура одного кандидата с числовым скором и метаданными
    chunk_id: str
    score: float
    text: str
    metadata: dict[str, Any]


_MMR_CAP = 5  # Максимум чанков для MMR-перебора: O(n²) → O(cap²)


def _precompute_token_sets(chunks: list[ScoredChunk]) -> list[set[str]]:
    """Однократно токенизируем все чанки — O(n) вместо O(n²) split."""
    return [set(c.text.lower().split()) for c in chunks]


def _jaccard_sets(a: set[str], b: set[str]) -> float:
    """Jaccard по заранее вычисленным множествам токенов."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _token_overlap_sim(a: ScoredChunk, b: ScoredChunk) -> float:
    """Jaccard over tokens — дешёвая похожесть по пересечению слов."""
    toks_a = set(a.text.lower().split())
    toks_b = set(b.text.lower().split())
    if not toks_a or not toks_b:
        return 0.0
    return len(toks_a & toks_b) / len(toks_a | toks_b)


def apply_mmr(
    chunks: list[ScoredChunk],
    diversity: float = 0.3,
    similarity_fn: Optional[Callable[[ScoredChunk, ScoredChunk], float]] = None,
) -> list[ScoredChunk]:
    """Maximal Marginal Relevance — outcome = (1-λ)·relevance - λ·max_sim.

    Оптимизации (Wave 29-AB):
    - Cap: MMR работает только на первых _MMR_CAP чанках → O(cap²) вместо O(n²).
    - Pre-computed token sets: токенизация O(n) одноразово, similarity O(k).
    """
    if not chunks or diversity == 0:
        return list(chunks)

    # Разделяем: top cap для MMR + хвост без изменений
    mmr_input = chunks[:_MMR_CAP]
    tail = list(chunks[_MMR_CAP:])

    if similarity_fn is None:
        # Однократно вычисляем token-sets для cap-кандидатов
        token_sets = _precompute_token_sets(mmr_input)

        def _fast_sim(a: ScoredChunk, b: ScoredChunk) -> float:
            # Lookup по индексу в mmr_input — нет повторной токенизации
            idx_a = mmr_input.index(a) if a in mmr_input else -1
            idx_b = mmr_input.index(b) if b in mmr_input else -1
            sa = token_sets[idx_a] if idx_a >= 0 else set(a.text.lower().split())
            sb = token_sets[idx_b] if idx_b >= 0 else set(b.text.lower().split())
            return _jaccard_sets(sa, sb)

        similarity_fn_eff: Callable[[ScoredChunk, ScoredChunk], float] = _fast_sim
    else:
        similarity_fn_eff = similarity_fn

    # Первый элемент берём с максимальной релевантностью
    selected: list[ScoredChunk] = [mmr_input[0]]
    remaining = list(mmr_input[1:])

    while remaining:
        best_idx = 0
        best_mmr = float("-inf")
        for i, cand in enumerate(remaining):
            max_sim = max(similarity_fn_eff(cand, sel) for sel in selected)
            mmr = (1 - diversity) * cand.score - diversity * max_sim
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = i
        selected.append(remaining.pop(best_idx))

    logger.debug("mmr_applied", input_count=len(chunks), mmr_cap=_MMR_CAP, diversity=diversity)
    return selected + tail


def apply_temporal_decay(
    chunks: list[ScoredChunk],
    half_life_days: float = 30.0,
    now_ts: Optional[float] = None,
) -> list[ScoredChunk]:
    """Boost recent chunks. decay = 0.5 ** (age_days / half_life)."""
    if now_ts is None:
        now_ts = time.time()
    result: list[ScoredChunk] = []
    for c in chunks:
        ts = c.metadata.get("timestamp", now_ts)
        age_days = max(0.0, (now_ts - ts) / 86400.0)
        decay = 0.5 ** (age_days / half_life_days)
        new_score = c.score * decay
        result.append(ScoredChunk(c.chunk_id, new_score, c.text, dict(c.metadata)))
    return sorted(result, key=lambda x: x.score, reverse=True)


def apply_trust_weights(
    chunks: list[ScoredChunk],
    trust_map: Optional[dict[str, float]] = None,
) -> list[ScoredChunk]:
    """Multiply score by trust weight от source."""
    if trust_map is None:
        trust_map = {"confirmed": 1.3, "reviewed": 1.15, "default": 1.0}
    result: list[ScoredChunk] = []
    for c in chunks:
        src = c.metadata.get("source_trust", "default")
        weight = trust_map.get(src, 1.0)
        result.append(ScoredChunk(c.chunk_id, c.score * weight, c.text, dict(c.metadata)))
    return sorted(result, key=lambda x: x.score, reverse=True)


def rerank_adaptive(
    chunks: list[dict],
    query: str,
    strategy: str = "mmr+temporal",
    **kwargs: Any,
) -> list[dict]:
    """
    Main entry — применить стратегию reranking.

    strategy formats:
        "mmr" — только MMR
        "temporal" — только temporal decay
        "trust" — только trust weights
        "mmr+temporal" — pipeline
        "mmr+temporal+trust" — full
    """
    if not chunks:
        return []

    scored = [
        ScoredChunk(
            chunk_id=c.get("id", ""),
            score=float(c.get("score", 0.0)),
            text=c.get("text", ""),
            metadata=dict(c.get("metadata", {})),
        )
        for c in chunks
    ]

    stages = [s.strip() for s in strategy.split("+") if s.strip()]
    for stage in stages:
        if stage == "mmr":
            scored = apply_mmr(scored, diversity=kwargs.get("diversity", 0.3))
        elif stage == "temporal":
            scored = apply_temporal_decay(
                scored,
                half_life_days=kwargs.get("half_life_days", 30.0),
                now_ts=kwargs.get("now_ts"),
            )
        elif stage == "trust":
            scored = apply_trust_weights(scored, trust_map=kwargs.get("trust_map"))
        else:
            logger.warning("rerank_unknown_stage", stage=stage)

    logger.info(
        "rerank_adaptive_done",
        query_len=len(query),
        strategy=strategy,
        input_count=len(chunks),
        output_count=len(scored),
    )

    return [
        {"id": s.chunk_id, "score": s.score, "text": s.text, "metadata": dict(s.metadata)}
        for s in scored
    ]
