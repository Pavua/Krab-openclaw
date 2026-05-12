# -*- coding: utf-8 -*-
"""Wave 78: Token-cost FinOps tracking.

После Wave 66/67 leak fix: paid Gemini сжёг €40 за неделю при минимальном
user-трафике. Экспонируем токены и стоимость каждого completion для
realtime аномалий.
"""

from __future__ import annotations

# Конвертация USD → EUR (упрощённый фиксированный курс).
_USD_TO_EUR = 0.92

# Wave 78 pricing per 1M tokens (USD): (prompt, completion).
# thoughts токены считаем по тарифу completion. Unknown model → 0.0.
_MODEL_PRICING_USD_PER_1M: dict[str, tuple[float, float]] = {
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-pro-preview": (1.25, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-3-flash-preview": (0.30, 2.50),
    "gemini-3-pro-preview": (1.25, 10.0),
    "gemini-3.1-pro-preview": (1.25, 10.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-opus-4": (15.0, 75.0),
    "gpt-5.5": (5.0, 20.0),
    "gpt-5": (5.0, 20.0),
}


def _resolve_pricing(model: str) -> tuple[float, float]:
    """Pricing по model: точное → suffix match → 0/0."""
    if not model:
        return (0.0, 0.0)
    key = model.split("/", 1)[1] if "/" in model else model
    key = key.lower().strip()
    if key in _MODEL_PRICING_USD_PER_1M:
        return _MODEL_PRICING_USD_PER_1M[key]
    for known_key, prices in _MODEL_PRICING_USD_PER_1M.items():
        if key.startswith(known_key) or known_key in key:
            return prices
    return (0.0, 0.0)


def _calculate_cost_eur(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    thoughts_tokens: int = 0,
) -> float:
    """Стоимость completion в EUR. Unknown model → 0.0."""
    price_in, price_out = _resolve_pricing(model)
    if price_in <= 0 and price_out <= 0:
        return 0.0
    cost_usd = (
        (max(0, prompt_tokens) / 1_000_000.0) * price_in
        + (max(0, completion_tokens) / 1_000_000.0) * price_out
        + (max(0, thoughts_tokens) / 1_000_000.0) * price_out
    )
    return round(cost_usd * _USD_TO_EUR, 6)


try:
    from prometheus_client import Counter as _Counter78  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _Histogram78  # type: ignore[import-not-found]

    krab_tokens_consumed_total = _Counter78(
        "krab_tokens_consumed_total",
        "Total tokens consumed by completions, labeled by provider/model/kind",
        ["provider", "model", "kind"],
    )
    krab_completion_cost_eur_total = _Counter78(
        "krab_completion_cost_eur_total",
        "Cumulative completion cost in EUR by provider/model (Wave 78 FinOps)",
        ["provider", "model"],
    )
    krab_completion_cost_eur = _Histogram78(
        "krab_completion_cost_eur",
        "Per-completion cost in EUR (Wave 78 FinOps)",
        ["provider", "model"],
        buckets=(0.0001, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0),
    )
except Exception:  # noqa: BLE001
    krab_tokens_consumed_total = None  # type: ignore[assignment]
    krab_completion_cost_eur_total = None  # type: ignore[assignment]
    krab_completion_cost_eur = None  # type: ignore[assignment]


def _facade():
    """Lazy import фасада."""
    import src.core.prometheus_metrics as _pm  # noqa: PLC0415

    return _pm


def record_completion_cost(
    *,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    thoughts_tokens: int = 0,
    cost_eur: float | None = None,
) -> float:
    """Wave 78: фиксирует токены и стоимость одного completion.

    Возвращает финальный cost_eur. Fail-safe.
    """
    try:
        prov = (provider or "unknown")[:40]
        mod = (model or "unknown")[:80]
        pt = max(0, int(prompt_tokens or 0))
        ct = max(0, int(completion_tokens or 0))
        tt = max(0, int(thoughts_tokens or 0))

        if cost_eur is None:
            cost_eur = _calculate_cost_eur(model, pt, ct, tt)
        else:
            cost_eur = max(0.0, float(cost_eur))

        pm = _facade()
        if pm.krab_tokens_consumed_total is not None:
            if pt > 0:
                pm.krab_tokens_consumed_total.labels(provider=prov, model=mod, kind="prompt").inc(
                    pt
                )
            if ct > 0:
                pm.krab_tokens_consumed_total.labels(
                    provider=prov, model=mod, kind="completion"
                ).inc(ct)
            if tt > 0:
                pm.krab_tokens_consumed_total.labels(provider=prov, model=mod, kind="thoughts").inc(
                    tt
                )

        if cost_eur > 0:
            if pm.krab_completion_cost_eur_total is not None:
                pm.krab_completion_cost_eur_total.labels(provider=prov, model=mod).inc(cost_eur)
            if pm.krab_completion_cost_eur is not None:
                pm.krab_completion_cost_eur.labels(provider=prov, model=mod).observe(cost_eur)
        return cost_eur
    except Exception:  # noqa: BLE001
        return 0.0


def _infer_provider_from_model(model: str) -> str:
    """Best-effort провайдер по имени модели."""
    if not model:
        return "unknown"
    if "/" in model:
        return model.split("/", 1)[0].lower()
    low = model.lower()
    if "gemini" in low or "gemma" in low:
        return "google"
    if "claude" in low:
        return "anthropic"
    if "gpt" in low or low.startswith("o1") or low.startswith("o3"):
        return "openai"
    if "local" in low or "mlx" in low or "gguf" in low:
        return "local"
    return "unknown"
