# -*- coding: utf-8 -*-
"""
Тесты для !set — управление настройками из Telegram.

Покрываем:
- Режим 1: !set (без аргументов) → показ всех настроек
- Режим 2: !set <key> → показ одной настройки (алиас и RAW-ключ)
- Режим 3: !set <key> <value> → установка значения
- Алиасы: stream_interval, reactions, weather_city, autodel, language
- Хелперы: _get_set_value, _render_all_settings
- config.update_setting для новых ключей
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.handlers.command_handlers import (
    _AUTODEL_STATE_KEY,
    _SET_ALIASES,
    _SET_FRIENDLY,
    _get_set_value,
    _render_all_settings,
    handle_set,
)
from src.core.exceptions import UserInputError


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_message(text: str, chat_id: int = 100) -> SimpleNamespace:
    """Минимальное fake-сообщение для handler."""
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=42, username="pablito"),
        reply=AsyncMock(),
    )


def _make_bot(
    *,
    runtime_state: dict | None = None,
    translator_profile: dict | None = None,
) -> SimpleNamespace:
    """Fake-бот с минимальным набором атрибутов."""
    bot = MagicMock()
    bot._runtime_state = runtime_state if runtime_state is not None else {}
    bot._get_command_args = MagicMock(side_effect=lambda msg: " ".join(msg.text.split()[1:]))

    if translator_profile is not None:
        bot.get_translator_runtime_profile = MagicMock(return_value=translator_profile)
        bot.update_translator_runtime_profile = MagicMock(return_value=translator_profile)
    else:
        # По умолчанию нет переводчика
        del bot.get_translator_runtime_profile
        del bot.update_translator_runtime_profile

    return bot


# ---------------------------------------------------------------------------
# Юнит-тесты хелперов
# ---------------------------------------------------------------------------


class TestGetSetValue:
    """_get_set_value возвращает корректные значения по алиасу."""

    def test_autodel_no_default(self):
        bot = _make_bot()
        result = _get_set_value(bot, "autodel")
        assert "0" in result  # "0 (выключен)"

    def test_autodel_with_default(self):
        bot = _make_bot(runtime_state={_AUTODEL_STATE_KEY: {"_default": 30.0}})
        result = _get_set_value(bot, "autodel")
        assert "30" in result

    def test_language_without_translator(self):
        bot = _make_bot(translator_profile=None)
        # Нет метода get_translator_runtime_profile — должен вернуть fallback "es-ru"
        result = _get_set_value(bot, "language")
        assert result == "es-ru"

    def test_language_with_translator(self):
        bot = _make_bot(translator_profile={"language_pair": "en-ru"})
        result = _get_set_value(bot, "language")
        assert result == "en-ru"

    def test_stream_interval_returns_config_value(self):
        from src.config import Config
        original = Config.TELEGRAM_STREAM_UPDATE_INTERVAL_SEC
        Config.TELEGRAM_STREAM_UPDATE_INTERVAL_SEC = 3.5
        try:
            bot = _make_bot()
            result = _get_set_value(bot, "stream_interval")
            assert "3.5" in result
        finally:
            Config.TELEGRAM_STREAM_UPDATE_INTERVAL_SEC = original

    def test_reactions_returns_config_value(self):
        from src.config import Config
        original = Config.TELEGRAM_REACTIONS_ENABLED
        Config.TELEGRAM_REACTIONS_ENABLED = False
        try:
            bot = _make_bot()
            result = _get_set_value(bot, "reactions")
            assert result == "False"
        finally:
            Config.TELEGRAM_REACTIONS_ENABLED = original

    def test_weather_city_returns_config_value(self):
        from src.config import Config
        original = Config.DEFAULT_WEATHER_CITY
        Config.DEFAULT_WEATHER_CITY = "Moscow"
        try:
            bot = _make_bot()
            result = _get_set_value(bot, "weather_city")
            assert result == "Moscow"
        finally:
            Config.DEFAULT_WEATHER_CITY = original


class TestRenderAllSettings:
    """_render_all_settings содержит все ключевые части."""

    def test_contains_all_aliases(self):
        bot = _make_bot()
        text = _render_all_settings(bot)
        for alias in _SET_FRIENDLY:
            assert alias in text

    def test_contains_usage_hint(self):
        bot = _make_bot()
        text = _render_all_settings(bot)
        assert "!set" in text
        assert "key" in text


# ---------------------------------------------------------------------------
# Тесты алиасов и структур данных
# ---------------------------------------------------------------------------


class TestSetAliases:
    """Проверяем что алиасы корректно определены."""

    def test_all_friendly_keys_in_aliases(self):
        # Все friendly-ключи должны быть в aliases или обрабатываться отдельно
        special = {"autodel", "language"}
        for alias in _SET_FRIENDLY:
            if alias not in special:
                assert alias in _SET_ALIASES, f"Алиас '{alias}' не найден в _SET_ALIASES"

    def test_stream_interval_alias(self):
        assert _SET_ALIASES["stream_interval"] == "TELEGRAM_STREAM_UPDATE_INTERVAL_SEC"

    def test_reactions_alias(self):
        assert _SET_ALIASES["reactions"] == "TELEGRAM_REACTIONS_ENABLED"

    def test_weather_city_alias(self):
        assert _SET_ALIASES["weather_city"] == "DEFAULT_WEATHER_CITY"


# ---------------------------------------------------------------------------
# Интеграционные тесты handle_set
# ---------------------------------------------------------------------------


class TestHandleSetNoArgs:
    """!set без аргументов → показ всех настроек."""

    @pytest.mark.asyncio
    async def test_no_args_replies_with_all_settings(self):
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="")
        msg = _make_message("!set")

        await handle_set(bot, msg)

        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "stream_interval" in reply_text
        assert "reactions" in reply_text
        assert "weather_city" in reply_text
        assert "autodel" in reply_text
        assert "language" in reply_text


class TestHandleSetOneArg:
    """!set <key> → показ значения одной настройки."""

    @pytest.mark.asyncio
    async def test_show_alias_stream_interval(self):
        from src.config import Config
        Config.TELEGRAM_STREAM_UPDATE_INTERVAL_SEC = 2.0
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="stream_interval")
        msg = _make_message("!set stream_interval")

        await handle_set(bot, msg)

        msg.reply.assert_called_once()
        assert "stream_interval" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_show_alias_reactions(self):
        from src.config import Config
        Config.TELEGRAM_REACTIONS_ENABLED = True
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="reactions")
        msg = _make_message("!set reactions")

        await handle_set(bot, msg)

        msg.reply.assert_called_once()
        assert "reactions" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_show_alias_weather_city(self):
        from src.config import Config
        Config.DEFAULT_WEATHER_CITY = "Barcelona"
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="weather_city")
        msg = _make_message("!set weather_city")

        await handle_set(bot, msg)

        msg.reply.assert_called_once()
        assert "weather_city" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_show_alias_autodel_no_default(self):
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="autodel")
        msg = _make_message("!set autodel")

        await handle_set(bot, msg)

        msg.reply.assert_called_once()
        assert "autodel" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_show_alias_language(self):
        bot = _make_bot(translator_profile={"language_pair": "es-ru"})
        bot._get_command_args = MagicMock(return_value="language")
        msg = _make_message("!set language")

        await handle_set(bot, msg)

        msg.reply.assert_called_once()
        assert "language" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_show_raw_config_key(self):
        from src.config import Config
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="DEFAULT_WEATHER_CITY")
        msg = _make_message("!set DEFAULT_WEATHER_CITY")

        await handle_set(bot, msg)

        msg.reply.assert_called_once()
        assert "DEFAULT_WEATHER_CITY" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_unknown_key_raises_error(self):
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="nonexistent_key_xyz")
        msg = _make_message("!set nonexistent_key_xyz")

        with pytest.raises(UserInputError):
            await handle_set(bot, msg)


class TestHandleSetTwoArgs:
    """!set <key> <value> → установка значения."""

    @pytest.mark.asyncio
    async def test_set_stream_interval(self):
        from src.config import Config
        original = Config.TELEGRAM_STREAM_UPDATE_INTERVAL_SEC
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="stream_interval 3.0")
        msg = _make_message("!set stream_interval 3.0")

        with patch.object(Config, "update_setting", return_value=True) as mock_upd:
            await handle_set(bot, msg)
            mock_upd.assert_called_once_with("TELEGRAM_STREAM_UPDATE_INTERVAL_SEC", "3.0")

        msg.reply.assert_called_once()
        assert "✅" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_set_reactions_on(self):
        from src.config import Config
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="reactions on")
        msg = _make_message("!set reactions on")

        with patch.object(Config, "update_setting", return_value=True) as mock_upd:
            await handle_set(bot, msg)
            mock_upd.assert_called_once_with("TELEGRAM_REACTIONS_ENABLED", "on")

        assert "✅" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_set_reactions_off(self):
        from src.config import Config
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="reactions off")
        msg = _make_message("!set reactions off")

        with patch.object(Config, "update_setting", return_value=True) as mock_upd:
            await handle_set(bot, msg)
            mock_upd.assert_called_once_with("TELEGRAM_REACTIONS_ENABLED", "off")

    @pytest.mark.asyncio
    async def test_set_weather_city(self):
        from src.config import Config
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="weather_city Madrid")
        msg = _make_message("!set weather_city Madrid")

        with patch.object(Config, "update_setting", return_value=True) as mock_upd:
            await handle_set(bot, msg)
            mock_upd.assert_called_once_with("DEFAULT_WEATHER_CITY", "Madrid")

        assert "✅" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_set_autodel_enable(self):
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="autodel 60")
        msg = _make_message("!set autodel 60")

        await handle_set(bot, msg)

        settings = bot._runtime_state[_AUTODEL_STATE_KEY]
        assert settings["_default"] == 60.0
        assert "60" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_set_autodel_disable(self):
        bot = _make_bot(runtime_state={_AUTODEL_STATE_KEY: {"_default": 30.0}})
        bot._get_command_args = MagicMock(return_value="autodel 0")
        msg = _make_message("!set autodel 0")

        await handle_set(bot, msg)

        settings = bot._runtime_state.get(_AUTODEL_STATE_KEY, {})
        assert "_default" not in settings
        assert "выключено" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_set_autodel_invalid_value(self):
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="autodel abc")
        msg = _make_message("!set autodel abc")

        with pytest.raises(UserInputError):
            await handle_set(bot, msg)

    @pytest.mark.asyncio
    async def test_set_language_valid(self):
        profile = {"language_pair": "en-ru"}
        bot = _make_bot(translator_profile=profile)
        bot._get_command_args = MagicMock(return_value="language en-ru")
        msg = _make_message("!set language en-ru")

        with patch(
            "src.handlers.command_handlers.ALLOWED_LANGUAGE_PAIRS",
            {"es-ru", "en-ru", "ru-en"},
            create=True,
        ):
            await handle_set(bot, msg)

        msg.reply.assert_called_once()
        assert "✅" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_set_language_invalid(self):
        profile = {"language_pair": "es-ru"}
        bot = _make_bot(translator_profile=profile)
        bot._get_command_args = MagicMock(return_value="language xx-yy")
        msg = _make_message("!set language xx-yy")

        with patch(
            "src.core.translator_runtime_profile.ALLOWED_LANGUAGE_PAIRS",
            {"es-ru", "en-ru"},
        ):
            with pytest.raises(UserInputError) as exc_info:
                await handle_set(bot, msg)

        assert "xx-yy" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_set_language_no_translator(self):
        bot = _make_bot(translator_profile=None)
        bot._get_command_args = MagicMock(return_value="language es-ru")
        msg = _make_message("!set language es-ru")

        with pytest.raises(UserInputError) as exc_info:
            await handle_set(bot, msg)

        assert "не инициализирован" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_set_raw_key(self):
        from src.config import Config
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="DEFAULT_WEATHER_CITY Tokyo")
        msg = _make_message("!set DEFAULT_WEATHER_CITY Tokyo")

        with patch.object(Config, "update_setting", return_value=True) as mock_upd:
            await handle_set(bot, msg)
            mock_upd.assert_called_once_with("DEFAULT_WEATHER_CITY", "Tokyo")

        assert "✅" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_set_update_fails_returns_error(self):
        from src.config import Config
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="weather_city Tokyo")
        msg = _make_message("!set weather_city Tokyo")

        with patch.object(Config, "update_setting", return_value=False):
            await handle_set(bot, msg)

        assert "❌" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_set_scheduler_enabled_syncs_runtime(self):
        from src.config import Config
        bot = _make_bot()
        bot._sync_scheduler_runtime = MagicMock()
        bot._get_command_args = MagicMock(return_value="SCHEDULER_ENABLED 1")
        msg = _make_message("!set SCHEDULER_ENABLED 1")

        with patch.object(Config, "update_setting", return_value=True):
            with patch.object(Config, "SCHEDULER_ENABLED", True):
                await handle_set(bot, msg)

        bot._sync_scheduler_runtime.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "Scheduler" in reply_text


# ---------------------------------------------------------------------------
# Тесты config.update_setting для новых ключей
# ---------------------------------------------------------------------------


class TestConfigUpdateSetting:
    """config.update_setting корректно обновляет новые поля."""

    def setup_method(self):
        from src.config import Config
        # Сохраняем оригинальные значения
        self._orig_interval = Config.TELEGRAM_STREAM_UPDATE_INTERVAL_SEC
        self._orig_reactions = Config.TELEGRAM_REACTIONS_ENABLED
        self._orig_city = Config.DEFAULT_WEATHER_CITY

    def teardown_method(self):
        from src.config import Config
        Config.TELEGRAM_STREAM_UPDATE_INTERVAL_SEC = self._orig_interval
        Config.TELEGRAM_REACTIONS_ENABLED = self._orig_reactions
        Config.DEFAULT_WEATHER_CITY = self._orig_city

    def test_update_stream_interval(self, tmp_path):
        from src.config import Config
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with patch.object(Config, "BASE_DIR", tmp_path):
            result = Config.update_setting("TELEGRAM_STREAM_UPDATE_INTERVAL_SEC", "5.0")
        assert result is True
        assert Config.TELEGRAM_STREAM_UPDATE_INTERVAL_SEC == 5.0

    def test_update_stream_interval_minimum_clamped(self, tmp_path):
        from src.config import Config
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("TELEGRAM_STREAM_UPDATE_INTERVAL_SEC", "0.1")
        # Минимум 0.5
        assert Config.TELEGRAM_STREAM_UPDATE_INTERVAL_SEC == 0.5

    def test_update_reactions_on(self, tmp_path):
        from src.config import Config
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("TELEGRAM_REACTIONS_ENABLED", "on")
        assert Config.TELEGRAM_REACTIONS_ENABLED is True

    def test_update_reactions_off(self, tmp_path):
        from src.config import Config
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("TELEGRAM_REACTIONS_ENABLED", "off")
        assert Config.TELEGRAM_REACTIONS_ENABLED is False

    def test_update_reactions_1_true(self, tmp_path):
        from src.config import Config
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("TELEGRAM_REACTIONS_ENABLED", "1")
        assert Config.TELEGRAM_REACTIONS_ENABLED is True

    def test_update_reactions_0_false(self, tmp_path):
        from src.config import Config
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("TELEGRAM_REACTIONS_ENABLED", "0")
        assert Config.TELEGRAM_REACTIONS_ENABLED is False

    def test_update_weather_city(self, tmp_path):
        from src.config import Config
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("DEFAULT_WEATHER_CITY", "Berlin")
        assert Config.DEFAULT_WEATHER_CITY == "Berlin"

    def test_update_weather_city_strips_whitespace(self, tmp_path):
        from src.config import Config
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("DEFAULT_WEATHER_CITY", "  Paris  ")
        assert Config.DEFAULT_WEATHER_CITY == "Paris"

    def test_update_setting_persists_to_env(self, tmp_path):
        from src.config import Config
        env_file = tmp_path / ".env"
        env_file.write_text("DEFAULT_WEATHER_CITY=Barcelona\n")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("DEFAULT_WEATHER_CITY", "Lisbon")
        content = env_file.read_text()
        assert "DEFAULT_WEATHER_CITY=Lisbon" in content

    def test_update_setting_appends_new_key(self, tmp_path):
        from src.config import Config
        env_file = tmp_path / ".env"
        env_file.write_text("OTHER=value\n")
        with patch.object(Config, "BASE_DIR", tmp_path):
            Config.update_setting("DEFAULT_WEATHER_CITY", "Rome")
        content = env_file.read_text()
        assert "DEFAULT_WEATHER_CITY=Rome" in content
