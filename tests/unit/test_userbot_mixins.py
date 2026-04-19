# -*- coding: utf-8 -*-
"""
Тесты pure-функций и helper-методов userbot mixin-модулей:
- AccessControlMixin
- RuntimeStatusMixin (static helpers)
- SessionMixin (static helpers)
- VoiceProfileMixin (classmethod-нормализаторы и helpers)
"""

from __future__ import annotations

import sqlite3
from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# AccessControlMixin
# ---------------------------------------------------------------------------


class TestAccessControlMixinNormalizeUsername:
    """Нормализация username для ACL-сравнений."""

    def _normalize(self, value: str) -> str:
        from src.userbot.access_control import AccessControlMixin

        return AccessControlMixin._normalize_username(value)

    def test_strips_at_sign(self):
        assert self._normalize("@TestUser") == "testuser"

    def test_lowercase(self):
        assert self._normalize("KRAAB") == "kraab"

    def test_empty_string(self):
        assert self._normalize("") == ""

    def test_none_value(self):
        assert self._normalize(None) == ""  # type: ignore[arg-type]

    def test_whitespace_stripped(self):
        assert self._normalize("  @User  ") == "user"


class TestAccessControlMixinIsTrigger:
    """Детекция trigger-слова в тексте сообщения."""

    def _mixin(self):
        from src.userbot.access_control import AccessControlMixin

        class FakeBot(AccessControlMixin):
            pass

        obj = FakeBot.__new__(FakeBot)
        return obj

    def _make_config(self, prefixes):
        """Создаёт mock config с нужными TRIGGER_PREFIXES."""
        cfg = MagicMock()
        cfg.TRIGGER_PREFIXES = prefixes
        return cfg

    def test_trigger_prefix_krab(self):
        obj = self._mixin()
        with patch("src.config.config", self._make_config(["!краб", "@краб"])):
            assert obj._is_trigger("!краб привет") is True

    def test_trigger_starts_with_krab_literal(self):
        obj = self._mixin()
        with patch("src.config.config", self._make_config([])):
            assert obj._is_trigger("Краб, помоги") is True

    def test_no_trigger_regular_text(self):
        obj = self._mixin()
        with patch("src.config.config", self._make_config(["!краб"])):
            assert obj._is_trigger("обычное сообщение") is False

    def test_empty_text_is_not_trigger(self):
        obj = self._mixin()
        with patch("src.config.config", self._make_config(["!краб"])):
            assert obj._is_trigger("") is False

    def test_runtime_username_mention_is_trigger(self):
        """
        `@yung_nagato ...` должен будить Краба даже если username не прописали
        вручную в TRIGGER_PREFIXES. Это живой регресс How2AI: владелец пинговал
        userbot-аккаунт, а trigger guard молчал.
        """
        obj = self._mixin()
        obj.me = types.SimpleNamespace(username="yung_nagato")
        cfg = self._make_config([])
        cfg.OWNER_USERNAME = "@yung_nagato"
        with patch("src.config.config", cfg):
            assert obj._is_trigger("@yung_nagato защищай меня") is True
            assert obj._is_trigger("@yung_nagato, защищай меня") is True
            assert obj._is_trigger("@yung_nagatobot защищай меня") is False


class TestLLMTextProcessingMixinCleanText:
    """Очистка trigger/mention перед отправкой текста в LLM."""

    def _mixin(self):
        from src.userbot.access_control import AccessControlMixin
        from src.userbot.llm_text_processing import LLMTextProcessingMixin

        class FakeBot(AccessControlMixin, LLMTextProcessingMixin):
            pass

        obj = FakeBot.__new__(FakeBot)
        obj.me = types.SimpleNamespace(username="yung_nagato")
        return obj

    def test_runtime_username_mention_is_removed_from_prompt(self):
        """В модель должен уходить чистый запрос без `@username`."""
        obj = self._mixin()
        cfg = MagicMock()
        cfg.TRIGGER_PREFIXES = ["!краб", "@краб"]
        cfg.OWNER_USERNAME = "@yung_nagato"
        with patch("src.userbot.llm_text_processing.config", cfg):
            assert obj._get_clean_text("@yung_nagato защищай меня") == "защищай меня"
            assert obj._get_clean_text("@yung_nagato, защищай меня") == "защищай меня"
            assert (
                obj._get_clean_text("@yung_nagatobot защищай меня")
                == "@yung_nagatobot защищай меня"
            )


class TestAccessControlMixinIsNotificationSender:
    """Определение SMS/OTP shortcode-отправителей."""

    def _check(self, username="", phone=""):
        from src.userbot.access_control import AccessControlMixin

        user = MagicMock(username=username, phone=phone)
        return AccessControlMixin._is_notification_sender(user)

    def test_short_numeric_username(self):
        assert self._check(username="12345") is True

    def test_long_numeric_username_is_not_shortcode(self):
        assert self._check(username="123456") is False

    def test_alpha_username_is_not_shortcode(self):
        assert self._check(username="sberbank") is False

    def test_short_numeric_phone(self):
        assert self._check(phone="+7123") is True

    def test_normal_phone_is_not_shortcode(self):
        assert self._check(phone="+79991234567") is False


# ---------------------------------------------------------------------------
# RuntimeStatusMixin — static helpers
# ---------------------------------------------------------------------------


class TestRuntimeStatusMixinBuildModelStatus:
    """Формирование строки runtime-маршрута."""

    def _build(self, route: dict) -> str:
        from src.userbot.runtime_status import RuntimeStatusMixin

        return RuntimeStatusMixin._build_runtime_model_status(route)

    def test_openclaw_cloud_channel(self):
        result = self._build(
            {
                "channel": "openclaw_cloud",
                "model": "gemini-3-pro",
                "provider": "google",
                "active_tier": "cloud",
            }
        )
        assert "openclaw_cloud" in result
        assert "gemini-3-pro" in result

    def test_local_direct_channel(self):
        result = self._build(
            {
                "channel": "local_direct",
                "model": "llama3",
                "provider": "lmstudio",
                "active_tier": "-",
            }
        )
        assert "local_direct (LM Studio)" in result

    def test_unknown_channel_passthrough(self):
        result = self._build(
            {"channel": "custom_channel", "model": "x", "provider": "y", "active_tier": "?"}
        )
        assert "custom_channel" in result


class TestRuntimeStatusMixinLooksLikeQuestions:
    """Все detector-методы должны возвращать False (отключено по просьбе)."""

    def test_model_question_always_false(self):
        from src.userbot.runtime_status import RuntimeStatusMixin

        assert (
            RuntimeStatusMixin._looks_like_model_status_question("на какой модели работаешь?")
            is False
        )

    def test_capability_question_always_false(self):
        from src.userbot.runtime_status import RuntimeStatusMixin

        assert RuntimeStatusMixin._looks_like_capability_status_question("что ты умеешь?") is False

    def test_commands_question_always_false(self):
        from src.userbot.runtime_status import RuntimeStatusMixin

        assert RuntimeStatusMixin._looks_like_commands_question("какие команды?") is False

    def test_integrations_question_always_false(self):
        from src.userbot.runtime_status import RuntimeStatusMixin

        assert RuntimeStatusMixin._looks_like_integrations_question("какие интеграции?") is False


# ---------------------------------------------------------------------------
# SessionMixin — static error-classification helpers
# ---------------------------------------------------------------------------


class TestSessionMixinErrorClassification:
    """Классификация ошибок sqlite и auth key."""

    def test_sqlite_operational_disk_io(self):
        from src.userbot.session import SessionMixin

        exc = sqlite3.OperationalError("disk I/O error")
        assert SessionMixin._is_sqlite_io_error(exc) is True

    def test_sqlite_operational_locked(self):
        from src.userbot.session import SessionMixin

        exc = sqlite3.OperationalError("database is locked")
        assert SessionMixin._is_sqlite_io_error(exc) is True

    def test_sqlite_programming_closed_db(self):
        from src.userbot.session import SessionMixin

        exc = sqlite3.ProgrammingError("Cannot operate on a closed database.")
        assert SessionMixin._is_sqlite_io_error(exc) is True

    def test_non_sqlite_error_is_false(self):
        from src.userbot.session import SessionMixin

        exc = ValueError("something unrelated")
        assert SessionMixin._is_sqlite_io_error(exc) is False

    def test_auth_key_not_found(self):
        from src.userbot.session import SessionMixin

        exc = Exception("auth key not found")
        assert SessionMixin._is_auth_key_invalid(exc) is True

    def test_auth_key_unregistered(self):
        from src.userbot.session import SessionMixin

        exc = Exception("auth_key_unregistered error")
        assert SessionMixin._is_auth_key_invalid(exc) is True

    def test_db_locked(self):
        from src.userbot.session import SessionMixin

        exc = Exception("database is locked, retry later")
        assert SessionMixin._is_db_locked_error(exc) is True


# ---------------------------------------------------------------------------
# VoiceProfileMixin — нормализация параметров голоса
# ---------------------------------------------------------------------------


class _FakeVoiceBot:
    """Минимальный stub для тестирования VoiceProfileMixin."""

    _voice_delivery_modes = frozenset({"text+voice", "voice-only", "text-only"})

    # Атрибуты runtime
    voice_mode: bool = False
    voice_reply_speed: float = 1.5
    voice_reply_voice: str = "ru-RU-DmitryNeural"
    voice_reply_delivery: str = "text+voice"
    perceptor = None


# Подмешиваем mixin динамически
def _make_voice_obj() -> Any:
    from src.userbot.voice_profile import VoiceProfileMixin

    class FakeBot(_FakeVoiceBot, VoiceProfileMixin):
        pass

    return FakeBot()


class TestVoiceProfileMixinNormalize:
    """Нормализация скорости, голоса и режима доставки."""

    def test_speed_clamp_above_max(self):
        obj = _make_voice_obj()
        assert obj._normalize_voice_reply_speed(5.0) == 2.5

    def test_speed_clamp_below_min(self):
        obj = _make_voice_obj()
        assert obj._normalize_voice_reply_speed(0.1) == 0.75

    def test_speed_valid_value(self):
        obj = _make_voice_obj()
        assert obj._normalize_voice_reply_speed(1.2) == 1.2

    def test_speed_invalid_string_returns_default(self):
        obj = _make_voice_obj()
        assert obj._normalize_voice_reply_speed("fast") == 1.5

    def test_voice_empty_returns_default(self):
        obj = _make_voice_obj()
        assert obj._normalize_voice_reply_voice("") == "ru-RU-DmitryNeural"

    def test_voice_custom_value(self):
        obj = _make_voice_obj()
        assert obj._normalize_voice_reply_voice("en-US-AriaNeural") == "en-US-AriaNeural"

    def test_delivery_unknown_returns_default(self):
        obj = _make_voice_obj()
        assert obj._normalize_voice_reply_delivery("unknown-mode") == "text+voice"

    def test_delivery_voice_only(self):
        obj = _make_voice_obj()
        assert obj._normalize_voice_reply_delivery("voice-only") == "voice-only"


class TestVoiceProfileMixinMessageHasAudio:
    """Детекция audio/voice attachment в сообщении."""

    def test_voice_attachment(self):
        from src.userbot.voice_profile import VoiceProfileMixin

        msg = MagicMock(voice=MagicMock(), audio=None)
        assert VoiceProfileMixin._message_has_audio(msg) is True

    def test_audio_attachment(self):
        from src.userbot.voice_profile import VoiceProfileMixin

        msg = MagicMock(voice=None, audio=MagicMock())
        assert VoiceProfileMixin._message_has_audio(msg) is True

    def test_no_audio(self):
        from src.userbot.voice_profile import VoiceProfileMixin

        msg = MagicMock(voice=None, audio=None)
        assert VoiceProfileMixin._message_has_audio(msg) is False


class TestVoiceProfileMixinDownloadSuffix:
    """Подбор расширения для временного voice-файла."""

    def test_voice_returns_ogg(self):
        from src.userbot.voice_profile import VoiceProfileMixin

        msg = MagicMock(voice=MagicMock(), audio=None)
        assert VoiceProfileMixin._voice_download_suffix(msg) == ".ogg"

    def test_audio_with_named_file(self):
        from src.userbot.voice_profile import VoiceProfileMixin

        audio = MagicMock(file_name="track.mp3")
        msg = MagicMock(voice=None, audio=audio)
        assert VoiceProfileMixin._voice_download_suffix(msg) == ".mp3"

    def test_audio_without_file_name_returns_ogg(self):
        from src.userbot.voice_profile import VoiceProfileMixin

        audio = MagicMock(file_name="")
        msg = MagicMock(voice=None, audio=audio)
        assert VoiceProfileMixin._voice_download_suffix(msg) == ".ogg"
