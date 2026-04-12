# -*- coding: utf-8 -*-
"""
Юнит-тесты для чистых функций command_handlers.py.
Только pure function тесты — без Telegram клиента, без side effects.
"""

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _format_size_gb,
    _parse_toggle_arg,
    _render_translator_profile,
    _render_translator_session_state,
    _render_voice_profile,
    _split_text_for_telegram,
)

# ---------------------------------------------------------------------------
# _render_translator_profile
# ---------------------------------------------------------------------------


class TestRenderTranslatorProfile:
    """Тесты рендеринга профиля переводчика."""

    def test_defaults_отображаются_корректно(self):
        """Дефолтный профиль — все поля в норме."""
        profile = {
            "language_pair": "es-ru",
            "translation_mode": "bilingual",
            "voice_strategy": "voice-first",
            "target_device": "iphone_companion",
        }
        result = _render_translator_profile(profile)
        assert "es-ru" in result
        assert "bilingual" in result
        assert "voice-first" in result
        assert "iphone_companion" in result

    def test_пустой_профиль_использует_fallback_значения(self):
        """Пустой dict — функция не падает, использует дефолты."""
        result = _render_translator_profile({})
        assert "es-ru" in result  # дефолт language_pair
        assert "bilingual" in result  # дефолт mode
        assert "voice-first" in result  # дефолт strategy

    def test_флаги_включены(self):
        """Все boolean-флаги в True — отображаются как ВКЛ."""
        profile = {
            "ordinary_calls_enabled": True,
            "internet_calls_enabled": True,
            "subtitles_enabled": True,
            "timeline_enabled": True,
            "summary_enabled": True,
            "diagnostics_enabled": True,
        }
        result = _render_translator_profile(profile)
        assert result.count("ВКЛ") >= 6

    def test_флаги_выключены(self):
        """Все boolean-флаги в False — отображаются как ВЫКЛ."""
        profile = {
            "ordinary_calls_enabled": False,
            "internet_calls_enabled": False,
            "subtitles_enabled": False,
            "timeline_enabled": False,
            "summary_enabled": False,
            "diagnostics_enabled": False,
        }
        result = _render_translator_profile(profile)
        assert result.count("ВЫКЛ") >= 6

    def test_quick_phrases_preview(self):
        """Quick phrases — первые 3 попадают в preview."""
        profile = {"quick_phrases": ["Hola", "Buenos días", "Gracias", "Por favor"]}
        result = _render_translator_profile(profile)
        assert "Hola" in result
        assert "Buenos días" in result
        assert "Gracias" in result
        # четвёртая фраза не влезает в preview[:3]
        assert "Por favor" not in result

    def test_пустые_quick_phrases_показывают_прочерк(self):
        """Нет фраз — preview показывает прочерк."""
        result = _render_translator_profile({"quick_phrases": []})
        assert "Preview: —" in result

    def test_voice_foundation_ready(self):
        """voice_foundation_ready: True → READY, False → DEGRADED."""
        assert "READY" in _render_translator_profile({"voice_foundation_ready": True})
        assert "DEGRADED" in _render_translator_profile({"voice_foundation_ready": False})

    def test_voice_runtime_enabled(self):
        """voice_runtime_enabled влияет на строку Voice runtime replies."""
        on = _render_translator_profile({"voice_runtime_enabled": True})
        off = _render_translator_profile({"voice_runtime_enabled": False})
        # оба содержат, но разные значения
        assert "ВКЛ" in on
        assert "ВЫКЛ" in off

    def test_содержит_блок_команд(self):
        """Результат всегда содержит блок Команды."""
        result = _render_translator_profile({})
        assert "!translator status" in result


# ---------------------------------------------------------------------------
# _render_translator_session_state
# ---------------------------------------------------------------------------


class TestRenderTranslatorSessionState:
    """Тесты рендеринга session state переводчика."""

    def test_пустой_state_не_падает(self):
        """Пустой dict → функция возвращает строку с дефолтами."""
        result = _render_translator_session_state({})
        assert "idle" in result  # дефолт статус
        assert "session_idle" in result  # дефолт last_event

    def test_active_state(self):
        """Активная сессия — все поля отображаются."""
        state = {
            "session_status": "active",
            "translation_muted": False,
            "session_id": "sess-123",
            "active_session_label": "Madrid call",
            "language_pair": "es-ru",
            "last_event": "translation_received",
            "updated_at": "2026-04-12T10:00:00",
        }
        result = _render_translator_session_state(state)
        assert "active" in result
        assert "sess-123" in result
        assert "Madrid call" in result
        assert "es-ru" in result
        assert "translation_received" in result

    def test_muted_state(self):
        """translation_muted: True → YES, False → NO."""
        muted = _render_translator_session_state({"translation_muted": True})
        unmuted = _render_translator_session_state({"translation_muted": False})
        assert "YES" in muted
        assert "NO" in unmuted

    def test_timeline_summary_counts(self):
        """timeline_summary с total/line_events/control_events отображается."""
        state = {
            "timeline_summary": {
                "total": 42,
                "line_events": 30,
                "control_events": 12,
            }
        }
        result = _render_translator_session_state(state)
        assert "42" in result
        assert "30" in result
        assert "12" in result

    def test_timeline_preview_items(self):
        """Элементы timeline_preview отображаются (до 3 штук)."""
        state = {
            "timeline_preview": [
                {"kind": "translation_received", "ts": "10:00:01", "translation": "Hola → Привет"},
                {"kind": "session_started", "ts": "10:00:00", "translation": ""},
                {"kind": "translation_received", "ts": "10:00:05", "translation": "Adiós → Пока"},
                {"kind": "extra_event", "ts": "10:00:06", "translation": "overflow"},
            ]
        }
        result = _render_translator_session_state(state)
        assert "translation_received" in result
        assert "Привет" in result
        # четвёртый элемент за пределами [:3]
        assert "overflow" not in result

    def test_пустой_timeline_показывает_заглушку(self):
        """Пустой timeline_preview → заглушка 'timeline пока пуст'."""
        result = _render_translator_session_state({"timeline_preview": []})
        assert "timeline пока пуст" in result

    def test_содержит_блок_команд(self):
        """Результат всегда содержит блок Команды."""
        result = _render_translator_session_state({})
        assert "!translator session" in result


# ---------------------------------------------------------------------------
# _parse_toggle_arg
# ---------------------------------------------------------------------------


class TestParseToggleArg:
    """Тесты парсинга on/off аргументов."""

    def test_on_возвращает_True(self):
        assert _parse_toggle_arg("on", field_name="test") is True

    def test_off_возвращает_False(self):
        assert _parse_toggle_arg("off", field_name="test") is False

    def test_ON_uppercase(self):
        """Регистр не важен."""
        assert _parse_toggle_arg("ON", field_name="test") is True

    def test_OFF_uppercase(self):
        assert _parse_toggle_arg("OFF", field_name="test") is False

    def test_невалидный_аргумент_бросает_UserInputError(self):
        with pytest.raises(UserInputError):
            _parse_toggle_arg("yes", field_name="subtitles")

    def test_пустая_строка_бросает_UserInputError(self):
        with pytest.raises(UserInputError):
            _parse_toggle_arg("", field_name="voice")

    def test_None_бросает_UserInputError(self):
        with pytest.raises(UserInputError):
            _parse_toggle_arg(None, field_name="timeline")


# ---------------------------------------------------------------------------
# _format_size_gb
# ---------------------------------------------------------------------------


class TestFormatSizeGb:
    """Тесты форматирования размера модели."""

    def test_нормальное_значение(self):
        assert _format_size_gb(7.5) == "7.50 GB"

    def test_нулевое_значение(self):
        assert _format_size_gb(0.0) == "n/a"

    def test_отрицательное_значение(self):
        assert _format_size_gb(-1.0) == "n/a"

    def test_строковое_число(self):
        assert _format_size_gb("13.5") == "13.50 GB"

    def test_None_возвращает_na(self):
        assert _format_size_gb(None) == "n/a"

    def test_невалидная_строка_возвращает_na(self):
        assert _format_size_gb("unknown") == "n/a"


# ---------------------------------------------------------------------------
# _split_text_for_telegram
# ---------------------------------------------------------------------------


class TestSplitTextForTelegram:
    """Тесты разбивки длинных текстов на чанки для Telegram."""

    def test_короткий_текст_не_делится(self):
        text = "Hello world"
        result = _split_text_for_telegram(text)
        assert result == ["Hello world"]

    def test_длинный_текст_делится_по_границам_строк(self):
        # создаём текст > 3900 символов
        line = "A" * 200
        lines = [line] * 25  # 25 * 200 = 5000 символов
        text = "\n".join(lines)
        result = _split_text_for_telegram(text, limit=3900)
        assert len(result) > 1
        # каждый чанк не превышает лимит
        for chunk in result:
            assert len(chunk) <= 3900

    def test_пустая_строка(self):
        result = _split_text_for_telegram("")
        assert result == [""]

    def test_точно_по_лимиту(self):
        text = "X" * 100
        result = _split_text_for_telegram(text, limit=100)
        assert len(result) == 1

    def test_кастомный_лимит(self):
        text = "Line1\nLine2\nLine3"
        result = _split_text_for_telegram(text, limit=6)
        assert len(result) >= 2


# ---------------------------------------------------------------------------
# _render_voice_profile
# ---------------------------------------------------------------------------


class TestRenderVoiceProfile:
    """Тесты рендеринга voice profile."""

    def test_включённый_профиль(self):
        profile = {
            "enabled": True,
            "delivery": "voice-only",
            "speed": 1.0,
            "voice": "ru-RU-SvetlanaNeural",
        }
        result = _render_voice_profile(profile)
        assert "ВКЛ" in result
        assert "voice-only" in result
        assert "1.00x" in result
        assert "ru-RU-SvetlanaNeural" in result

    def test_выключённый_профиль(self):
        profile = {"enabled": False}
        result = _render_voice_profile(profile)
        assert "ВЫКЛ" in result

    def test_пустой_профиль_не_падает(self):
        """Пустой профиль → дефолтные значения."""
        result = _render_voice_profile({})
        assert "ВЫКЛ" in result  # enabled дефолт False
        assert "text+voice" in result

    def test_blocked_chats_preview(self):
        """Заблокированные чаты отображаются (до 5)."""
        profile = {"blocked_chats": [1, 2, 3, 4, 5, 6]}
        result = _render_voice_profile(profile)
        assert "+1" in result  # лишний чат

    def test_нет_blocked_chats_показывает_прочерк(self):
        result = _render_voice_profile({"blocked_chats": []})
        assert "—" in result

    def test_содержит_блок_команд(self):
        result = _render_voice_profile({})
        assert "!voice on" in result
