"""
Query expansion для Memory Layer retrieval.

Strategies (все optional, stackable):
1. Russian/English term normalization — убрать ё→е, transliterate
2. Synonyms dictionary — small hand-curated mapping
3. Stem-based expansion — simple suffix stripping
4. Multi-query — 2-3 variations run, dedup results

Usage:
    queries = expand_query("как установить Krab?")
    # → ["как установить Krab?", "установка Krab", "how to install Krab"]
"""

from __future__ import annotations

import os
import re

from .logger import get_logger

logger = get_logger(__name__)

QUERY_EXPANSION_ENABLED = os.environ.get("QUERY_EXPANSION_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)

# Hand-curated synonym clusters (ru+en)
_SYNONYMS: dict[str, list[str]] = {
    "установить": ["установка", "поставить", "install", "setup"],
    "install": ["setup", "установить", "установка"],
    "удалить": ["удаление", "delete", "remove"],
    "delete": ["удалить", "remove"],
    "remove": ["удалить", "delete"],
    "настроить": ["настройка", "configure", "config"],
    "configure": ["настройка", "настроить", "config"],
    "ошибка": ["error", "fail", "баг", "bug"],
    "error": ["ошибка", "fail", "bug"],
    "fail": ["ошибка", "error", "bug"],
    "запустить": ["start", "run", "launch", "запуск"],
    "start": ["запустить", "run", "launch"],
    "run": ["запустить", "start"],
    "проверить": ["check", "verify", "test"],
    "check": ["проверить", "verify"],
    "память": ["memory", "memo", "архив"],
    "memory": ["память", "архив"],
    "архив": ["archive", "память", "memory"],
    "чат": ["chat", "conversation"],
    "chat": ["чат", "conversation"],
    "команда": ["command", "cmd"],
    "command": ["команда", "cmd"],
}


def normalize(text: str) -> str:
    """Basic normalization — lowercase, ё→е."""
    return text.lower().replace("ё", "е").strip()


def stem_simple(word: str) -> str:
    """Primitive stem — remove common RU endings."""
    ru_endings = (
        "ция",
        "ние",
        "ать",
        "ить",
        "оть",
        "уть",
        "ов",
        "ев",
        "ой",
        "ая",
        "ое",
        "ые",
        "ый",
    )
    for ending in sorted(ru_endings, key=len, reverse=True):
        if word.endswith(ending) and len(word) > len(ending) + 2:
            return word[: -len(ending)]
    return word


def expand_query(query: str, max_variants: int = 3) -> list[str]:
    """
    Expand query into list of semantically similar queries.
    Returns [original, ...variants] with max_variants total.
    """
    if not QUERY_EXPANSION_ENABLED or not query.strip():
        return [query]

    variants: list[str] = [query]
    norm = normalize(query)
    tokens = re.findall(r"\w+", norm)

    # Strategy 1: Replace each term with synonym if known
    for i, token in enumerate(tokens):
        if len(variants) >= max_variants:
            break
        syns = _SYNONYMS.get(token, [])
        for syn in syns[:2]:  # Limit per term
            if len(variants) >= max_variants:
                break
            new_tokens = tokens.copy()
            new_tokens[i] = syn
            variant = " ".join(new_tokens)
            if variant not in [normalize(v) for v in variants]:
                variants.append(variant)

    # Strategy 2: Stem-based reduction for remaining slot
    if len(variants) < max_variants:
        stemmed = [stem_simple(t) for t in tokens]
        stem_query = " ".join(stemmed)
        if stem_query != norm and stem_query not in [normalize(v) for v in variants]:
            variants.append(stem_query)

    return variants[:max_variants]


def merge_results(results_lists: list[list]) -> list:
    """Merge search results from multiple queries, dedup by chunk_id."""
    seen: dict[str, object] = {}
    for results in results_lists:
        for r in results:
            cid = getattr(r, "chunk_id", None) if not isinstance(r, dict) else r.get("chunk_id")
            if not cid:
                continue
            if cid not in seen:
                seen[cid] = r
            else:
                # Boost score для duplicates (appears в multiple expansions)
                existing = seen[cid]
                if hasattr(existing, "rrf_score"):
                    existing.rrf_score = existing.rrf_score * 1.2  # 20% boost
                elif isinstance(existing, dict) and "rrf_score" in existing:
                    existing["rrf_score"] = existing["rrf_score"] * 1.2

    results = list(seen.values())
    results.sort(
        key=lambda r: (
            -(getattr(r, "rrf_score", 0) if not isinstance(r, dict) else r.get("rrf_score", 0))
        )
    )
    return results


def hybrid_search_expanded(query: str, limit: int = 5, max_expansions: int = 3) -> list:
    """Drop-in replacement для hybrid_search with expansion."""
    try:
        from .memory_hybrid_reranker import hybrid_search
    except ImportError:
        logger.warning("hybrid_search_not_available")
        return []

    queries = expand_query(query, max_variants=max_expansions)
    if len(queries) == 1:
        return hybrid_search(query, limit=limit)

    all_results: list[list] = []
    for q in queries:
        try:
            all_results.append(hybrid_search(q, limit=limit))
        except Exception as e:
            logger.debug("expansion_query_failed", query=q, error=str(e))

    merged = merge_results(all_results)
    logger.info("query_expansion", original=query, variants=len(queries), total=len(merged))
    return merged[:limit]
