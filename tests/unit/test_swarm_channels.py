# -*- coding: utf-8 -*-
"""
Тесты для src/core/swarm_channels.py — live broadcast + owner intervention.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
    @pytest.fixture()
    def channels(self, tmp_path: Path) -> SwarmChannels:
        ch = SwarmChannels()
        ch._path  # access property — we'll monkeypatch _STATE_PATH
        # Override _path via __dict__ trick
        original_path = SwarmChannels._path
        ch.__class__ = type("TestSwarmChannels", (SwarmChannels,), {
            "_path": property(lambda self: tmp_path / "swarm_channels.json")
        })
        ch._team_chats = {}
        return ch

    def test_register_and_retrieve(self, channels: SwarmChannels):
        channels.register_team_chat("traders", -1001234)
        assert channels.get_team_chat("traders") == -1001234
        assert channels.get_team_chat("coders") is None

    def test_is_swarm_chat(self, channels: SwarmChannels):
        channels.register_team_chat("traders", -1001234)
        assert channels.is_swarm_chat(-1001234) == "traders"
        assert channels.is_swarm_chat(-9999) is None

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
        # Раунд не активен — intervention игнорируется
        channels.add_intervention("traders", "Текст")
        assert channels.get_pending_intervention("traders") == ""

    def test_intervention_consumed_once(self, channels: SwarmChannels):
        channels.mark_round_active("traders")
        channels.add_intervention("traders", "first")
        channels.add_intervention("traders", "second")
        result = channels.get_pending_intervention("traders")
        assert "first" in result
        assert "second" in result
        # Второй вызов — пусто
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
        ch._team_chats = {}
        result = ch.format_status()
        assert "не настроены" in result

    def test_format_status_with_teams(self):
        ch = SwarmChannels()
        ch._team_chats = {"traders": -100, "coders": -200}
        result = ch.format_status()
        assert "traders" in result
        assert "coders" in result


class TestSwarmChannelsBroadcast:
    @pytest.fixture()
    def channels(self) -> SwarmChannels:
        ch = SwarmChannels()
        ch._team_chats = {"traders": -100}
        ch._client = AsyncMock()
        return ch

    @pytest.mark.asyncio
    async def test_broadcast_role_step(self, channels: SwarmChannels):
        await channels.broadcast_role_step(
            team="traders", role_name="analyst", role_emoji="📊",
            role_title="Аналитик", text="BTC на поддержке 59k",
        )
        channels._client.send_message.assert_called_once()
        call_args = channels._client.send_message.call_args
        assert call_args[0][0] == -100
        assert "Аналитик" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_broadcast_no_client(self):
        ch = SwarmChannels()
        ch._team_chats = {"traders": -100}
        ch._client = None
        # Не должен падать
        await ch.broadcast_role_step(
            team="traders", role_name="test", role_emoji="🤖",
            role_title="Test", text="test",
        )

    @pytest.mark.asyncio
    async def test_broadcast_no_team_chat(self, channels: SwarmChannels):
        # Для unknown team — тихо пропускаем
        await channels.broadcast_role_step(
            team="unknown", role_name="test", role_emoji="🤖",
            role_title="Test", text="test",
        )
        channels._client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_round_start(self, channels: SwarmChannels):
        await channels.broadcast_round_start(team="traders", topic="BTC анализ")
        channels._client.send_message.assert_called_once()
        text = channels._client.send_message.call_args[0][1]
        assert "Новый раунд" in text
        assert "BTC" in text

    @pytest.mark.asyncio
    async def test_broadcast_round_end(self, channels: SwarmChannels):
        await channels.broadcast_round_end(team="traders", summary="BTC на 60k")
        channels._client.send_message.assert_called_once()
        text = channels._client.send_message.call_args[0][1]
        assert "завершён" in text
