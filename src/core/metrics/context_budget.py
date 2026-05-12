# -*- coding: utf-8 -*-
"""
Wave 128: Prometheus метрики для LLM context window budget.

Каждая модель имеет свой context window (Gemini 3 Pro — 1M токенов,
Claude Sonnet 4.5 — 200K, GPT-5.5 — 128K). Krab трекает prompt_tokens
per-request но раньше не сопоставлял их с лимитом модели — нет видимости
"приближаемся ли мы к пределу контекста".

Gauge `krab_llm_context_usage_pct{model}` = prompt_tokens последнего
запроса / context_window модели. Обновляется из openclaw_client
параллельно с cost analytics (`record_usage`).

Если `prometheus_client` отсутствует — Gauge становится no-op (как и
другие модули metrics/).
"""

from __future__ import annotations

from typing import Any

# Таблица context window (в токенах) по model_id.
# Покрывает primary/translator routing + LM Studio локальные модели.
# Источник лимитов:
#   - Gemini 3 Pro / Flash preview: 1M токенов (https://ai.google.dev/gemini-api/docs/models)
#   - Gemini 2.5 Pro / Flash: 1M / 1M (https://ai.google.dev/gemini-api/docs/models/gemini)
#   - Claude Sonnet 4.5: 200K (https://docs.anthropic.com/en/docs/about-claude/models)
#   - Claude Opus 4.x: 200K (1M в beta)
#   - GPT-5 family: 128K context (OpenAI docs)
#   - LM Studio локальные модели: типичный SFT ≈ 32K/128K, ставим консервативно
MODEL_CONTEXT_WINDOW: dict[str, int] = {
    # Google Gemini
    "google/gemini-3-pro-preview": 1_048_576,
    "google/gemini-3-flash-preview": 1_048_576,
    "google/gemini-3.1-pro-preview": 1_048_576,
    "google/gemini-2.5-pro-preview": 1_048_576,
    "google/gemini-2.5-flash": 1_048_576,
    "google/gemini-2.5-pro": 1_048_576,
    # Anthropic Claude
    "anthropic/claude-sonnet-4-5": 200_000,
    "anthropic/claude-opus-4-5": 200_000,
    "anthropic/claude-opus-4-7": 200_000,
    "anthropic/claude-3-5-sonnet": 200_000,
    # OpenAI GPT
    "openai/gpt-5": 128_000,
    "openai/gpt-5.4": 128_000,
    "openai/gpt-5.5": 128_000,
    # LM Studio локальные (консервативная оценка)
    "lm-studio/gemma-4-e4b-it-mlx": 32_768,
    "lm-studio/qwen2.5-coder": 32_768,
}


try:
    from prometheus_client import Gauge  # type: ignore[import-not-found]

    _HAS_PROM = True
except Exception:  # pragma: no cover — slim env

    class _Noop:
        def labels(self, *_a: Any, **_kw: Any) -> "_Noop":
            return self

        def set(self, *_a: Any, **_kw: Any) -> None:
            return None

    Gauge = _Noop  # type: ignore[assignment,misc]
    _HAS_PROM = False


# Per-model context utilisation (0..1+ — может превышать 1 если pre-cap пропустил).
krab_llm_context_usage_pct = Gauge(
    "krab_llm_context_usage_pct",
    "Доля prompt_tokens последнего запроса от context window модели (0..1+, Wave 128)",
    ["model"],
)


def get_context_window(model_id: str) -> int:
    """Возвращает context window для модели, или 0 если неизвестна."""
    if not model_id:
        return 0
    return int(MODEL_CONTEXT_WINDOW.get(model_id, 0))


def compute_context_usage_pct(model_id: str, prompt_tokens: int) -> float:
    """
    Считает prompt_tokens / context_window. Возвращает 0.0 если модель
    неизвестна (нет лимита в таблице) или prompt_tokens <= 0.
    Значение НЕ ограничено сверху — если pre-cap пропустил, может быть >1.
    """
    window = get_context_window(model_id)
    if window <= 0:
        return 0.0
    try:
        tokens = int(prompt_tokens)
    except (TypeError, ValueError):
        return 0.0
    if tokens <= 0:
        return 0.0
    return tokens / float(window)


def record_context_usage(model_id: str, prompt_tokens: int) -> None:
    """
    Обновляет gauge `krab_llm_context_usage_pct{model}` для модели.
    Fail-safe: ни одна ошибка не пробрасывается наверх.
    Co-located с CostAnalytics.record_usage (Wave 78) — вызывается рядом.
    """
    if not model_id:
        return
    pct = compute_context_usage_pct(model_id, prompt_tokens)
    if pct <= 0.0:
        return
    try:
        krab_llm_context_usage_pct.labels(model=model_id).set(pct)
    except Exception:  # noqa: BLE001 — prometheus optional
        return


__all__ = [
    "MODEL_CONTEXT_WINDOW",
    "compute_context_usage_pct",
    "get_context_window",
    "krab_llm_context_usage_pct",
    "record_context_usage",
]
