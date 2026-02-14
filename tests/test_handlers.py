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
