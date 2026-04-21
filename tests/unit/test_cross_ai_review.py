"""Тесты для src/core/cross_ai_review.py."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.cross_ai_review import (
    ReviewResult,
    parse_review_bullets,
    request_review,
)

# ---------------------------------------------------------------------------
# parse_review_bullets
# ---------------------------------------------------------------------------


class TestParseReviewBullets:
    def test_normal_dash_bullets(self) -> None:
        text = "- First point\n- Second point\n- Third point"
        result = parse_review_bullets(text)
        assert result == ["First point", "Second point", "Third point"]

    def test_asterisk_bullets(self) -> None:
        text = "* Alpha\n* Beta\n* Gamma"
        result = parse_review_bullets(text)
        assert result == ["Alpha", "Beta", "Gamma"]

    def test_numbered_list(self) -> None:
        text = "1. First item\n2. Second item\n10. Tenth item"
        result = parse_review_bullets(text)
        assert result == ["First item", "Second item", "Tenth item"]

    def test_nested_bullets_skipped(self) -> None:
        text = "- Top level\n  - Nested item\n    - Deep nested\n- Another top"
        result = parse_review_bullets(text)
        assert result == ["Top level", "Another top"]

    def test_mixed_markers(self) -> None:
        text = "- Dash item\n* Star item\n1. Numbered item"
        result = parse_review_bullets(text)
        assert result == ["Dash item", "Star item", "Numbered item"]

    def test_empty_text(self) -> None:
        assert parse_review_bullets("") == []

    def test_no_bullets(self) -> None:
        text = "Just a paragraph\nwith no bullets at all."
        assert parse_review_bullets(text) == []

    def test_strips_extra_whitespace(self) -> None:
        text = "-   Leading spaces in content   \n* Also trimmed   "
        result = parse_review_bullets(text)
        assert result == ["Leading spaces in content", "Also trimmed"]

    def test_inline_text_before_bullet_ignored(self) -> None:
        # Строки без маркера в начале не должны захватываться
        text = "Intro text\n- Actual bullet\nTrailing text"
        result = parse_review_bullets(text)
        assert result == ["Actual bullet"]


# ---------------------------------------------------------------------------
# request_review
# ---------------------------------------------------------------------------


class TestRequestReview:
    @pytest.fixture
    def mock_channels(self) -> MagicMock:
        ch = MagicMock()
        ch.broadcast_to_topic = AsyncMock(return_value=None)
        return ch

    @pytest.mark.asyncio
    async def test_calls_broadcast_with_correct_key(
        self, mock_channels: MagicMock
    ) -> None:
        result = await request_review(
            artifact_url="https://example.com/design.fig",
            topic_key="analysts",
            prompt_context="Review the API surface.",
            swarm_channels=mock_channels,
        )
        mock_channels.broadcast_to_topic.assert_awaited_once()
        call_args = mock_channels.broadcast_to_topic.call_args
        assert call_args[0][0] == "analysts"

    @pytest.mark.asyncio
    async def test_message_contains_artifact_url(
        self, mock_channels: MagicMock
    ) -> None:
        await request_review(
            artifact_url="https://example.com/design.fig",
            topic_key="coders",
            prompt_context="Check the structure.",
            swarm_channels=mock_channels,
        )
        sent_text: str = mock_channels.broadcast_to_topic.call_args[0][1]
        assert "https://example.com/design.fig" in sent_text
        assert "Check the structure." in sent_text

    @pytest.mark.asyncio
    async def test_returns_ok_true_on_success(
        self, mock_channels: MagicMock
    ) -> None:
        result = await request_review(
            artifact_url="https://example.com/art.png",
            topic_key="traders",
            prompt_context="Context here.",
            swarm_channels=mock_channels,
        )
        assert isinstance(result, ReviewResult)
        assert result.ok is True
        assert result.bullets == []
        assert result.error is None
        assert result.reviewer == "traders"

    @pytest.mark.asyncio
    async def test_handles_broadcast_exception(self) -> None:
        bad_channels = MagicMock()
        bad_channels.broadcast_to_topic = AsyncMock(
            side_effect=RuntimeError("connection refused")
        )
        result = await request_review(
            artifact_url="https://example.com/x",
            topic_key="creative",
            prompt_context="Review please.",
            swarm_channels=bad_channels,
        )
        assert result.ok is False
        assert "connection refused" in (result.error or "")
        assert result.reviewer == "creative"

    @pytest.mark.asyncio
    async def test_works_without_swarm_channels(self) -> None:
        # Не должен падать при swarm_channels=None
        result = await request_review(
            artifact_url="https://example.com/no_channel",
            topic_key="analysts",
            prompt_context="Any context.",
            swarm_channels=None,
        )
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_timeout_returns_ok_false(self) -> None:
        slow_channels = MagicMock()

        async def _slow(*_a: object, **_kw: object) -> None:
            await asyncio.sleep(10)

        slow_channels.broadcast_to_topic = _slow
        result = await request_review(
            artifact_url="https://example.com/slow",
            topic_key="coders",
            prompt_context="Slow test.",
            timeout_sec=0,  # срабатывает немедленно
            swarm_channels=slow_channels,
        )
        assert result.ok is False
        assert result.error is not None
        assert "timeout" in result.error

    @pytest.mark.asyncio
    async def test_reviewer_field_matches_topic_key(
        self, mock_channels: MagicMock
    ) -> None:
        result = await request_review(
            artifact_url="https://example.com/art",
            topic_key="my_topic",
            prompt_context="ctx",
            swarm_channels=mock_channels,
        )
        assert result.reviewer == "my_topic"
