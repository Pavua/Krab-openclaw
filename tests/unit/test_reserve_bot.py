# -*- coding: utf-8 -*-
"""
Тесты для src/reserve_bot.py.

Покрывает:
- _resolve_bot_token: приоритет config vs openclaw.json
- _resolve_owner_ids: объединение config и openclaw.json
- _split_text: разбивка длинных сообщений
- ReserveBotBridge: init, is_configured, is_running, start/stop/send_to_owner
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ------------------------------------------------------------------
# _resolve_bot_token
# ------------------------------------------------------------------


class TestResolveBotToken:
    def test_returns_config_token_when_set(self) -> None:
        """config.TELEGRAM_BOT_TOKEN имеет приоритет над openclaw.json."""
        fake_config = SimpleNamespace(TELEGRAM_BOT_TOKEN="config_token_123")
        with patch("src.reserve_bot.config", fake_config):
            from src.reserve_bot import _resolve_bot_token

            assert _resolve_bot_token() == "config_token_123"

    def test_falls_back_to_openclaw_json(self, tmp_path: Path) -> None:
        """Если config пустой — берёт botToken из openclaw.json."""
        openclaw = tmp_path / "openclaw.json"
        openclaw.write_text(
            json.dumps({"channels": {"telegram": {"botToken": "json_token_456"}}}),
            encoding="utf-8",
        )
        fake_config = SimpleNamespace(TELEGRAM_BOT_TOKEN="")
        with (
            patch("src.reserve_bot.config", fake_config),
            patch("src.reserve_bot._OPENCLAW_JSON", openclaw),
        ):
            from src.reserve_bot import _resolve_bot_token

            assert _resolve_bot_token() == "json_token_456"

    def test_returns_empty_when_both_missing(self, tmp_path: Path) -> None:
        """Если ни config, ни openclaw.json не содержат токен — возвращает ''."""
        openclaw = tmp_path / "openclaw.json"
        openclaw.write_text(json.dumps({}), encoding="utf-8")
        fake_config = SimpleNamespace(TELEGRAM_BOT_TOKEN="")
        with (
            patch("src.reserve_bot.config", fake_config),
            patch("src.reserve_bot._OPENCLAW_JSON", openclaw),
        ):
            from src.reserve_bot import _resolve_bot_token

            assert _resolve_bot_token() == ""

    def test_returns_empty_when_file_missing(self) -> None:
        """Отсутствующий openclaw.json не вызывает исключение — возвращает ''."""
        fake_config = SimpleNamespace(TELEGRAM_BOT_TOKEN="")
        with (
            patch("src.reserve_bot.config", fake_config),
            patch("src.reserve_bot._OPENCLAW_JSON", Path("/nonexistent/path/openclaw.json")),
        ):
            from src.reserve_bot import _resolve_bot_token

            assert _resolve_bot_token() == ""

    def test_strips_whitespace_from_config_token(self) -> None:
        """Пробелы вокруг токена из config обрезаются."""
        fake_config = SimpleNamespace(TELEGRAM_BOT_TOKEN="  trimmed_token  ")
        with patch("src.reserve_bot.config", fake_config):
            from src.reserve_bot import _resolve_bot_token

            assert _resolve_bot_token() == "trimmed_token"


# ------------------------------------------------------------------
# _resolve_owner_ids
# ------------------------------------------------------------------


class TestResolveOwnerIds:
    def test_parses_config_ids(self) -> None:
        """config.OWNER_USER_IDS преобразуются в int."""
        fake_config = SimpleNamespace(TELEGRAM_BOT_TOKEN="", OWNER_USER_IDS=["111", "222"])
        with (
            patch("src.reserve_bot.config", fake_config),
            patch("src.reserve_bot._OPENCLAW_JSON", Path("/nonexistent/openclaw.json")),
        ):
            from src.reserve_bot import _resolve_owner_ids

            result = _resolve_owner_ids()
            assert set(result) == {111, 222}

    def test_merges_openclaw_json_ids(self, tmp_path: Path) -> None:
        """allowFrom из openclaw.json добавляется к config-ids без дублей."""
        openclaw = tmp_path / "openclaw.json"
        openclaw.write_text(
            json.dumps({"channels": {"telegram": {"allowFrom": [333, 111]}}}),
            encoding="utf-8",
        )
        fake_config = SimpleNamespace(TELEGRAM_BOT_TOKEN="", OWNER_USER_IDS=["111"])
        with (
            patch("src.reserve_bot.config", fake_config),
            patch("src.reserve_bot._OPENCLAW_JSON", openclaw),
        ):
            from src.reserve_bot import _resolve_owner_ids

            result = _resolve_owner_ids()
            assert set(result) == {111, 333}

    def test_skips_invalid_ids(self) -> None:
        """Невалидные значения (строки, None) в OWNER_USER_IDS игнорируются."""
        fake_config = SimpleNamespace(TELEGRAM_BOT_TOKEN="", OWNER_USER_IDS=["abc", None, "999"])
        with (
            patch("src.reserve_bot.config", fake_config),
            patch("src.reserve_bot._OPENCLAW_JSON", Path("/nonexistent/openclaw.json")),
        ):
            from src.reserve_bot import _resolve_owner_ids

            result = _resolve_owner_ids()
            assert result == [999]

    def test_returns_empty_list_when_all_missing(self, tmp_path: Path) -> None:
        """Если нет ни config, ни файла — возвращает пустой список."""
        openclaw = tmp_path / "openclaw.json"
        openclaw.write_text(json.dumps({}), encoding="utf-8")
        fake_config = SimpleNamespace(TELEGRAM_BOT_TOKEN="", OWNER_USER_IDS=[])
        with (
            patch("src.reserve_bot.config", fake_config),
            patch("src.reserve_bot._OPENCLAW_JSON", openclaw),
        ):
            from src.reserve_bot import _resolve_owner_ids

            assert _resolve_owner_ids() == []


# ------------------------------------------------------------------
# _split_text
# ------------------------------------------------------------------


class TestSplitText:
    def test_short_text_unchanged(self) -> None:
        """Текст короче лимита возвращается как один элемент."""
        from src.reserve_bot import _split_text

        result = _split_text("hello", limit=100)
        assert result == ["hello"]

    def test_exactly_limit_unchanged(self) -> None:
        """Текст ровно в лимит — один чанк."""
        from src.reserve_bot import _split_text

        text = "x" * 100
        assert _split_text(text, limit=100) == [text]

    def test_splits_into_chunks(self) -> None:
        """Длинный текст разбивается на ожидаемое число чанков."""
        from src.reserve_bot import _split_text

        text = "a" * 250
        chunks = _split_text(text, limit=100)
        assert len(chunks) == 3
        assert chunks[0] == "a" * 100
        assert chunks[1] == "a" * 100
        assert chunks[2] == "a" * 50

    def test_default_limit_is_4096(self) -> None:
        """Дефолтный лимит — 4096 символов."""
        from src.reserve_bot import _split_text

        text = "b" * 4096
        assert _split_text(text) == [text]
        big = "b" * 4097
        assert len(_split_text(big)) == 2


# ------------------------------------------------------------------
# ReserveBotBridge init / is_configured / is_running
# ------------------------------------------------------------------


class TestReserveBotBridgeInit:
    def _make_bridge(
        self, token: str = "tok", owner_ids: list[int] | None = None
    ) -> "ReserveBotBridge":  # noqa: F821
        """Создаёт ReserveBotBridge с инжектированным токеном и owner_ids."""
        from src.reserve_bot import ReserveBotBridge

        bridge = ReserveBotBridge.__new__(ReserveBotBridge)
        bridge._token = token
        bridge._owner_ids = owner_ids if owner_ids is not None else [12345]
        bridge._client = None
        bridge._running = False
        return bridge

    def test_is_configured_true(self) -> None:
        """is_configured → True при наличии токена и хотя бы одного owner."""
        bridge = self._make_bridge(token="tok", owner_ids=[111])
        assert bridge.is_configured is True

    def test_is_configured_false_no_token(self) -> None:
        """is_configured → False без токена."""
        bridge = self._make_bridge(token="", owner_ids=[111])
        assert bridge.is_configured is False

    def test_is_configured_false_no_owners(self) -> None:
        """is_configured → False без owner_ids."""
        bridge = self._make_bridge(token="tok", owner_ids=[])
        assert bridge.is_configured is False

    def test_is_running_false_initially(self) -> None:
        """is_running → False до вызова start()."""
        bridge = self._make_bridge()
        assert bridge.is_running is False

    def test_is_running_requires_client_and_running_flag(self) -> None:
        """is_running → True только если и _running=True, и _client не None."""
        bridge = self._make_bridge()
        bridge._running = True
        bridge._client = None
        assert bridge.is_running is False
        bridge._client = MagicMock()
        assert bridge.is_running is True


# ------------------------------------------------------------------
# ReserveBotBridge.start / stop
# ------------------------------------------------------------------


class TestReserveBotBridgeStartStop:
    def _make_bridge(
        self, token: str = "tok", owner_ids: list[int] | None = None
    ) -> "ReserveBotBridge":  # noqa: F821
        from src.reserve_bot import ReserveBotBridge

        bridge = ReserveBotBridge.__new__(ReserveBotBridge)
        bridge._token = token
        bridge._owner_ids = owner_ids if owner_ids is not None else [12345]
        bridge._client = None
        bridge._running = False
        return bridge

    @pytest.mark.asyncio
    async def test_start_returns_false_when_not_configured(self) -> None:
        """start() → False если не сконфигурирован."""
        bridge = self._make_bridge(token="", owner_ids=[])
        result = await bridge.start()
        assert result is False
        assert bridge.is_running is False

    @pytest.mark.asyncio
    async def test_start_returns_true_if_already_running(self) -> None:
        """start() → True если бот уже запущен (идемпотентность)."""
        bridge = self._make_bridge()
        bridge._running = True
        bridge._client = MagicMock()
        result = await bridge.start()
        assert result is True

    @pytest.mark.asyncio
    async def test_start_returns_false_on_client_error(self) -> None:
        """start() → False при ошибке создания клиента (не паникует)."""
        bridge = self._make_bridge()
        fake_config = SimpleNamespace(TELEGRAM_API_ID=12345, TELEGRAM_API_HASH="hash")
        mock_client = MagicMock()
        mock_client.start = AsyncMock(side_effect=RuntimeError("connection refused"))
        mock_client.on_message = MagicMock(return_value=lambda f: f)
        mock_pyrogram = MagicMock()
        mock_pyrogram.Client = MagicMock(return_value=mock_client)
        mock_pyrogram.filters = MagicMock()
        mock_pyrogram.filters.command = MagicMock(
            return_value=MagicMock(__and__=MagicMock(return_value=MagicMock()))
        )
        mock_pyrogram.filters.user = MagicMock(return_value=MagicMock())
        mock_pyrogram.filters.text = MagicMock()
        with (
            patch("src.reserve_bot.config", fake_config),
            patch.dict("sys.modules", {"pyrogram": mock_pyrogram, "pyrogram.types": MagicMock()}),
        ):
            result = await bridge.start()
        assert result is False
        assert bridge.is_running is False

    @pytest.mark.asyncio
    async def test_stop_is_noop_when_not_running(self) -> None:
        """stop() не бросает исключение если бот не запущен."""
        bridge = self._make_bridge()
        await bridge.stop()  # не должно кидать
        assert bridge.is_running is False

    @pytest.mark.asyncio
    async def test_stop_cleans_state(self) -> None:
        """stop() сбрасывает _running и _client даже при ошибке клиента."""
        bridge = self._make_bridge()
        mock_client = MagicMock()
        mock_client.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
        bridge._client = mock_client
        bridge._running = True

        await bridge.stop()

        assert bridge._running is False
        assert bridge._client is None


# ------------------------------------------------------------------
# ReserveBotBridge.send_to_owner
# ------------------------------------------------------------------


class TestReserveBotBridgeSendToOwner:
    def _make_running_bridge(self, owner_ids: list[int] | None = None) -> "ReserveBotBridge":  # noqa: F821
        from src.reserve_bot import ReserveBotBridge

        bridge = ReserveBotBridge.__new__(ReserveBotBridge)
        bridge._token = "tok"
        bridge._owner_ids = owner_ids if owner_ids is not None else [111, 222]
        bridge._client = MagicMock()
        bridge._client.send_message = AsyncMock()
        bridge._running = True
        return bridge

    @pytest.mark.asyncio
    async def test_returns_false_when_not_running(self) -> None:
        """send_to_owner → False если бот не запущен."""
        from src.reserve_bot import ReserveBotBridge

        bridge = ReserveBotBridge.__new__(ReserveBotBridge)
        bridge._token = "tok"
        bridge._owner_ids = [111]
        bridge._client = None
        bridge._running = False

        result = await bridge.send_to_owner("hello")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_for_empty_text(self) -> None:
        """send_to_owner → False при пустом тексте."""
        bridge = self._make_running_bridge()
        result = await bridge.send_to_owner("   ")
        assert result is False
        bridge._client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_to_all_owners(self) -> None:
        """send_to_owner рассылает сообщение каждому owner."""
        bridge = self._make_running_bridge(owner_ids=[111, 222])
        result = await bridge.send_to_owner("test message")
        assert result is True
        assert bridge._client.send_message.call_count == 2
        call_uids = {c.args[0] for c in bridge._client.send_message.call_args_list}
        assert call_uids == {111, 222}

    @pytest.mark.asyncio
    async def test_returns_false_when_all_sends_fail(self) -> None:
        """send_to_owner → False если отправка всем упала."""
        bridge = self._make_running_bridge(owner_ids=[111])
        bridge._client.send_message = AsyncMock(side_effect=RuntimeError("network"))
        result = await bridge.send_to_owner("hello")
        assert result is False

    @pytest.mark.asyncio
    async def test_splits_long_message(self) -> None:
        """send_to_owner разбивает сообщение > 4096 символов на чанки."""
        bridge = self._make_running_bridge(owner_ids=[111])
        long_msg = "x" * 4097
        result = await bridge.send_to_owner(long_msg)
        assert result is True
        assert bridge._client.send_message.call_count == 2
