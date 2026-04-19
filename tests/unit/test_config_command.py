# -*- coding: utf-8 -*-
"""
Тесты для !config — просмотр и редактирование технических настроек Краба.

Покрываем:
- Режим 1: !config (без аргументов) → показ всех настроек
- Режим 2: !config <KEY> → показ одной настройки
- Режим 3: !config <KEY> <value> → установка значения
- Хелперы: _render_config_value, _render_config_all, _CONFIG_KEY_DESC
- Обработка ошибок: несуществующий ключ, не найдена настройка
- update_setting: успех и неудача
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _CONFIG_GROUPS,
    _CONFIG_KEY_DESC,
    _render_config_all,
    _render_config_value,
    handle_config,
)

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


def _make_bot(args: str = "") -> MagicMock:
    """Fake-бот для тестов handle_config."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=args)
    return bot


# ---------------------------------------------------------------------------
# Тесты хелпера _render_config_value
# ---------------------------------------------------------------------------


class TestRenderConfigValue:
    """_render_config_value форматирует значения корректно."""

    def test_string_value(self):
        # MODEL — строка
        result = _render_config_value("MODEL")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_bool_value(self):
        result = _render_config_value("FORCE_CLOUD")
        assert result in ("True", "False")

    def test_int_value(self):
        result = _render_config_value("MAX_RAM_GB")
        assert result.isdigit()

    def test_float_value(self):
        result = _render_config_value("VOICE_REPLY_SPEED")
        assert float(result) > 0

    def test_list_value_nonempty(self):
        from src.config import config

        # Временно подставляем список
        with patch.object(config, "TRIGGER_PREFIXES", ["!краб", "@краб"], create=False):
            result = _render_config_value("TRIGGER_PREFIXES")
        assert "," in result or len(result) > 0

    def test_list_value_empty(self):
        from src.config import config

        with patch.object(config, "TRIGGER_PREFIXES", [], create=False):
            result = _render_config_value("TRIGGER_PREFIXES")
        assert result == "(пусто)"

    def test_frozenset_value(self):
        from src.config import config

        with patch.object(config, "MANUAL_BLOCKLIST", frozenset(["userA", "userB"]), create=False):
            result = _render_config_value("MANUAL_BLOCKLIST")
        assert "userA" in result or "userB" in result

    def test_unknown_key_returns_dash(self):
        result = _render_config_value("NONEXISTENT_SETTING_XYZ_999")
        assert result == "—"

    def test_none_value_returns_dash(self):
        from src.config import config

        with patch.object(config, "GEMINI_API_KEY_FREE", None, create=False):
            result = _render_config_value("GEMINI_API_KEY_FREE")
        assert result == "—"


# ---------------------------------------------------------------------------
# Тесты _CONFIG_GROUPS и _CONFIG_KEY_DESC
# ---------------------------------------------------------------------------


class TestConfigGroups:
    """Структура групп и индекс ключей."""

    def test_groups_nonempty(self):
        assert len(_CONFIG_GROUPS) > 0

    def test_every_group_has_name_and_list(self):
        for group_name, keys in _CONFIG_GROUPS:
            assert isinstance(group_name, str) and group_name
            assert isinstance(keys, list) and len(keys) > 0

    def test_key_desc_index_populated(self):
        assert len(_CONFIG_KEY_DESC) > 0

    def test_known_keys_in_desc(self):
        """Критичные ключи должны быть в индексе."""
        for key in ("MODEL", "FORCE_CLOUD", "SCHEDULER_ENABLED", "VOICE_MODE_DEFAULT"):
            assert key in _CONFIG_KEY_DESC, f"{key} отсутствует в _CONFIG_KEY_DESC"

    def test_all_group_keys_in_desc(self):
        """Все ключи из групп должны попасть в _CONFIG_KEY_DESC."""
        for _, keys in _CONFIG_GROUPS:
            for key, _ in keys:
                assert key in _CONFIG_KEY_DESC, f"{key} не в _CONFIG_KEY_DESC"

    def test_no_duplicate_keys(self):
        """В группах не должно быть дублирующихся ключей."""
        all_keys = [k for _, keys in _CONFIG_GROUPS for k, _ in keys]
        assert len(all_keys) == len(set(all_keys)), "Дублирующийся ключ в _CONFIG_GROUPS"


# ---------------------------------------------------------------------------
# Тесты _render_config_all
# ---------------------------------------------------------------------------


class TestRenderConfigAll:
    """_render_config_all форматирует полный вывод."""

    def test_returns_string(self):
        result = _render_config_all()
        assert isinstance(result, str)

    def test_contains_header(self):
        result = _render_config_all()
        assert "Конфигурация Краба" in result

    def test_contains_group_names(self):
        result = _render_config_all()
        for group_name, _ in _CONFIG_GROUPS:
            assert group_name in result, f"Группа '{group_name}' не найдена в выводе"

    def test_contains_usage_hint(self):
        result = _render_config_all()
        assert "!config" in result

    def test_contains_key_value_pairs(self):
        result = _render_config_all()
        assert "MODEL" in result
        assert "FORCE_CLOUD" in result

    def test_all_group_keys_present(self):
        result = _render_config_all()
        for _, keys in _CONFIG_GROUPS:
            for key, _ in keys:
                assert key in result, f"Ключ {key} не найден в выводе _render_config_all"


# ---------------------------------------------------------------------------
# Тесты handle_config — Режим 1: !config (без аргументов)
# ---------------------------------------------------------------------------


class TestHandleConfigNoArgs:
    """!config без аргументов показывает все настройки."""

    @pytest.mark.asyncio
    async def test_reply_called(self):
        bot = _make_bot(args="")
        msg = _make_message("!config")
        await handle_config(bot, msg)
        msg.reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_reply_contains_header(self):
        bot = _make_bot(args="")
        msg = _make_message("!config")
        await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "Конфигурация Краба" in text

    @pytest.mark.asyncio
    async def test_reply_contains_groups(self):
        bot = _make_bot(args="")
        msg = _make_message("!config")
        await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        for group_name, _ in _CONFIG_GROUPS:
            assert group_name in text

    @pytest.mark.asyncio
    async def test_reply_contains_usage_hint(self):
        bot = _make_bot(args="")
        msg = _make_message("!config")
        await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "!config" in text


# ---------------------------------------------------------------------------
# Тесты handle_config — Режим 2: !config <KEY>
# ---------------------------------------------------------------------------


class TestHandleConfigGetKey:
    """!config <KEY> показывает одну настройку."""

    @pytest.mark.asyncio
    async def test_known_key_model(self):
        bot = _make_bot(args="MODEL")
        msg = _make_message("!config MODEL")
        await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "MODEL" in text

    @pytest.mark.asyncio
    async def test_known_key_lowercase(self):
        """Ключ принимается в любом регистре."""
        bot = _make_bot(args="model")
        msg = _make_message("!config model")
        await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "MODEL" in text

    @pytest.mark.asyncio
    async def test_known_key_force_cloud(self):
        bot = _make_bot(args="FORCE_CLOUD")
        msg = _make_message("!config FORCE_CLOUD")
        await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "FORCE_CLOUD" in text

    @pytest.mark.asyncio
    async def test_known_key_scheduler_enabled(self):
        bot = _make_bot(args="SCHEDULER_ENABLED")
        msg = _make_message("!config SCHEDULER_ENABLED")
        await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "SCHEDULER_ENABLED" in text

    @pytest.mark.asyncio
    async def test_known_key_with_description(self):
        """Для ключа из _CONFIG_KEY_DESC добавляется описание."""
        bot = _make_bot(args="VOICE_MODE_DEFAULT")
        msg = _make_message("!config VOICE_MODE_DEFAULT")
        await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "VOICE_MODE_DEFAULT" in text
        # Описание из _CONFIG_KEY_DESC должно присутствовать
        assert _CONFIG_KEY_DESC.get("VOICE_MODE_DEFAULT", "") in text

    @pytest.mark.asyncio
    async def test_unknown_key_raises_user_input_error(self):
        bot = _make_bot(args="NONEXISTENT_KEY_XYZ")
        msg = _make_message("!config NONEXISTENT_KEY_XYZ")
        with pytest.raises(UserInputError):
            await handle_config(bot, msg)

    @pytest.mark.asyncio
    async def test_unknown_key_error_message_contains_hint(self):
        bot = _make_bot(args="TOTALLY_UNKNOWN")
        msg = _make_message("!config TOTALLY_UNKNOWN")
        with pytest.raises(UserInputError) as exc_info:
            await handle_config(bot, msg)
        assert "!config" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_list_key_trigger_prefixes(self):
        bot = _make_bot(args="TRIGGER_PREFIXES")
        msg = _make_message("!config TRIGGER_PREFIXES")
        await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "TRIGGER_PREFIXES" in text

    @pytest.mark.asyncio
    async def test_key_log_level(self):
        bot = _make_bot(args="LOG_LEVEL")
        msg = _make_message("!config LOG_LEVEL")
        await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "LOG_LEVEL" in text

    @pytest.mark.asyncio
    async def test_key_max_ram_gb(self):
        bot = _make_bot(args="MAX_RAM_GB")
        msg = _make_message("!config MAX_RAM_GB")
        await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "MAX_RAM_GB" in text


# ---------------------------------------------------------------------------
# Тесты handle_config — Режим 3: !config <KEY> <value>
# ---------------------------------------------------------------------------


class TestHandleConfigSetKey:
    """!config <KEY> <value> устанавливает значение через config.update_setting."""

    @pytest.mark.asyncio
    async def test_set_known_key_success(self):
        bot = _make_bot(args="DEFAULT_WEATHER_CITY Madrid")
        msg = _make_message("!config DEFAULT_WEATHER_CITY Madrid")
        with patch("src.handlers.command_handlers.config") as mock_cfg:
            mock_cfg.update_setting.return_value = True
            # Нужен hasattr и getattr для render
            mock_cfg.__class__ = type("Config", (), {})
            type(mock_cfg).DEFAULT_WEATHER_CITY = property(lambda self: "Madrid")
            mock_cfg.update_setting = MagicMock(return_value=True)
            # Упростим: патчим только update_setting и getattr
            from src.config import Config

            with patch.object(Config, "update_setting", return_value=True):
                await handle_config(bot, msg)
        msg.reply.assert_called_once()
        text = msg.reply.call_args[0][0]
        assert "✅" in text
        assert "DEFAULT_WEATHER_CITY" in text

    @pytest.mark.asyncio
    async def test_set_known_key_failure(self):
        bot = _make_bot(args="DEFAULT_WEATHER_CITY Madrid")
        msg = _make_message("!config DEFAULT_WEATHER_CITY Madrid")
        from src.config import Config

        with patch.object(Config, "update_setting", return_value=False):
            await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "❌" in text

    @pytest.mark.asyncio
    async def test_set_unknown_key_raises(self):
        bot = _make_bot(args="UNKNOWN_KEY_789 somevalue")
        msg = _make_message("!config UNKNOWN_KEY_789 somevalue")
        with pytest.raises(UserInputError):
            await handle_config(bot, msg)

    @pytest.mark.asyncio
    async def test_set_model(self):
        bot = _make_bot(args="MODEL google/gemini-2.5-pro")
        msg = _make_message("!config MODEL google/gemini-2.5-pro")
        from src.config import Config

        with patch.object(Config, "update_setting", return_value=True):
            await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "✅" in text
        assert "MODEL" in text

    @pytest.mark.asyncio
    async def test_set_force_cloud(self):
        bot = _make_bot(args="FORCE_CLOUD 1")
        msg = _make_message("!config FORCE_CLOUD 1")
        from src.config import Config

        with patch.object(Config, "update_setting", return_value=True):
            await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "FORCE_CLOUD" in text

    @pytest.mark.asyncio
    async def test_set_scheduler_enabled(self):
        bot = _make_bot(args="SCHEDULER_ENABLED 0")
        msg = _make_message("!config SCHEDULER_ENABLED 0")
        from src.config import Config

        with patch.object(Config, "update_setting", return_value=True):
            await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "SCHEDULER_ENABLED" in text

    @pytest.mark.asyncio
    async def test_set_lowercase_key(self):
        """Ключ в нижнем регистре нормализуется в upper."""
        bot = _make_bot(args="log_level DEBUG")
        msg = _make_message("!config log_level DEBUG")
        from src.config import Config

        with patch.object(Config, "update_setting", return_value=True):
            await handle_config(bot, msg)
        # Должен ответить успехом или ошибкой (LOG_LEVEL есть в Config)
        msg.reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_voice_reply_speed(self):
        bot = _make_bot(args="VOICE_REPLY_SPEED 1.8")
        msg = _make_message("!config VOICE_REPLY_SPEED 1.8")
        from src.config import Config

        with patch.object(Config, "update_setting", return_value=True):
            await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "VOICE_REPLY_SPEED" in text

    @pytest.mark.asyncio
    async def test_set_max_ram_gb(self):
        bot = _make_bot(args="MAX_RAM_GB 32")
        msg = _make_message("!config MAX_RAM_GB 32")
        from src.config import Config

        with patch.object(Config, "update_setting", return_value=True):
            await handle_config(bot, msg)
        text = msg.reply.call_args[0][0]
        assert "MAX_RAM_GB" in text


# ---------------------------------------------------------------------------
# Тесты без _get_command_args (fallback на пустую строку)
# ---------------------------------------------------------------------------


class TestHandleConfigFallback:
    """handle_config работает если у bot нет _get_command_args."""

    @pytest.mark.asyncio
    async def test_no_get_command_args_attr(self):
        bot = MagicMock(spec=[])  # нет _get_command_args
        msg = _make_message("!config")
        await handle_config(bot, msg)
        msg.reply.assert_called_once()
        text = msg.reply.call_args[0][0]
        assert "Конфигурация Краба" in text


# ---------------------------------------------------------------------------
# Тесты полноты конфигурации
# ---------------------------------------------------------------------------


class TestConfigCompleteness:
    """Все ключи из _CONFIG_GROUPS присутствуют в реальном Config."""

    def test_all_group_keys_exist_in_config(self):
        from src.config import Config

        missing = []
        for _, keys in _CONFIG_GROUPS:
            for key, _ in keys:
                if not hasattr(Config, key):
                    missing.append(key)
        assert not missing, f"Ключи не найдены в Config: {missing}"
