# -*- coding: utf-8 -*-
"""
Model Tier Classifier + Usage Aggregator.

Классифицирует model_id по тирам (opus/sonnet/haiku/gpt5/gemini_pro/…)
и предоставляет агрегированную аналитику затрат по тирам.

Tiers:
- claude_opus       — дорогой, архитектурные решения
- claude_sonnet     — сбалансированный, основная работа
- claude_haiku      — дешёвый, summaries, простые задачи
- openai_gpt5       — codex-cli, GPT-5
- openai_gpt4       — legacy GPT-4
- google_gemini_pro — vision, глубокий анализ
- google_gemini_flash — переводчик, быстрые задачи
- local_lmstudio    — локальные модели (бесплатно)
- unknown           — неизвестные модели
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cost_analytics import CallRecord

# ---------------------------------------------------------------------------
# Ценники за 1M токенов (input, output) в USD — сводная таблица тиров
# Данные: Anthropic/OpenAI/Google публичные прайсы (апрель 2026)
# ---------------------------------------------------------------------------
TIER_PRICING: dict[str, dict[str, float]] = {
    "claude_opus": {"input": 15.00, "output": 75.00},
    "claude_sonnet": {"input": 3.00, "output": 15.00},
    "claude_haiku": {"input": 0.80, "output": 4.00},
    "openai_gpt5": {"input": 10.00, "output": 30.00},
    "openai_gpt4": {"input": 10.00, "output": 30.00},
    "google_gemini_pro": {"input": 1.25, "output": 5.00},
    "google_gemini_flash": {"input": 0.075, "output": 0.30},
    "local_lmstudio": {"input": 0.0, "output": 0.0},
    "unknown": {"input": 0.10, "output": 0.30},
}

# Человекочитаемые метки для display
TIER_LABELS: dict[str, str] = {
    "claude_opus": "Claude Opus",
    "claude_sonnet": "Claude Sonnet",
    "claude_haiku": "Claude Haiku",
    "openai_gpt5": "GPT-5 / Codex",
    "openai_gpt4": "GPT-4",
    "google_gemini_pro": "Gemini Pro",
    "google_gemini_flash": "Gemini Flash",
    "local_lmstudio": "LM Studio (local)",
    "unknown": "Unknown",
}

# Порядок тиров по убыванию стоимости (для display/sorting)
TIER_ORDER = [
    "claude_opus",
    "openai_gpt5",
    "openai_gpt4",
    "claude_sonnet",
    "google_gemini_pro",
    "claude_haiku",
    "google_gemini_flash",
    "local_lmstudio",
    "unknown",
]


def classify_tier(model_id: str) -> str:
    """
    Классифицирует model_id по тиру.

    Порядок проверок: от специфичных к общим, чтобы
    «gemini-3-pro» не попал в gemini_flash раньше времени.
    """
    if not model_id:
        return "unknown"
    low = model_id.lower()

    # Локальные модели — проверяем первыми
    if any(tok in low for tok in ("local", "mlx", "gguf", "lmstudio", "lm-studio", "lm_studio")):
        return "local_lmstudio"

    # Anthropic Claude
    if "opus" in low:
        return "claude_opus"
    if "sonnet" in low:
        return "claude_sonnet"
    if "haiku" in low:
        return "claude_haiku"
    # Generic claude без тира — treat as sonnet
    if "claude" in low:
        return "claude_sonnet"

    # OpenAI
    if "gpt-5" in low or "gpt5" in low or "o3" in low or "o4" in low:
        return "openai_gpt5"
    if "gpt-4" in low or "gpt4" in low or "o1" in low or "o2" in low:
        return "openai_gpt4"
    if "codex" in low:
        return "openai_gpt5"

    # Google — сначала pro (специфичнее), потом flash
    if "gemini" in low:
        if any(tok in low for tok in ("pro", "ultra", "exp", "preview")):
            # «gemini-3-flash-preview» — flash tier, не pro
            if "flash" in low:
                return "google_gemini_flash"
            return "google_gemini_pro"
        if "flash" in low or "nano" in low or "lite" in low:
            return "google_gemini_flash"
        # gemini без уточнения — pro
        return "google_gemini_pro"

    return "unknown"


def _estimate_cost_usd(tier: str, input_tokens: int, output_tokens: int) -> float:
    """Оценочная стоимость по тирному прайсу."""
    pricing = TIER_PRICING.get(tier, TIER_PRICING["unknown"])
    inp_cost = (input_tokens / 1_000_000.0) * pricing["input"]
    out_cost = (output_tokens / 1_000_000.0) * pricing["output"]
    return round(inp_cost + out_cost, 6)


def get_tier_usage(calls: "list[CallRecord]", since_hours: float = 24) -> dict[str, Any]:
    """
    Агрегирует usage по тирам за последние `since_hours` часов.

    Возвращает dict {tier: {calls, tokens_in, tokens_out, cost_usd, pct_calls}}.
    cost_usd рассчитывается по тирному прайсу (не из записи CallRecord,
    т.к. CallRecord использует усреднённый дефолтный прайс).
    """
    cutoff = time.time() - since_hours * 3600
    by_tier: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    )

    for r in calls:
        if r.timestamp < cutoff:
            continue
        tier = classify_tier(r.model_id)
        by_tier[tier]["calls"] += 1
        by_tier[tier]["tokens_in"] += r.input_tokens
        by_tier[tier]["tokens_out"] += r.output_tokens
        by_tier[tier]["cost_usd"] += _estimate_cost_usd(tier, r.input_tokens, r.output_tokens)

    total_calls = sum(v["calls"] for v in by_tier.values()) or 1
    result: dict[str, Any] = {}
    for tier, data in by_tier.items():
        result[tier] = {
            "calls": data["calls"],
            "tokens_in": data["tokens_in"],
            "tokens_out": data["tokens_out"],
            "cost_usd": round(data["cost_usd"], 6),
            "pct_calls": round(data["calls"] / total_calls * 100, 1),
            "label": TIER_LABELS.get(tier, tier),
        }

    return result


def get_tier_histogram(calls: "list[CallRecord]", since_hours: float = 24) -> list[tuple[str, int]]:
    """
    Возвращает список (tier, calls_count) отсортированный по убыванию calls,
    для bar-chart отображения.
    """
    usage = get_tier_usage(calls, since_hours=since_hours)
    hist = [(tier, data["calls"]) for tier, data in usage.items()]
    return sorted(hist, key=lambda x: -x[1])


def get_tier_summary(calls: "list[CallRecord]", since_hours: float = 24) -> dict[str, Any]:
    """
    Полный summary для API endpoint /api/costs/by-tier.

    Включает:
    - per_tier: агрегация по тиру
    - histogram: отсортированный список для bar chart
    - by_channel_tier: {channel: {tier: count}} для per-channel breakdown
    - totals: суммарные показатели
    - pricing_table: справочные цены
    """
    cutoff = time.time() - since_hours * 3600
    filtered = [r for r in calls if r.timestamp >= cutoff]

    per_tier = get_tier_usage(filtered, since_hours=since_hours * 999)  # уже отфильтровали
    histogram = get_tier_histogram(filtered, since_hours=since_hours * 999)

    # Per-channel breakdown
    by_channel_tier: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in filtered:
        ch = r.channel or "unknown"
        tier = classify_tier(r.model_id)
        by_channel_tier[ch][tier] += 1

    # Totals
    total_calls = sum(v["calls"] for v in per_tier.values())
    total_cost = sum(v["cost_usd"] for v in per_tier.values())
    total_tokens_in = sum(v["tokens_in"] for v in per_tier.values())
    total_tokens_out = sum(v["tokens_out"] for v in per_tier.values())

    return {
        "since_hours": since_hours,
        "per_tier": per_tier,
        "histogram": histogram,
        "by_channel_tier": {ch: dict(td) for ch, td in by_channel_tier.items()},
        "totals": {
            "calls": total_calls,
            "cost_usd": round(total_cost, 6),
            "tokens_in": total_tokens_in,
            "tokens_out": total_tokens_out,
        },
        "pricing_table": TIER_PRICING,
        "tier_order": TIER_ORDER,
    }


def format_tier_summary_text(summary: dict[str, Any]) -> str:
    """Форматирует summary в краткий текст для Telegram (!models команда)."""
    lines = ["**Модели по тирам** (за 24ч)", "─────────────────────"]
    per_tier = summary.get("per_tier", {})
    totals = summary.get("totals", {})

    if not per_tier:
        lines.append("Нет данных за период.")
        return "\n".join(lines)

    for tier in TIER_ORDER:
        if tier not in per_tier:
            continue
        data = per_tier[tier]
        label = data.get("label", tier)
        calls = data["calls"]
        cost = data["cost_usd"]
        pct = data["pct_calls"]
        bar_len = max(1, round(pct / 10))
        bar = "█" * bar_len + "░" * (10 - bar_len)
        lines.append(f"[{bar}] {label}")
        lines.append(f"  {calls} calls ({pct}%) · ${cost:.4f}")

    lines.append("─────────────────────")
    lines.append(f"Итого: {totals.get('calls', 0)} вызовов · ${totals.get('cost_usd', 0.0):.4f}")
    return "\n".join(lines)
