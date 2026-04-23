# -*- coding: utf-8 -*-
"""
Тесты для model_tier_tracker — классификация, агрегация, форматирование.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pytest

from src.core.model_tier_tracker import (
    TIER_ORDER,
    TIER_PRICING,
    classify_tier,
    format_tier_summary_text,
    get_tier_histogram,
    get_tier_summary,
    get_tier_usage,
)


# ---------------------------------------------------------------------------
# Фиктивный CallRecord (чтобы не тянуть зависимость от cost_analytics)
# ---------------------------------------------------------------------------


@dataclass
class _FakeRecord:
    model_id: str
    input_tokens: int = 1000
    output_tokens: int = 500
    cost_usd: float = 0.01
    timestamp: float = field(default_factory=time.time)
    channel: str = ""
    is_fallback: bool = False
    tool_calls_count: int = 0
    context_tokens: int = 0


def _rec(model_id: str, channel: str = "", hours_ago: float = 0.0) -> _FakeRecord:
    ts = time.time() - hours_ago * 3600
    return _FakeRecord(model_id=model_id, channel=channel, timestamp=ts)


# ---------------------------------------------------------------------------
# classify_tier
# ---------------------------------------------------------------------------


class TestClassifyTier:
    @pytest.mark.parametrize(
        "model_id,expected",
        [
            # Claude tiers
            ("claude-opus-4", "claude_opus"),
            ("claude-opus-latest", "claude_opus"),
            ("claude-sonnet-4-5", "claude_sonnet"),
            ("claude-3-5-sonnet-20241022", "claude_sonnet"),
            ("claude-haiku-3", "claude_haiku"),
            ("claude-3-haiku-20240307", "claude_haiku"),
            ("claude-generic", "claude_sonnet"),  # generic claude → sonnet
            # OpenAI
            ("gpt-5", "openai_gpt5"),
            ("gpt-5.4", "openai_gpt5"),
            ("gpt-4o", "openai_gpt4"),
            ("gpt-4-turbo", "openai_gpt4"),
            ("codex-davinci", "openai_gpt5"),
            ("o3-mini", "openai_gpt5"),
            ("o1-preview", "openai_gpt4"),
            # Google Gemini
            ("google/gemini-3-pro-preview", "google_gemini_pro"),
            ("gemini-2.5-pro", "google_gemini_pro"),
            ("gemini-3-flash-preview", "google_gemini_flash"),
            ("gemini-2.5-flash", "google_gemini_flash"),
            ("gemini-nano", "google_gemini_flash"),
            ("gemini-ultra", "google_gemini_pro"),
            ("gemini-exp-1206", "google_gemini_pro"),
            # Local
            ("local-qwen-7b", "local_lmstudio"),
            ("mlx-gemma-2b", "local_lmstudio"),
            ("my-model.gguf", "local_lmstudio"),
            ("lmstudio-llama3", "local_lmstudio"),
            # Unknown / empty
            ("unknown-provider-xyz", "unknown"),
            ("", "unknown"),
        ],
    )
    def test_classify(self, model_id: str, expected: str):
        assert classify_tier(model_id) == expected

    def test_flash_not_misclassified_as_pro(self):
        """gemini-3-flash-preview должен быть flash, не pro."""
        assert classify_tier("gemini-3-flash-preview") == "google_gemini_flash"

    def test_gemini_pro_preview_is_pro(self):
        """gemini-3-pro-preview (без flash) — pro tier."""
        assert classify_tier("google/gemini-3-pro-preview") == "google_gemini_pro"


# ---------------------------------------------------------------------------
# get_tier_usage
# ---------------------------------------------------------------------------


class TestGetTierUsage:
    def test_empty_calls_returns_empty(self):
        result = get_tier_usage([], since_hours=24)
        assert result == {}

    def test_aggregates_same_tier(self):
        calls = [_rec("claude-opus-4"), _rec("claude-opus-latest")]
        result = get_tier_usage(calls, since_hours=24)
        assert "claude_opus" in result
        assert result["claude_opus"]["calls"] == 2

    def test_pct_sums_100(self):
        calls = [_rec("claude-opus-4"), _rec("claude-sonnet-4-5"), _rec("gemini-2.5-flash")]
        result = get_tier_usage(calls, since_hours=24)
        total_pct = sum(v["pct_calls"] for v in result.values())
        assert abs(total_pct - 100.0) < 0.5  # допуск на округление

    def test_old_records_excluded(self):
        old = _rec("claude-opus-4", hours_ago=50)
        recent = _rec("claude-sonnet-4-5", hours_ago=1)
        result = get_tier_usage([old, recent], since_hours=24)
        assert "claude_opus" not in result
        assert "claude_sonnet" in result

    def test_cost_usd_estimated_by_tier(self):
        """Стоимость рассчитывается по тирному прайсу, а не из record.cost_usd."""
        calls = [_FakeRecord(model_id="claude-opus-4", input_tokens=1_000_000, output_tokens=0)]
        result = get_tier_usage(calls, since_hours=24)
        expected = TIER_PRICING["claude_opus"]["input"]  # $15 за 1M input
        assert abs(result["claude_opus"]["cost_usd"] - expected) < 0.01

    def test_local_model_zero_cost(self):
        calls = [_FakeRecord(model_id="local-qwen-7b", input_tokens=1_000_000, output_tokens=500_000)]
        result = get_tier_usage(calls, since_hours=24)
        assert result["local_lmstudio"]["cost_usd"] == 0.0

    def test_label_in_result(self):
        calls = [_rec("claude-sonnet-4-5")]
        result = get_tier_usage(calls, since_hours=24)
        assert result["claude_sonnet"]["label"] == "Claude Sonnet"


# ---------------------------------------------------------------------------
# get_tier_histogram
# ---------------------------------------------------------------------------


class TestGetTierHistogram:
    def test_sorted_descending(self):
        calls = [
            _rec("claude-opus-4"),
            _rec("gemini-2.5-flash"),
            _rec("gemini-2.5-flash"),
            _rec("gemini-2.5-flash"),
        ]
        hist = get_tier_histogram(calls, since_hours=24)
        counts = [c for _, c in hist]
        assert counts == sorted(counts, reverse=True)

    def test_returns_list_of_tuples(self):
        calls = [_rec("claude-opus-4")]
        hist = get_tier_histogram(calls, since_hours=24)
        assert isinstance(hist, list)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in hist)


# ---------------------------------------------------------------------------
# get_tier_summary
# ---------------------------------------------------------------------------


class TestGetTierSummary:
    def _make_calls(self):
        return [
            _rec("claude-opus-4", channel="telegram"),
            _rec("claude-sonnet-4-5", channel="swarm"),
            _rec("gemini-3-flash-preview", channel="telegram"),
            _rec("local-qwen-7b", channel="background"),
        ]

    def test_has_required_keys(self):
        summary = get_tier_summary(self._make_calls(), since_hours=24)
        for key in ("per_tier", "histogram", "by_channel_tier", "totals", "pricing_table", "tier_order"):
            assert key in summary

    def test_totals_calls_match(self):
        calls = self._make_calls()
        summary = get_tier_summary(calls, since_hours=24)
        assert summary["totals"]["calls"] == 4

    def test_by_channel_tier_populated(self):
        calls = self._make_calls()
        summary = get_tier_summary(calls, since_hours=24)
        ch = summary["by_channel_tier"]
        assert "telegram" in ch
        assert "swarm" in ch

    def test_pricing_table_present(self):
        summary = get_tier_summary([], since_hours=24)
        assert "claude_opus" in summary["pricing_table"]

    def test_since_hours_respected(self):
        calls = [
            _rec("claude-opus-4", hours_ago=2),
            _rec("claude-sonnet-4-5", hours_ago=30),  # старый
        ]
        summary = get_tier_summary(calls, since_hours=24)
        assert summary["totals"]["calls"] == 1


# ---------------------------------------------------------------------------
# format_tier_summary_text
# ---------------------------------------------------------------------------


class TestFormatTierSummaryText:
    def test_no_data_message(self):
        summary = get_tier_summary([], since_hours=24)
        text = format_tier_summary_text(summary)
        assert "Нет данных" in text

    def test_contains_tier_labels(self):
        calls = [_rec("claude-opus-4"), _rec("gemini-2.5-flash")]
        summary = get_tier_summary(calls, since_hours=24)
        text = format_tier_summary_text(summary)
        assert "Claude Opus" in text
        assert "Gemini Flash" in text

    def test_contains_cost(self):
        calls = [_FakeRecord(model_id="claude-opus-4", input_tokens=1000, output_tokens=500)]
        summary = get_tier_summary(calls, since_hours=24)
        text = format_tier_summary_text(summary)
        assert "$" in text

    def test_contains_totals_line(self):
        calls = [_rec("claude-sonnet-4-5")]
        summary = get_tier_summary(calls, since_hours=24)
        text = format_tier_summary_text(summary)
        assert "Итого" in text
