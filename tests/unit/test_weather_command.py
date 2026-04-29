# -*- coding: utf-8 -*-
"""
Юнит-тесты для !weather command handler.

Покрываем:
  - handle_weather: без аргументов берёт DEFAULT_WEATHER_CITY
  - handle_weather <город>: передаёт указанный город в промпт
  - disable_tools=False (LLM использует web_search)
  - изолированная сессия weather_{chat_id}
  - пустой ответ AI обрабатывается корректно
  - streaming-чанки склеиваются
  - ошибки openclaw_client обрабатываются gracefully
  - handle_weather экспортируется из handlers
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.handlers.command_handlers import handle_weather

# ---------------------------------------------------------------------------
# Env isolation — пинним DEFAULT_WEATHER_CITY=Barcelona, чтобы тесты не
# зависели от shell-окружения разработчика (где может быть Tokyo и т.п.)
# и от содержимого .env файла, который load_dotenv подтягивает.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _pin_default_weather_city(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config singleton уже инстанцирован с env на момент первого импорта,
    поэтому помимо env-пина патчим атрибут на singleton'е, который видит
    handler."""
    from src.handlers import command_handlers as _ch

    monkeypatch.setenv("DEFAULT_WEATHER_CITY", "Barcelona")
    monkeypatch.setattr(_ch.config, "DEFAULT_WEATHER_CITY", "Barcelona")


@pytest.fixture(autouse=True)
def _stub_fetch_wttr(monkeypatch: pytest.MonkeyPatch) -> None:
    """wttr.in fast-path возвращает результат при реальной сети — тесты
    ожидают fallback через openclaw_client. Пиним None по умолчанию."""
    from unittest.mock import AsyncMock as _AsyncMock

    monkeypatch.setattr(
        "src.handlers.command_handlers._fetch_wttr",
        _AsyncMock(return_value=None),
    )


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_message(
    command_args: str = "",
    chat_id: int = 42000,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    """Возвращает (bot, message) stubs."""
    edit_mock = AsyncMock()
    sent_msg = SimpleNamespace(edit=edit_mock)

    msg = SimpleNamespace(
        text=f"!weather {command_args}".strip(),
        reply=AsyncMock(return_value=sent_msg),
        chat=SimpleNamespace(id=chat_id),
    )

    bot = SimpleNamespace(_get_command_args=lambda _m: command_args)
    return bot, msg


# ===========================================================================
# Город по умолчанию
# ===========================================================================


class TestHandleWeatherDefaultCity:
    """!weather без аргументов использует DEFAULT_WEATHER_CITY."""

    @pytest.mark.asyncio
    async def test_без_аргументов_берёт_default_city(self) -> None:
        """Нет аргументов → DEFAULT_WEATHER_CITY попадает в промпт."""
        bot, msg = _make_message(command_args="")

        captured_prompt: list[str] = []

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            captured_prompt.append(message)
            yield "Солнечно, 22°C"

        with (
            patch(
                "src.handlers.command_handlers.openclaw_client.send_message_stream",
                side_effect=fake_stream,
            ),
            patch(
                "src.handlers.command_handlers.config.DEFAULT_WEATHER_CITY",
                "Barcelona",
            ),
        ):
            await handle_weather(bot, msg)

        assert len(captured_prompt) == 1
        assert "Barcelona" in captured_prompt[0]

    @pytest.mark.asyncio
    async def test_статусное_сообщение_содержит_default_city(self) -> None:
        """Статусное сообщение 'Смотрю погоду...' содержит дефолтный город."""
        bot, msg = _make_message(command_args="")

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            yield "Пасмурно, 18°C"

        with (
            patch(
                "src.handlers.command_handlers.openclaw_client.send_message_stream",
                side_effect=fake_stream,
            ),
            patch(
                "src.handlers.command_handlers.config.DEFAULT_WEATHER_CITY",
                "TestCity",
            ),
        ):
            await handle_weather(bot, msg)

        # Первый вызов reply() — статусное сообщение
        first_reply_text = msg.reply.call_args_list[0][0][0]
        assert "TestCity" in first_reply_text


# ===========================================================================
# Указанный город
# ===========================================================================


class TestHandleWeatherCustomCity:
    """!weather <город> передаёт указанный город."""

    @pytest.mark.asyncio
    async def test_указанный_город_в_промпте(self) -> None:
        """Аргумент 'Moscow' → попадает в prompt."""
        bot, msg = _make_message(command_args="Moscow")

        captured: list[str] = []

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            captured.append(message)
            yield "-5°C, снег"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_weather(bot, msg)

        assert "Moscow" in captured[0]

    @pytest.mark.asyncio
    async def test_указанный_город_не_равен_дефолту(self) -> None:
        """Когда указан город, дефолт не используется."""
        bot, msg = _make_message(command_args="Tokyo")

        captured: list[str] = []

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            captured.append(message)
            yield "28°C, влажно"

        with (
            patch(
                "src.handlers.command_handlers.openclaw_client.send_message_stream",
                side_effect=fake_stream,
            ),
            patch(
                "src.handlers.command_handlers.config.DEFAULT_WEATHER_CITY",
                "Barcelona",
            ),
        ):
            await handle_weather(bot, msg)

        assert "Tokyo" in captured[0]
        assert "Barcelona" not in captured[0]

    @pytest.mark.asyncio
    async def test_многословный_город_передаётся_полностью(self) -> None:
        """!weather New York → 'New York' в промпте."""
        bot, msg = _make_message(command_args="New York")

        captured: list[str] = []

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            captured.append(message)
            yield "15°C, облачно"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_weather(bot, msg)

        assert "New York" in captured[0]


# ===========================================================================
# Сессия и параметры вызова
# ===========================================================================


class TestHandleWeatherSession:
    """Проверка session_id и disable_tools."""

    @pytest.mark.asyncio
    async def test_session_id_изолирован(self) -> None:
        """chat_id передаётся как 'weather_{chat_id}'."""
        bot, msg = _make_message(command_args="Paris", chat_id=77777)

        captured_chat_id: list[str] = []

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            captured_chat_id.append(chat_id)
            yield "18°C"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_weather(bot, msg)

        assert captured_chat_id[0] == "weather_77777"

    @pytest.mark.asyncio
    async def test_disable_tools_false(self) -> None:
        """!weather вызывает send_message_stream с disable_tools=False."""
        bot, msg = _make_message(command_args="Berlin")

        captured_kwargs: list[dict] = []

        async def fake_stream(message, chat_id, disable_tools=True, **_kw):
            captured_kwargs.append({"disable_tools": disable_tools})
            yield "5°C, пасмурно"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_weather(bot, msg)

        assert captured_kwargs[0]["disable_tools"] is False

    @pytest.mark.asyncio
    async def test_разные_чаты_разные_сессии(self) -> None:
        """Два разных chat_id → разные session_id."""
        sessions: list[str] = []

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            sessions.append(chat_id)
            yield "Ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            bot1, msg1 = _make_message(command_args="London", chat_id=111)
            await handle_weather(bot1, msg1)

            bot2, msg2 = _make_message(command_args="London", chat_id=222)
            await handle_weather(bot2, msg2)

        assert sessions[0] == "weather_111"
        assert sessions[1] == "weather_222"
        assert sessions[0] != sessions[1]


# ===========================================================================
# Обработка ответа AI
# ===========================================================================


class TestHandleWeatherResponse:
    """Обработка различных вариантов ответа от AI."""

    @pytest.mark.asyncio
    async def test_успешный_ответ_редактирует_сообщение(self) -> None:
        """Ответ AI → edit() вызывается с контентом."""
        bot, msg = _make_message(command_args="Rome")

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            yield "25°C, солнечно, без осадков"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_weather(bot, msg)

        sent = msg.reply.return_value
        sent.edit.assert_called_once()
        call_text = sent.edit.call_args[0][0]
        assert "25°C" in call_text

    @pytest.mark.asyncio
    async def test_пустой_ответ_ai_сообщение_об_ошибке(self) -> None:
        """Если AI вернул пустую строку → сообщение об ошибке."""
        bot, msg = _make_message(command_args="Cairo")

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            yield ""

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_weather(bot, msg)

        sent = msg.reply.return_value
        sent.edit.assert_called_once()
        call_text = sent.edit.call_args[0][0].lower()
        assert "не удалось" in call_text or "❌" in sent.edit.call_args[0][0]

    @pytest.mark.asyncio
    async def test_только_пробелы_в_ответе_тоже_ошибка(self) -> None:
        """Whitespace-только ответ → сообщение об ошибке."""
        bot, msg = _make_message(command_args="Oslo")

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            yield "   \n  "

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_weather(bot, msg)

        sent = msg.reply.return_value
        call_text = sent.edit.call_args[0][0]
        assert "❌" in call_text or "не удалось" in call_text.lower()

    @pytest.mark.asyncio
    async def test_streaming_несколько_чанков_склеиваются(self) -> None:
        """Несколько streaming-чанков → склеиваются в один ответ."""
        bot, msg = _make_message(command_args="Vienna")

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            yield "Температура: "
            yield "12°C. "
            yield "Облачно, без осадков."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_weather(bot, msg)

        sent = msg.reply.return_value
        call_text = sent.edit.call_args[0][0]
        assert "Температура: 12°C. Облачно, без осадков." in call_text

    @pytest.mark.asyncio
    async def test_exception_из_openclaw_graceful(self) -> None:
        """RuntimeError в send_message_stream → edit() с сообщением об ошибке."""
        bot, msg = _make_message(command_args="Madrid")

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            raise RuntimeError("network error")
            yield  # делаем генератором

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_weather(bot, msg)

        sent = msg.reply.return_value
        call_text = sent.edit.call_args[0][0]
        assert "❌" in call_text

    @pytest.mark.asyncio
    async def test_exception_содержит_текст_ошибки(self) -> None:
        """Текст исключения отображается пользователю."""
        bot, msg = _make_message(command_args="Athens")

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            raise ValueError("quota exceeded")
            yield

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_weather(bot, msg)

        sent = msg.reply.return_value
        call_text = sent.edit.call_args[0][0]
        assert "quota exceeded" in call_text


# ===========================================================================
# Промпт содержит запрос на актуальность
# ===========================================================================


class TestHandleWeatherPrompt:
    """Промпт содержит нужные компоненты."""

    @pytest.mark.asyncio
    async def test_промпт_содержит_упоминание_погоды(self) -> None:
        """Промпт содержит слово 'погода' или 'weather'."""
        bot, msg = _make_message(command_args="Helsinki")

        captured: list[str] = []

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            captured.append(message)
            yield "-10°C, снег"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_weather(bot, msg)

        prompt = captured[0].lower()
        assert "погода" in prompt or "weather" in prompt

    @pytest.mark.asyncio
    async def test_промпт_содержит_запрос_краткости(self) -> None:
        """Промпт просит краткий ответ."""
        bot, msg = _make_message(command_args="Lisbon")

        captured: list[str] = []

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            captured.append(message)
            yield "20°C"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_weather(bot, msg)

        prompt = captured[0].lower()
        assert "кратк" in prompt


# ===========================================================================
# Экспорт из handlers
# ===========================================================================


class TestHandleWeatherExported:
    """handle_weather должен быть экспортирован из модуля handlers."""

    def test_handle_weather_importable(self) -> None:
        """handle_weather импортируется из src.handlers.command_handlers."""
        from src.handlers.command_handlers import handle_weather  # noqa: F401

        assert callable(handle_weather)

    def test_handle_weather_async(self) -> None:
        """handle_weather — корутина."""
        import asyncio

        from src.handlers.command_handlers import handle_weather

        assert asyncio.iscoroutinefunction(handle_weather)


# ===========================================================================
# Config: DEFAULT_WEATHER_CITY
# ===========================================================================


class TestDefaultWeatherCityConfig:
    """DEFAULT_WEATHER_CITY доступен в config."""

    def test_config_имеет_default_weather_city(self) -> None:
        """Config содержит атрибут DEFAULT_WEATHER_CITY."""
        from src.config import config

        assert hasattr(config, "DEFAULT_WEATHER_CITY")

    def test_default_weather_city_является_строкой(self) -> None:
        """DEFAULT_WEATHER_CITY — непустая строка."""
        from src.config import config

        assert isinstance(config.DEFAULT_WEATHER_CITY, str)
        assert len(config.DEFAULT_WEATHER_CITY) > 0

    def test_default_weather_city_по_умолчанию_barcelona(self) -> None:
        """По умолчанию (без env-переменной и без .env) значение 'Barcelona'."""
        import os
        from importlib import reload
        from unittest.mock import patch

        # Сохраняем текущий env и отключаем load_dotenv, чтобы reload
        # не подтянул .env (где может быть DEFAULT_WEATHER_CITY=Tokyo).
        original = os.environ.pop("DEFAULT_WEATHER_CITY", None)
        try:
            with patch("dotenv.load_dotenv", lambda *a, **kw: False):
                import src.config as config_module

                reload(config_module)
                assert config_module.Config.DEFAULT_WEATHER_CITY == "Barcelona"
        finally:
            if original is not None:
                os.environ["DEFAULT_WEATHER_CITY"] = original
            import src.config as config_module

            reload(config_module)

    def test_default_weather_city_override_через_env(self) -> None:
        """DEFAULT_WEATHER_CITY читается из env-переменной."""
        import os
        from importlib import reload

        os.environ["DEFAULT_WEATHER_CITY"] = "Timbuktu"
        try:
            import src.config as config_module

            reload(config_module)
            assert config_module.Config.DEFAULT_WEATHER_CITY == "Timbuktu"
        finally:
            del os.environ["DEFAULT_WEATHER_CITY"]
            reload(config_module)


# ===========================================================================
# command_registry
# ===========================================================================


class TestWeatherCommandRegistry:
    """!weather зарегистрирована в command_registry."""

    def test_weather_в_реестре(self) -> None:
        """Команда 'weather' присутствует в реестре."""
        from src.core.command_registry import registry

        cmd = registry.get("weather")
        assert cmd is not None

    def test_weather_категория_ai(self) -> None:
        """Команда 'weather' в категории 'ai'."""
        from src.core.command_registry import registry

        cmd = registry.get("weather")
        assert cmd is not None
        assert cmd.category == "ai"

    def test_weather_в_списке_по_категории(self) -> None:
        """by_category('ai') содержит команду 'weather'."""
        from src.core.command_registry import registry

        ai_cmds = [c.name for c in registry.by_category("ai")]
        assert "weather" in ai_cmds
