# -*- coding: utf-8 -*-
"""
Тесты для src/core/swarm_channels.py — live broadcast + owner intervention.
Обновлено для Forum Topics и _send_message() routing.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.swarm_channels import SwarmChannels, _parse_team_chats_env


class TestParseTeamChatsEnv:
    def test_basic(self):
        result = _parse_team_chats_env("traders:-1001234567890,coders:-1009876543210")
        assert result == {"traders": -1001234567890, "coders": -1009876543210}

    def test_with_spaces(self):
        result = _parse_team_chats_env(" traders : -1001234 , coders : -1005678 ")
        assert result == {"traders": -1001234, "coders": -1005678}

    def test_empty(self):
        assert _parse_team_chats_env("") == {}

    def test_invalid_chat_id(self):
        result = _parse_team_chats_env("traders:notanumber")
        assert result == {}

    def test_no_colon(self):
        result = _parse_team_chats_env("traders-1001234")
        assert result == {}


class TestSwarmChannelsPersistence:
    """Тесты persist/load через JSON файл."""

    @pytest.fixture()
    def channels(self, tmp_path: Path) -> SwarmChannels:
        ch = SwarmChannels()
        # Очищаем состояние от runtime-singleton
        ch._forum_chat_id = None
        ch._team_topics = {}
        ch._team_chats = {}
        # Подменяем путь через _STATE_PATH
        ch._STATE_PATH = str(tmp_path / "swarm_channels.json")
        return ch

    def test_register_and_retrieve(self, channels: SwarmChannels):
        channels.register_team_chat("traders", -1001234)
        assert channels.get_team_chat("traders") == -1001234
        assert channels.get_team_chat("coders") is None

    def test_is_swarm_chat(self, channels: SwarmChannels):
        channels.register_team_chat("traders", -1001234)
        assert channels.is_swarm_chat(-1001234) == "traders"
        assert channels.is_swarm_chat(-9999) is None

    def test_is_swarm_chat_forum(self, channels: SwarmChannels):
        """Forum mode: is_swarm_chat возвращает '_forum' для forum_chat_id."""
        channels._forum_chat_id = -100999
        channels._team_topics = {"traders": 19}
        assert channels.is_swarm_chat(-100999) == "_forum"

    def test_get_all_team_chats(self, channels: SwarmChannels):
        channels.register_team_chat("traders", -100)
        channels.register_team_chat("coders", -200)
        all_chats = channels.get_all_team_chats()
        assert all_chats == {"traders": -100, "coders": -200}


class TestSwarmChannelsIntervention:
    @pytest.fixture()
    def channels(self) -> SwarmChannels:
        ch = SwarmChannels()
        ch._team_chats = {"traders": -100}
        return ch

    def test_intervention_when_active(self, channels: SwarmChannels):
        channels.mark_round_active("traders")
        channels.add_intervention("traders", "Обрати внимание на ETH")
        result = channels.get_pending_intervention("traders")
        assert "Директива владельца" in result
        assert "ETH" in result

    def test_intervention_when_inactive(self, channels: SwarmChannels):
        channels.add_intervention("traders", "Текст")
        assert channels.get_pending_intervention("traders") == ""

    def test_intervention_consumed_once(self, channels: SwarmChannels):
        channels.mark_round_active("traders")
        channels.add_intervention("traders", "first")
        channels.add_intervention("traders", "second")
        result = channels.get_pending_intervention("traders")
        assert "first" in result
        assert "second" in result
        assert channels.get_pending_intervention("traders") == ""

    def test_round_lifecycle(self, channels: SwarmChannels):
        assert not channels.is_round_active("traders")
        channels.mark_round_active("traders")
        assert channels.is_round_active("traders")
        channels.mark_round_done("traders")
        assert not channels.is_round_active("traders")


class TestSwarmChannelsFormatting:
    def test_format_status_empty(self):
        ch = SwarmChannels()
        ch._forum_chat_id = None
        ch._team_topics = {}
        ch._team_chats = {}
        result = ch.format_status()
        assert "не настроены" in result

    def test_format_status_forum_mode(self):
        ch = SwarmChannels()
        ch._forum_chat_id = -100999
        ch._team_topics = {"traders": 19, "coders": 20}
        ch._team_chats = {}
        result = ch.format_status()
        assert "Forum" in result
        assert "traders" in result
        assert "coders" in result

    def test_format_status_legacy_mode(self):
        ch = SwarmChannels()
        ch._forum_chat_id = None
        ch._team_topics = {}
        ch._team_chats = {"traders": -100, "coders": -200}
        result = ch.format_status()
        assert "traders" in result
        assert "coders" in result


class TestSwarmChannelsBroadcast:
    """Тесты broadcast через _send_message (Forum Topics routing)."""

    @pytest.fixture()
    def channels(self) -> SwarmChannels:
        ch = SwarmChannels()
        # Forum mode
        ch._forum_chat_id = -100999
        ch._team_topics = {"traders": 19, "coders": 20, "crossteam": 23}
        ch._team_chats = {}
        # Мокаем _send_message напрямую (он уже тестирован отдельно)
        ch._send_message = AsyncMock()
        ch._client = MagicMock()  # нужен для проверки if _client
        return ch

    @pytest.mark.asyncio
    async def test_broadcast_role_step(self, channels: SwarmChannels):
        await channels.broadcast_role_step(
            team="traders", role_name="analyst", role_emoji="📊",
            role_title="Аналитик", text="BTC на поддержке 59k",
        )
        channels._send_message.assert_called_once()
        args, kwargs = channels._send_message.call_args
        assert args[0] == -100999  # forum chat_id
        assert kwargs.get("topic_id") == 19  # traders topic
        assert "Аналитик" in args[1]  # text

    @pytest.mark.asyncio
    async def test_broadcast_no_client(self):
        ch = SwarmChannels()
        ch._forum_chat_id = -100999
        ch._team_topics = {"traders": 19}
        ch._client = None
        # Не должен падать
        await ch.broadcast_role_step(
            team="traders", role_name="test", role_emoji="🤖",
            role_title="Test", text="test",
        )

    @pytest.mark.asyncio
    async def test_broadcast_unknown_team(self, channels: SwarmChannels):
        """Unknown team без crossteam fallback — тихо пропускаем."""
        ch = SwarmChannels()
        ch._forum_chat_id = None
        ch._team_topics = {}
        ch._team_chats = {}
        ch._send_message = AsyncMock()
        ch._client = MagicMock()
        await ch.broadcast_role_step(
            team="unknown", role_name="test", role_emoji="🤖",
            role_title="Test", text="test",
        )
        ch._send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_round_start(self, channels: SwarmChannels):
        await channels.broadcast_round_start(team="traders", topic="BTC анализ")
        channels._send_message.assert_called_once()
        args, kwargs = channels._send_message.call_args
        assert "Новый раунд" in args[1]
        assert "BTC" in args[1]
        assert kwargs.get("topic_id") == 19

    @pytest.mark.asyncio
    async def test_broadcast_round_end(self, channels: SwarmChannels):
        await channels.broadcast_round_end(team="traders", summary="BTC на 60k")
        channels._send_message.assert_called_once()
        args, kwargs = channels._send_message.call_args
        assert "завершён" in args[1]
        assert kwargs.get("topic_id") == 19

    @pytest.mark.asyncio
    async def test_broadcast_delegation(self, channels: SwarmChannels):
        """Делегирование идёт в crossteam топик."""
        await channels.broadcast_delegation(
            source_team="traders", target_team="coders",
            topic="Реализовать BTC Range Defender",
        )
        channels._send_message.assert_called_once()
        args, kwargs = channels._send_message.call_args
        assert "Делегирование" in args[1]
        assert kwargs.get("topic_id") == 23  # crossteam


class TestResolveDestination:
    def test_forum_mode(self):
        ch = SwarmChannels()
        ch._forum_chat_id = -100999
        ch._team_topics = {"traders": 19}
        ch._team_chats = {}
        chat_id, topic_id = ch._resolve_destination("traders")
        assert chat_id == -100999
        assert topic_id == 19

    def test_legacy_mode(self):
        ch = SwarmChannels()
        ch._forum_chat_id = None
        ch._team_topics = {}
        ch._team_chats = {"traders": -100}
        chat_id, topic_id = ch._resolve_destination("traders")
        assert chat_id == -100
        assert topic_id is None

    def test_crossteam_fallback(self):
        ch = SwarmChannels()
        ch._forum_chat_id = -100999
        ch._team_topics = {"crossteam": 23}
        ch._team_chats = {}
        chat_id, topic_id = ch._resolve_destination("unknown_team")
        assert chat_id == -100999
        assert topic_id == 23

    def test_no_destination(self):
        ch = SwarmChannels()
        ch._forum_chat_id = None
        ch._team_topics = {}
        ch._team_chats = {}
        chat_id, topic_id = ch._resolve_destination("unknown")
        assert chat_id is None
        assert topic_id is None


class TestIsForumMode:
    def test_forum_mode_true(self):
        ch = SwarmChannels()
        ch._forum_chat_id = -100999
        ch._team_topics = {"traders": 19}
        assert ch.is_forum_mode is True

    def test_forum_mode_false_no_chat(self):
        ch = SwarmChannels()
        ch._forum_chat_id = None
        ch._team_topics = {}
        assert ch.is_forum_mode is False

    def test_forum_mode_false_no_topics(self):
        ch = SwarmChannels()
        ch._forum_chat_id = -100999
        ch._team_topics = {}
        assert ch.is_forum_mode is False


class TestResolveTeamFromTopic:
    def test_found(self):
        ch = SwarmChannels()
        ch._team_topics = {"traders": 19, "coders": 20}
        assert ch.resolve_team_from_topic(19) == "traders"
        assert ch.resolve_team_from_topic(20) == "coders"

    def test_not_found(self):
        ch = SwarmChannels()
        ch._team_topics = {"traders": 19}
        assert ch.resolve_team_from_topic(999) is None
