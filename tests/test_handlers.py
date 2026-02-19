# -*- coding: utf-8 -*-
"""
Тесты для модульной системы обработчиков (src/handlers/).

Покрывает:
- auth.py: авторизация, проверка прав, определение владельца
- scheduling.py: _parse_duration
- commands.py / ai.py / tools.py / system.py: mock-регистрация
- Интеграция: register_all_handlers
"""

import os
import sys
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# === AUTH MODULE ===

class TestAuth:
    """Тесты для src/handlers/auth.py — централизованная авторизация."""

    def test_get_owner_from_env(self):
        """Проверяем что get_owner читает из OWNER_USERNAME."""
        with patch.dict(os.environ, {"OWNER_USERNAME": "@testowner"}):
            from src.handlers.auth import get_owner
            assert get_owner() == "testowner"

    def test_get_owner_strips_at(self):
        """@ в начале должен быть убран."""
        with patch.dict(os.environ, {"OWNER_USERNAME": "@p0lrd"}):
            from src.handlers.auth import get_owner
            assert get_owner() == "p0lrd"

    def test_get_owner_without_at(self):
        """Владелец без @ тоже работает."""
        with patch.dict(os.environ, {"OWNER_USERNAME": "p0lrd"}):
            from src.handlers.auth import get_owner
            assert get_owner() == "p0lrd"

    def test_get_allowed_users_includes_owner(self):
        """Список разрешённых должен включать владельца."""
        with patch.dict(os.environ, {
            "OWNER_USERNAME": "p0lrd",
            "ALLOWED_USERS": "user1,user2"
        }):
            from src.handlers.auth import get_allowed_users
            allowed = get_allowed_users()
            assert "p0lrd" in allowed
            assert "user1" in allowed
            assert "user2" in allowed

    def test_get_allowed_users_handles_empty(self):
        """Пустой ALLOWED_USERS не ломает систему."""
        with patch.dict(os.environ, {
            "OWNER_USERNAME": "p0lrd",
            "ALLOWED_USERS": ""
        }):
            from src.handlers.auth import get_allowed_users
            allowed = get_allowed_users()
            assert "p0lrd" in allowed
            assert len(allowed) >= 1

    def test_is_owner_self_message(self):
        """Сообщения от self (юзербот) считаются от владельца."""
        from src.handlers.auth import is_owner
        msg = MagicMock()
        msg.from_user.is_self = True
        msg.from_user.username = "whatever"
        assert is_owner(msg) is True

    def test_is_owner_by_username(self):
        """Владелец определяется по username из .env."""
        from src.handlers.auth import is_owner
        with patch.dict(os.environ, {"OWNER_USERNAME": "p0lrd"}):
            msg = MagicMock()
            msg.from_user.is_self = False
            msg.from_user.username = "p0lrd"
            assert is_owner(msg) is True

    def test_is_owner_not_owner(self):
        """Чужой пользователь не является владельцем."""
        from src.handlers.auth import is_owner
        with patch.dict(os.environ, {"OWNER_USERNAME": "p0lrd"}):
            msg = MagicMock()
            msg.from_user.is_self = False
            msg.from_user.username = "hacker"
            assert is_owner(msg) is False

    def test_is_authorized_owner(self):
        """Владелец всегда авторизован."""
        from src.handlers.auth import is_authorized
        with patch.dict(os.environ, {
            "OWNER_USERNAME": "p0lrd",
            "ALLOWED_USERS": ""
        }):
            msg = MagicMock()
            msg.from_user.is_self = True
            msg.from_user.username = "p0lrd"
            msg.from_user.id = 123
            assert is_authorized(msg) is True

    def test_is_authorized_allowed_user(self):
        """Пользователь из ALLOWED_USERS авторизован."""
        from src.handlers.auth import is_authorized
        with patch.dict(os.environ, {
            "OWNER_USERNAME": "p0lrd",
            "ALLOWED_USERS": "friend1,friend2"
        }):
            msg = MagicMock()
            msg.from_user.is_self = False
            msg.from_user.username = "friend1"
            msg.from_user.id = 456
            assert is_authorized(msg) is True

    def test_is_authorized_stranger(self):
        """Неизвестный пользователь не авторизован."""
        from src.handlers.auth import is_authorized
        with patch.dict(os.environ, {
            "OWNER_USERNAME": "p0lrd",
            "ALLOWED_USERS": "friend1"
        }):
            msg = MagicMock()
            msg.from_user.is_self = False
            msg.from_user.username = "stranger"
            msg.from_user.id = 999
            assert is_authorized(msg) is False


# === SCHEDULING: _parse_duration ===

class TestParseDuration:
    """Тесты для парсинга длительности (scheduling.py)."""

    def test_seconds_default(self):
        from src.handlers.scheduling import _parse_duration
        assert _parse_duration("90") == 90

    def test_seconds_explicit(self):
        from src.handlers.scheduling import _parse_duration
        assert _parse_duration("30s") == 30

    def test_minutes(self):
        from src.handlers.scheduling import _parse_duration
        assert _parse_duration("5m") == 300
        assert _parse_duration("10min") == 600

    def test_hours(self):
        from src.handlers.scheduling import _parse_duration
        assert _parse_duration("2h") == 7200
        assert _parse_duration("1hour") == 3600

    def test_days(self):
        from src.handlers.scheduling import _parse_duration
        assert _parse_duration("1d") == 86400
        assert _parse_duration("2day") == 172800

    def test_invalid_format(self):
        from src.handlers.scheduling import _parse_duration
        assert _parse_duration("garbage") == 0
        assert _parse_duration("") == 0
        assert _parse_duration("abc123") == 0

    def test_whitespace_handling(self):
        from src.handlers.scheduling import _parse_duration
        assert _parse_duration("  5m  ") == 300
        assert _parse_duration(" 30s ") == 30


# === HANDLER REGISTRATION ===

class TestHandlerRegistration:
    """Тесты для register_all_handlers (src/handlers/__init__.py)."""

    def test_register_all_handlers_succeeds(self):
        """register_all_handlers не падает при вызове с mock-зависимостями."""
        from src.handlers import register_all_handlers

        # Мок Pyrogram App
        mock_app = MagicMock()
        mock_app.on_message = MagicMock(return_value=lambda f: f)

        # Мок зависимостей — минимальный набор
        deps = {
            "router": MagicMock(),
            "memory": MagicMock(),
            "perceptor": MagicMock(),
            "screen_catcher": MagicMock(),
            "black_box": MagicMock(),
            "scout": MagicMock(),
            "security": MagicMock(),
            "config_manager": MagicMock(),
            "persona_manager": MagicMock(),
            "agent": MagicMock(),
            "tools": MagicMock(),
            "rate_limiter": MagicMock(),
            "safe_handler": lambda f: f,  # Декоратор-заглушка
            "get_last_logs": MagicMock(return_value=[]),
        }

        # Не должно бросать исключений
        register_all_handlers(mock_app, deps)

    def test_handler_modules_importable(self):
        """Все handler-модули должны быть импортируемы."""
        import importlib
        modules = [
            "src.handlers.auth",
            "src.handlers.commands",
            "src.handlers.ai",
            "src.handlers.media",
            "src.handlers.tools",
            "src.handlers.system",
            "src.handlers.scheduling",
            "src.handlers.mac",
            "src.handlers.rag",
            "src.handlers.persona",
        ]
        for mod_name in modules:
            mod = importlib.import_module(mod_name)
            assert mod is not None, f"Модуль {mod_name} не загрузился"

    def test_each_module_has_register_handlers(self):
        """Каждый handler-модуль (кроме auth) имеет функцию register_handlers."""
        import importlib
        modules = [
            "src.handlers.commands",
            "src.handlers.ai",
            "src.handlers.media",
            "src.handlers.tools",
            "src.handlers.system",
            "src.handlers.scheduling",
            "src.handlers.mac",
            "src.handlers.rag",
            "src.handlers.persona",
        ]
        for mod_name in modules:
            mod = importlib.import_module(mod_name)
            assert hasattr(mod, "register_handlers"), \
                f"Модуль {mod_name} не имеет функции register_handlers"
            assert callable(mod.register_handlers), \
                f"{mod_name}.register_handlers не является вызываемым объектом"


# === MODEL MANAGER: конфигурация моделей ===

class TestModelManagerConfig:
    """Тесты для конфигурирования моделей из .env."""

    def test_default_models(self):
        """Дефолтные модели используются когда .env не задан."""
        from src.core.model_manager import ModelRouter
        router = ModelRouter(config={})
        assert "gemini" in router.models["chat"]
        assert "thinking" in router.models["thinking"]

    def test_custom_models_from_env(self):
        """Модели из .env имеют приоритет."""
        from src.core.model_manager import ModelRouter
        config = {
            "GEMINI_CHAT_MODEL": "gemini-3-pro",
            "GEMINI_THINKING_MODEL": "gemini-3-thinking",
            "GEMINI_PRO_MODEL": "gemini-3-ultra",
            "GEMINI_CODING_MODEL": "gemini-3-code",
        }
        router = ModelRouter(config=config)
        assert router.models["chat"] == "gemini-3-pro"
        assert router.models["thinking"] == "gemini-3-thinking"
        assert router.models["pro"] == "gemini-3-ultra"
        assert router.models["coding"] == "gemini-3-code"


# === PERCEPTOR: конфигурация ===

class TestPerceptorConfig:
    """Тесты для конфигурирования Perceptor."""

    @patch("src.modules.perceptor.register_heif_opener")
    def test_vision_model_from_env(self, mock_heif):
        """Vision model читается из .env."""
        with patch.dict(os.environ, {"GEMINI_VISION_MODEL": "gemini-3-vision"}):
            # Перезагружаем модуль
            import importlib
            import src.modules.perceptor as perc_mod
            importlib.reload(perc_mod)
            
            with patch.object(perc_mod.Perceptor, '_warmup_audio'):
                p = perc_mod.Perceptor(config={})
                assert p.vision_model == "gemini-3-vision"

    @patch("src.modules.perceptor.register_heif_opener")
    def test_whisper_model_from_config(self, mock_heif):
        """Whisper model читается из config dict."""
        import importlib
        import src.modules.perceptor as perc_mod
        importlib.reload(perc_mod)
        
        with patch.object(perc_mod.Perceptor, '_warmup_audio'):
            p = perc_mod.Perceptor(config={"WHISPER_MODEL": "custom-whisper"})
            assert p.whisper_model == "custom-whisper"


class TestConfigManagerExposure:
    def test_get_all_returns_copy(self):
        from src.core.config_manager import ConfigManager
        cfg = ConfigManager()
        cfg.set("ai.temperature", 0.5)
        snapshot = cfg.get_all()
        assert isinstance(snapshot, dict)
        snapshot["ai"]["temperature"] = 0.1
        assert cfg.get("ai.temperature") == 0.5


class _DummyApp:
    def __init__(self):
        self.handlers = {}

    def on_message(self, *args, **kwargs):
        def decorator(func):
            self.handlers[func.__name__] = func
            return func

        return decorator

    def on_callback_query(self, *args, **kwargs):
        return self.on_message(*args, **kwargs)


class _MockStatusMessage:
    def __init__(self):
        self.command = ["!status"]
        self.from_user = SimpleNamespace(is_self=True, username="owner", id=1)
        self.chat = SimpleNamespace(id=123, title="test")
        self.reply_text = AsyncMock(
            return_value=SimpleNamespace(edit_text=AsyncMock())
        )


class _StatusSink:
    """Накопитель текста edit_text для проверки итогового отчета."""

    def __init__(self):
        self.last_text = ""
        self.edit_text = AsyncMock(side_effect=self._store)

    async def _store(self, text):
        self.last_text = str(text)


class _MockStatusMessageWithSink:
    """Сообщение !status с перехватом финального текста отчета."""

    def __init__(self):
        self.command = ["!status"]
        self.from_user = SimpleNamespace(is_self=True, username="owner", id=1)
        self.chat = SimpleNamespace(id=123, title="test")
        self.sink = _StatusSink()
        self.reply_text = AsyncMock(return_value=self.sink)


def _build_status_handler(reminder_manager, black_box=None):
    from src.handlers.commands import register_handlers as register_commands

    router = MagicMock()
    router.check_local_health = AsyncMock(return_value=True)
    router.gemini_client = MagicMock()
    router.rag = MagicMock(get_total_documents=MagicMock(return_value=5))
    router.local_engine = "lm-studio"
    router.active_local_model = "qwen2.5-7b"
    router.models = {"chat": "gemini-2.5-flash"}
    router.is_local_available = True
    router._stats = {
        "local_calls": 1,
        "cloud_calls": 0,
        "local_failures": 0,
        "cloud_failures": 0,
    }

    voice_gateway_client = MagicMock()
    voice_gateway_client.health_check = AsyncMock(return_value=True)

    openclaw_client = MagicMock()
    openclaw_client.health_check = AsyncMock(return_value=True)

    config_manager = MagicMock()
    config_manager.get = MagicMock(side_effect=lambda key, default=None: default or 8080)

    deps = {
        "router": router,
        "config_manager": config_manager,
        "black_box": black_box or MagicMock(get_uptime=MagicMock(return_value="0h 0m 1s")),
        "safe_handler": lambda f: f,
        "voice_gateway_client": voice_gateway_client,
        "openclaw_client": openclaw_client,
        "reminder_manager": reminder_manager,
        "persona_manager": MagicMock(active_persona="default", personas={"default": {}}),
    }
    app = _DummyApp()
    register_commands(app, deps)
    return app.handlers["status_command"]


@patch("src.handlers.commands.logger.warning")
@pytest.mark.asyncio
async def test_status_warns_when_reminder_manager_missing(mock_warning):
    handler = _build_status_handler(None)
    await handler(None, _MockStatusMessage())
    mock_warning.assert_any_call("Reminder manager missing for status command.")


class _FailingReminderManager:
    def get_list(self, chat_id):
        raise RuntimeError("boom")


@patch("src.handlers.commands.logger.warning")
@pytest.mark.asyncio
async def test_status_logs_when_reminder_list_fails(mock_warning):
    handler = _build_status_handler(_FailingReminderManager())
    await handler(None, _MockStatusMessage())
    mock_warning.assert_any_call(
        "Reminder manager get_list failed for status.", error="boom"
    )


@patch("src.handlers.commands.logger.warning")
@pytest.mark.asyncio
async def test_status_ignores_missing_get_uptime(mock_warning):
    handler = _build_status_handler(None, black_box=MagicMock())
    await handler(None, _MockStatusMessage())
    mock_warning.assert_any_call(
        "Reminder manager missing for status command."
    )


@patch("src.handlers.commands.logger.warning")
@pytest.mark.asyncio
async def test_status_degrades_when_blackbox_missing_get_uptime(mock_warning):
    handler = _build_status_handler(None, black_box=SimpleNamespace())
    await handler(None, _MockStatusMessage())
    assert mock_warning.call_count >= 1  # ensures status ran without AttributeError


@pytest.mark.asyncio
async def test_status_shows_local_degradation_reason_and_cloud_fallback() -> None:
    from src.handlers.commands import register_handlers as register_commands

    router = MagicMock()
    router.check_local_health = AsyncMock(return_value=False)
    router.rag = None
    router.local_engine = "lm-studio"
    router.active_local_model = None
    router.last_local_load_error_human = "⚠️ LM Studio доступна, но ни одна модель не загружена."
    router.last_local_load_error = "no_model_loaded"
    router.models = {"chat": "gemini-2.5-flash-lite"}
    router._stats = {"local_calls": 0, "cloud_calls": 2, "local_failures": 0, "cloud_failures": 0}
    router.get_last_route = MagicMock(
        return_value={"channel": "cloud", "profile": "communication", "model": "gemini-2.5-flash-lite"}
    )
    router.get_last_stream_route = MagicMock(return_value={})

    voice_gateway_client = MagicMock()
    voice_gateway_client.health_check = AsyncMock(return_value=False)
    openclaw_client = MagicMock()
    openclaw_client.health_check = AsyncMock(return_value=True)
    config_manager = MagicMock()
    config_manager.get = MagicMock(side_effect=lambda key, default=None: default or 8080)

    deps = {
        "router": router,
        "config_manager": config_manager,
        "black_box": MagicMock(get_uptime=MagicMock(return_value="0h 0m 1s")),
        "safe_handler": lambda f: f,
        "voice_gateway_client": voice_gateway_client,
        "openclaw_client": openclaw_client,
        "reminder_manager": None,
        "persona_manager": MagicMock(active_persona="default", personas={"default": {}}),
    }
    app = _DummyApp()
    register_commands(app, deps)
    handler = app.handlers["status_command"]

    msg = _MockStatusMessageWithSink()
    await handler(None, msg)

    assert "Reason:" in msg.sink.last_text
    assert "LM Studio доступна" in msg.sink.last_text
    assert "Cloud reason" in msg.sink.last_text
    assert "local_unavailable" in msg.sink.last_text


@pytest.mark.asyncio
async def test_status_shows_explicit_cloud_route_reason() -> None:
    from src.handlers.commands import register_handlers as register_commands

    router = MagicMock()
    router.check_local_health = AsyncMock(return_value=True)
    router.rag = None
    router.local_engine = "lm-studio"
    router.active_local_model = "zai-org/glm-4.6v-flash"
    router.last_local_load_error_human = ""
    router.last_local_load_error = ""
    router.models = {"chat": "gemini-2.5-flash-lite"}
    router._stats = {"local_calls": 3, "cloud_calls": 4, "local_failures": 0, "cloud_failures": 0}
    router.get_last_route = MagicMock(
        return_value={
            "channel": "cloud",
            "profile": "communication",
            "model": "gemini-2.5-pro",
            "route_reason": "force_cloud",
            "route_detail": "forced by router mode",
        }
    )
    router.get_last_stream_route = MagicMock(return_value={})

    voice_gateway_client = MagicMock()
    voice_gateway_client.health_check = AsyncMock(return_value=True)
    openclaw_client = MagicMock()
    openclaw_client.health_check = AsyncMock(return_value=True)
    config_manager = MagicMock()
    config_manager.get = MagicMock(side_effect=lambda key, default=None: default or 8080)

    deps = {
        "router": router,
        "config_manager": config_manager,
        "black_box": MagicMock(get_uptime=MagicMock(return_value="0h 0m 2s")),
        "safe_handler": lambda f: f,
        "voice_gateway_client": voice_gateway_client,
        "openclaw_client": openclaw_client,
        "reminder_manager": None,
        "persona_manager": MagicMock(active_persona="default", personas={"default": {}}),
    }
    app = _DummyApp()
    register_commands(app, deps)
    handler = app.handlers["status_command"]

    msg = _MockStatusMessageWithSink()
    await handler(None, msg)

    assert "Cloud reason" in msg.sink.last_text
    assert "force_cloud" in msg.sink.last_text
    assert "forced by router mode" in msg.sink.last_text


class TestAiOutputPostprocess:
    """Точечные тесты постобработки AI-ответа."""

    def test_is_voice_reply_requested_requires_explicit_voice_marker(self):
        from src.handlers.ai import _is_voice_reply_requested

        assert _is_voice_reply_requested("Ответь голосом, пожалуйста") is True
        assert _is_voice_reply_requested("Отвечай, пожалуйста, голосом") is True
        assert _is_voice_reply_requested("Голосом отвечай, мне сложно читать") is True
        assert _is_voice_reply_requested("voice reply with summary") is True
        assert _is_voice_reply_requested("Расскажи подробнее про почту") is False
        assert _is_voice_reply_requested("Можешь проанализировать письма?") is False

    def test_should_force_russian_reply_respects_explicit_english_request(self):
        from src.handlers.ai import _should_force_russian_reply

        assert (
            _should_force_russian_reply(
                user_text="Please answer in English",
                is_private=True,
                is_owner_sender=True,
                is_voice_response_needed=False,
            )
            is False
        )
        assert (
            _should_force_russian_reply(
                user_text="Сделай обзор статуса",
                is_private=True,
                is_owner_sender=True,
                is_voice_response_needed=False,
            )
            is True
        )

    def test_drop_english_scaffold_when_russian_expected(self):
        from src.handlers.ai import _drop_english_scaffold_when_russian_expected

        payload = (
            "The apple-mail skill can be configured and used via shell scripts located in the scripts directory. "
            "Here is the setup process with prerequisites, commands and output examples for every step.\n\n"
            "Отлично, продолжаем на русском. Ниже краткий практический план и следующие шаги для настройки."
        )
        cleaned, removed = _drop_english_scaffold_when_russian_expected(
            payload,
            prefer_russian=True,
            min_paragraph_len=120,
        )
        assert removed is True
        assert "The apple-mail skill can be configured" not in cleaned
        assert "продолжаем на русском" in cleaned

    def test_prune_repetitive_numbered_items_removes_duplicates(self):
        from src.handlers.ai import _prune_repetitive_numbered_items

        payload = (
            "1. Проверь окружение\n"
            "2. Найди воду\n"
            "3. Проверь окружение\n"
            "4. Проверь окружение\n"
            "5. Найди воду\n"
        )
        cleaned, removed = _prune_repetitive_numbered_items(payload, max_same_body=2)
        assert removed is True
        assert cleaned.count("Проверь окружение") == 2
        assert cleaned.count("Найди воду") == 2

    def test_prune_repetitive_numbered_items_keeps_unique_lines(self):
        from src.handlers.ai import _prune_repetitive_numbered_items

        payload = (
            "1. Подготовь укрытие\n"
            "2. Найди источник воды\n"
            "3. Организуй сигнал SOS\n"
        )
        cleaned, removed = _prune_repetitive_numbered_items(payload, max_same_body=2)
        assert removed is False
        assert cleaned == payload.strip()

    def test_collapse_repeated_lines_removes_looped_plain_text(self):
        from src.handlers.ai import _collapse_repeated_lines

        payload = (
            "Это означает, что вы описываете своего спасителя как хорошего человека.\n"
            "Это означает, что вы описываете своего спасителя как \"хорошего\" человека.\n"
            "Это означает, что вы описываете своего спасителя как *хорошего* человека.\n"
            "Итог: нужно сверить факты и не делать поспешных выводов."
        )
        cleaned, removed = _collapse_repeated_lines(payload, max_consecutive_repeats=2)
        assert removed is True
        assert cleaned.count("Это означает, что вы описываете своего спасителя") == 2
        assert "Итог: нужно сверить факты" in cleaned

    def test_collapse_repeated_lines_keeps_non_repeating_flow(self):
        from src.handlers.ai import _collapse_repeated_lines

        payload = (
            "Шаг 1: Проверь факты.\n"
            "Шаг 2: Уточни источник.\n"
            "Шаг 3: Сделай вывод."
        )
        cleaned, removed = _collapse_repeated_lines(payload, max_consecutive_repeats=2)
        assert removed is False
        assert cleaned == payload

    def test_collapse_repeated_paragraphs_normalizes_punctuation(self):
        from src.handlers.ai import _collapse_repeated_paragraphs

        payload = (
            "Это означает, что вы описываете своего спасителя как \"хорошего\" человека.\n\n"
            "Это означает, что вы описываете своего спасителя как *хорошего* человека!\n\n"
            "Это означает, что вы описываете своего спасителя как хорошего человека...\n\n"
            "Вывод: уточните факты."
        )
        cleaned, removed = _collapse_repeated_paragraphs(payload, max_consecutive_repeats=2)
        assert removed is True
        assert cleaned.lower().count("это означает, что вы описываете своего спасителя") == 2
        assert "Вывод: уточните факты." in cleaned

    def test_split_text_chunks_prefers_paragraph_boundaries(self):
        from src.handlers.ai import _split_text_chunks_for_telegram

        payload = (
            "Абзац 1.\n\n"
            + ("Абзац 2 очень длинный. " * 180)
            + "\n\nАбзац 3 финальный."
        )
        chunks = _split_text_chunks_for_telegram(payload, max_len=900)
        assert len(chunks) >= 2
        assert all(len(chunk) <= 900 for chunk in chunks)
        assert "Абзац 1." in chunks[0]

    def test_split_text_chunks_fallback_for_single_long_line(self):
        from src.handlers.ai import _split_text_chunks_for_telegram

        payload = "x" * 4200
        chunks = _split_text_chunks_for_telegram(payload, max_len=1000)
        assert len(chunks) == 5
        assert "".join(chunks) == payload

    def test_split_tts_chunks_splits_long_text_without_loss(self):
        from src.handlers.ai import _split_tts_chunks

        payload = ("Первый блок. " * 140) + "\n\n" + ("Второй блок. " * 140)
        chunks = _split_tts_chunks(payload, max_chars=500, max_chunks=10)
        assert len(chunks) >= 2
        assert all(len(chunk) <= 500 for chunk in chunks[:-1])
        merged = " ".join(chunks)
        assert "Первый блок" in merged
        assert "Второй блок" in merged

    def test_split_tts_chunks_respects_max_chunks(self):
        from src.handlers.ai import _split_tts_chunks

        payload = " ".join(["слово"] * 8000)
        chunks = _split_tts_chunks(payload, max_chars=220, max_chunks=4)
        assert len(chunks) <= 4
        assert any(chunk.strip() for chunk in chunks)

    def test_dedupe_repeated_long_paragraphs_removes_nonconsecutive_duplicates(self):
        from src.handlers.ai import _dedupe_repeated_long_paragraphs

        repeated = (
            "Этот длинный абзац нужен для теста удаления не подряд идущих повторов. "
            "Он содержит много слов, чтобы пройти порог нормализации."
        )
        payload = (
            f"{repeated}\n\n"
            "Короткая вставка между блоками.\n\n"
            f"{repeated}\n\n"
            "Финал без дубликатов."
        )
        cleaned, removed = _dedupe_repeated_long_paragraphs(payload, min_normalized_len=80, max_occurrences=1)
        assert removed is True
        assert cleaned.count("Этот длинный абзац нужен для теста") == 1
        assert "Финал без дубликатов." in cleaned

    def test_build_vision_route_fact_line_reports_cloud_fallback(self):
        from src.handlers.ai import _build_vision_route_fact_line

        line = _build_vision_route_fact_line(
            {
                "route": "cloud_gemini",
                "model": "gemini-2.0-flash",
                "fallback_used": True,
                "error": "local_vision_model_not_set",
            }
        )
        assert "cloud через Gemini" in line
        assert "неуспешной попытки локального vision" in line

    def test_enforce_vision_route_consistency_strips_local_only_claims_for_cloud(self):
        from src.handlers.ai import _enforce_vision_route_consistency

        payload = (
            "ℹ️ Факт vision: cloud через Gemini (gemini-2.0-flash).\n\n"
            "Мы полностью перешли к локальной обработке данных.\n\n"
            "Никакие данные не передаются в интернет.\n\n"
            "Короткий полезный вывод по изображению."
        )
        cleaned, changed = _enforce_vision_route_consistency(
            payload,
            {"route": "cloud_gemini", "model": "gemini-2.0-flash", "fallback_used": True},
        )
        lowered = cleaned.lower()
        assert changed is True
        assert "полностью перешли к локальной" not in lowered
        assert "не передаются в интернет" not in lowered
        assert "vision-запрос выполнен через cloud" in lowered
        assert "полезный вывод по изображению" in lowered


class _MockPolicyMessage:
    def __init__(self, text: str):
        self.text = text
        self.command = text.split()
        self.from_user = SimpleNamespace(is_self=True, username="owner", id=1)
        self.chat = SimpleNamespace(id=123, type=SimpleNamespace(name="PRIVATE"))
        self.reply_text = AsyncMock()


def _build_policy_handler(ai_runtime, config_manager=None):
    from src.handlers.commands import register_handlers as register_commands

    deps = {
        "router": MagicMock(),
        "config_manager": config_manager or MagicMock(),
        "black_box": MagicMock(),
        "safe_handler": lambda f: f,
        "voice_gateway_client": MagicMock(),
        "openclaw_client": MagicMock(),
        "reminder_manager": MagicMock(),
        "persona_manager": MagicMock(active_persona="default", personas={"default": {}}),
        "ai_runtime": ai_runtime,
    }
    app = _DummyApp()
    register_commands(app, deps)
    return app.handlers["policy_command"]


@pytest.mark.asyncio
async def test_policy_queue_author_isolation_toggle():
    ai_runtime = MagicMock()
    ai_runtime.get_policy_snapshot = MagicMock(return_value={"queue": {}, "guardrails": {}})
    ai_runtime.set_group_author_isolation_enabled = MagicMock()
    config_manager = MagicMock()
    handler = _build_policy_handler(ai_runtime, config_manager=config_manager)

    msg = _MockPolicyMessage("!policy queue author_isolation off")
    await handler(None, msg)

    ai_runtime.set_group_author_isolation_enabled.assert_called_once_with(False)
    config_manager.set.assert_called_once_with("AUTO_REPLY_GROUP_AUTHOR_ISOLATION_ENABLED", "0")
    msg.reply_text.assert_called()


@pytest.mark.asyncio
async def test_policy_queue_continue_toggle():
    ai_runtime = MagicMock()
    ai_runtime.get_policy_snapshot = MagicMock(return_value={"queue": {}, "guardrails": {}})
    ai_runtime.set_continue_on_incomplete_enabled = MagicMock()
    config_manager = MagicMock()
    handler = _build_policy_handler(ai_runtime, config_manager=config_manager)

    msg = _MockPolicyMessage("!policy queue continue on")
    await handler(None, msg)

    ai_runtime.set_continue_on_incomplete_enabled.assert_called_once_with(True)
    config_manager.set.assert_called_once_with("AUTO_REPLY_CONTINUE_ON_INCOMPLETE", "1")
    msg.reply_text.assert_called()


@pytest.mark.asyncio
async def test_policy_queue_retries_toggle():
    ai_runtime = MagicMock()
    ai_runtime.get_policy_snapshot = MagicMock(return_value={"queue": {}, "guardrails": {}})
    ai_runtime.set_queue_max_retries = MagicMock()
    config_manager = MagicMock()
    handler = _build_policy_handler(ai_runtime, config_manager=config_manager)

    msg = _MockPolicyMessage("!policy queue retries 3")
    await handler(None, msg)

    ai_runtime.set_queue_max_retries.assert_called_once_with(3)
    config_manager.set.assert_called_once_with("AUTO_REPLY_QUEUE_MAX_RETRIES", "3")
    msg.reply_text.assert_called()


@pytest.mark.asyncio
async def test_policy_queue_notify_toggle():
    ai_runtime = MagicMock()
    ai_runtime.get_policy_snapshot = MagicMock(return_value={"queue": {}, "guardrails": {}})
    ai_runtime.set_queue_notify_position_enabled = MagicMock()
    config_manager = MagicMock()
    handler = _build_policy_handler(ai_runtime, config_manager=config_manager)

    msg = _MockPolicyMessage("!policy queue notify off")
    await handler(None, msg)

    ai_runtime.set_queue_notify_position_enabled.assert_called_once_with(False)
    config_manager.set.assert_called_once_with("AUTO_REPLY_QUEUE_NOTIFY_POSITION", "0")
    msg.reply_text.assert_called()


@pytest.mark.asyncio
async def test_policy_show_displays_author_isolation():
    ai_runtime = MagicMock()
    ai_runtime.get_policy_snapshot = MagicMock(
        return_value={
            "queue_enabled": True,
            "queue_notify_position_enabled": True,
            "forward_context_enabled": True,
            "group_author_isolation_enabled": True,
            "reaction_learning_enabled": True,
            "chat_mood_enabled": True,
            "auto_reactions_enabled": True,
            "queue": {"max_per_chat": 50, "queued_total": 0, "active_chats": 0, "retried": 2, "max_retries": 1},
            "guardrails": {
                "local_include_reasoning": True,
                "local_reasoning_max_chars": 2000,
                "local_stream_total_timeout_seconds": 60.0,
                "local_stream_sock_read_timeout_seconds": 20.0,
            },
        }
    )
    handler = _build_policy_handler(ai_runtime)

    msg = _MockPolicyMessage("!policy show")
    await handler(None, msg)
    sent = msg.reply_text.call_args.args[0]
    assert "Group author isolation" in sent
    assert "Continue on incomplete" in sent
    assert "Queue notify position" in sent
    assert "Queue retried" in sent


def _build_ctx_handler(ai_runtime):
    from src.handlers.commands import register_handlers as register_commands

    router = MagicMock()
    router.get_last_route = MagicMock(return_value={"channel": "cloud", "profile": "chat", "model": "gemini-2.5-flash"})
    deps = {
        "router": router,
        "config_manager": MagicMock(),
        "black_box": MagicMock(),
        "safe_handler": lambda f: f,
        "voice_gateway_client": MagicMock(),
        "openclaw_client": MagicMock(),
        "reminder_manager": MagicMock(),
        "persona_manager": MagicMock(active_persona="default", personas={"default": {}}),
        "ai_runtime": ai_runtime,
    }
    app = _DummyApp()
    register_commands(app, deps)
    return app.handlers["ctx_command"]


@pytest.mark.asyncio
async def test_ctx_shows_group_author_isolation_fields():
    ai_runtime = MagicMock()
    ai_runtime.get_context_snapshot = MagicMock(
        return_value={
            "context_messages": 12,
            "prompt_length_chars": 777,
            "response_length_chars": 1234,
            "telegram_truncated": False,
            "telegram_chunks_sent": 1,
            "has_forward_context": True,
            "has_reply_context": True,
            "group_author_isolation_enabled": True,
            "continue_on_incomplete_enabled": True,
            "continue_on_incomplete_triggered": True,
            "continue_on_incomplete_applied": True,
            "group_author_context_trimmed": True,
            "group_author_context_user_messages_before": 8,
            "group_author_context_user_messages_after": 3,
            "group_author_context_dropped_user_messages": 5,
            "service_artifact_context_items_dropped": 2,
            "updated_at": 1234567890,
        }
    )
    handler = _build_ctx_handler(ai_runtime)
    msg = _MockPolicyMessage("!ctx")

    await handler(None, msg)

    sent = msg.reply_text.call_args.args[0]
    assert "Group author isolation" in sent
    assert "Continue on incomplete" in sent
    assert "Continue triggered/applied" in sent
    assert "Group context trimmed" in sent
    assert "Group user msgs dropped" in sent
    assert "Service ctx artifacts dropped" in sent


@pytest.mark.asyncio
async def test_ctx_all_lists_recent_snapshots():
    ai_runtime = MagicMock()
    ai_runtime.get_context_snapshot = MagicMock(return_value={})
    ai_runtime.get_context_snapshots = MagicMock(
        return_value={
            "100": {"context_messages": 7, "group_author_context_dropped_user_messages": 0, "updated_at": 1000},
            "200": {"context_messages": 9, "group_author_context_dropped_user_messages": 3, "updated_at": 2000},
        }
    )
    handler = _build_ctx_handler(ai_runtime)
    msg = _MockPolicyMessage("!ctx all")

    await handler(None, msg)

    sent = msg.reply_text.call_args.args[0]
    assert "Context Snapshot (all chats)" in sent
    assert "chat `200`" in sent
    assert "dropped=`3`" in sent


class _BrowserSink:
    """Перехватчик финального текста для !browser."""

    def __init__(self):
        self.last_text = ""
        self.edit_text = AsyncMock(side_effect=self._store)

    async def _store(self, text, *args, **kwargs):
        self.last_text = str(text)


class _MockBrowserMessage:
    def __init__(self, text: str):
        self.text = text
        self.command = text.split()
        self.from_user = SimpleNamespace(is_self=True, username="owner", id=1)
        self.chat = SimpleNamespace(id=123, type=SimpleNamespace(name="PRIVATE"))
        self.sink = _BrowserSink()
        self.reply_text = AsyncMock(return_value=self.sink)


def _build_browser_handler(openclaw_client):
    from src.handlers.commands import register_handlers as register_commands

    deps = {
        "router": MagicMock(),
        "config_manager": MagicMock(),
        "black_box": MagicMock(),
        "safe_handler": lambda f: f,
        "voice_gateway_client": MagicMock(),
        "openclaw_client": openclaw_client,
        "reminder_manager": MagicMock(),
        "persona_manager": MagicMock(active_persona="default", personas={"default": {}}),
    }
    app = _DummyApp()
    register_commands(app, deps)
    return app.handlers["browser_command"]


@pytest.mark.asyncio
async def test_browser_command_fast_mode_uses_research_fast():
    openclaw = MagicMock()
    openclaw.execute_agent_task = AsyncMock(return_value="Короткий web-ответ")
    handler = _build_browser_handler(openclaw)
    msg = _MockBrowserMessage("!browser fast проверка сети")

    await handler(None, msg)

    openclaw.execute_agent_task.assert_called_once_with("проверка сети", agent_id="research_fast")
    assert "OpenClaw Web Response (fast)" in msg.sink.last_text


@pytest.mark.asyncio
async def test_browser_command_default_mode_uses_research_deep():
    openclaw = MagicMock()
    openclaw.execute_agent_task = AsyncMock(return_value="Глубокий web-ответ")
    handler = _build_browser_handler(openclaw)
    msg = _MockBrowserMessage("!browser проверка маршрута")

    await handler(None, msg)

    openclaw.execute_agent_task.assert_called_once_with("проверка маршрута", agent_id="research_deep")
    assert "OpenClaw Web Response (deep)" in msg.sink.last_text
