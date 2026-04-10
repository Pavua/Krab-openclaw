# -*- coding: utf-8 -*-
"""
Тесты VoiceProfileMixin для userbot.

Покрываем:
1) нормализацию speed/voice/delivery (clamp, fallback);
2) get_voice_runtime_profile — структура, значения перцептора;
3) update_voice_runtime_profile — обновление полей, persist-флаг;
4) _should_send_voice_reply / _should_send_full_text_reply / delivery logic;
5) _message_has_audio — детекция voice/audio вложений;
6) _voice_download_suffix — расширение для временного файла;
7) per-chat blocklist тесты намеренно НЕ дублируем — они в test_userbot_voice_blocklist.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.userbot_bridge as userbot_bridge_module
from src.userbot_bridge import KraabUserbot


def _make_bot_stub() -> KraabUserbot:
    """Минимальный stub для тестов VoiceProfileMixin."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=777, username="owner")
    bot.voice_mode = False
    bot.voice_reply_speed = 1.5
    bot.voice_reply_voice = "ru-RU-DmitryNeural"
    bot.voice_reply_delivery = "text+voice"
    bot.perceptor = None
    return bot


@pytest.fixture(autouse=True)
def _patch_update_setting(monkeypatch):
    """
    Заменяет config.update_setting на in-memory реализацию, чтобы тесты
    не трогали реальный .env файл. Та же техника, что в test_userbot_voice_blocklist.
    """
    target_cls = userbot_bridge_module.config.__class__
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        target_cls,
        "update_setting",
        classmethod(lambda cls, key, val: calls.append((key, val)) or True),
    )
    return calls


# ---------------------------------------------------------------------------
# Нормализация speed
# ---------------------------------------------------------------------------


def test_normalize_speed_clamp_upper() -> None:
    """Значения выше 2.5 зажимаются в 2.5."""
    assert KraabUserbot._normalize_voice_reply_speed(5.0) == 2.5
    assert KraabUserbot._normalize_voice_reply_speed(999) == 2.5


def test_normalize_speed_clamp_lower() -> None:
    """Значения ниже 0.75 зажимаются в 0.75."""
    assert KraabUserbot._normalize_voice_reply_speed(0.1) == 0.75
    assert KraabUserbot._normalize_voice_reply_speed(-1) == 0.75


def test_normalize_speed_valid_range() -> None:
    """Корректные значения в диапазоне возвращаются с округлением до 2 знаков."""
    assert KraabUserbot._normalize_voice_reply_speed(1.5) == 1.5
    assert KraabUserbot._normalize_voice_reply_speed(2.0) == 2.0
    assert KraabUserbot._normalize_voice_reply_speed(1.234) == 1.23


def test_normalize_speed_non_numeric_returns_default() -> None:
    """Нечисловое значение → 1.5 (значение по умолчанию)."""
    assert KraabUserbot._normalize_voice_reply_speed("badvalue") == 1.5
    assert KraabUserbot._normalize_voice_reply_speed(None) == 1.5


# ---------------------------------------------------------------------------
# Нормализация voice id
# ---------------------------------------------------------------------------


def test_normalize_voice_valid_id() -> None:
    """Корректный voice id возвращается без изменений."""
    assert KraabUserbot._normalize_voice_reply_voice("ru-RU-SvetlanaNeural") == "ru-RU-SvetlanaNeural"


def test_normalize_voice_empty_returns_default() -> None:
    """Пустая строка → дефолтный голос ru-RU-DmitryNeural."""
    assert KraabUserbot._normalize_voice_reply_voice("") == "ru-RU-DmitryNeural"
    assert KraabUserbot._normalize_voice_reply_voice(None) == "ru-RU-DmitryNeural"


def test_normalize_voice_strips_whitespace() -> None:
    """Пробелы вокруг voice id обрезаются."""
    assert KraabUserbot._normalize_voice_reply_voice("  ru-RU-DmitryNeural  ") == "ru-RU-DmitryNeural"


# ---------------------------------------------------------------------------
# Нормализация delivery mode
# ---------------------------------------------------------------------------


def test_normalize_delivery_valid_modes() -> None:
    """Поддерживаемые режимы: text+voice и voice-only."""
    bot = _make_bot_stub()
    assert bot._normalize_voice_reply_delivery("text+voice") == "text+voice"
    assert bot._normalize_voice_reply_delivery("voice-only") == "voice-only"


def test_normalize_delivery_invalid_falls_back_to_default() -> None:
    """Неизвестный режим → text+voice."""
    bot = _make_bot_stub()
    assert bot._normalize_voice_reply_delivery("unknown") == "text+voice"
    assert bot._normalize_voice_reply_delivery("") == "text+voice"
    assert bot._normalize_voice_reply_delivery(None) == "text+voice"


# ---------------------------------------------------------------------------
# get_voice_runtime_profile
# ---------------------------------------------------------------------------


def test_get_voice_runtime_profile_structure() -> None:
    """Профиль содержит все обязательные ключи."""
    bot = _make_bot_stub()
    profile = bot.get_voice_runtime_profile()
    for key in ("enabled", "delivery", "speed", "voice", "input_transcription_ready", "output_tts_ready", "live_voice_foundation", "blocked_chats"):
        assert key in profile, f"ключ '{key}' отсутствует в voice profile"


def test_get_voice_runtime_profile_perceptor_none_transcription_not_ready() -> None:
    """Без perceptor → input_transcription_ready=False."""
    bot = _make_bot_stub()
    bot.perceptor = None
    profile = bot.get_voice_runtime_profile()
    assert profile["input_transcription_ready"] is False
    assert profile["live_voice_foundation"] is False


def test_get_voice_runtime_profile_perceptor_ready() -> None:
    """С perceptor, имеющим метод transcribe → input_transcription_ready=True."""
    bot = _make_bot_stub()
    bot.perceptor = SimpleNamespace(transcribe=lambda x: x)
    profile = bot.get_voice_runtime_profile()
    assert profile["input_transcription_ready"] is True
    assert profile["live_voice_foundation"] is True


def test_get_voice_runtime_profile_output_tts_always_ready() -> None:
    """output_tts_ready всегда True (edge-tts встроен)."""
    bot = _make_bot_stub()
    profile = bot.get_voice_runtime_profile()
    assert profile["output_tts_ready"] is True


def test_get_voice_runtime_profile_reflects_current_state() -> None:
    """Профиль отражает текущее состояние voice_mode."""
    bot = _make_bot_stub()
    bot.voice_mode = True
    assert bot.get_voice_runtime_profile()["enabled"] is True
    bot.voice_mode = False
    assert bot.get_voice_runtime_profile()["enabled"] is False


# ---------------------------------------------------------------------------
# update_voice_runtime_profile
# ---------------------------------------------------------------------------


def test_update_voice_profile_sets_enabled(_patch_update_setting) -> None:
    """update с enabled=True включает голос и может сохранить в .env."""
    bot = _make_bot_stub()
    calls = _patch_update_setting
    result = bot.update_voice_runtime_profile(enabled=True, persist=True)
    assert bot.voice_mode is True
    assert result["enabled"] is True
    assert any(k == "VOICE_MODE_DEFAULT" for k, _ in calls)


def test_update_voice_profile_sets_speed() -> None:
    """update speed нормализует и сохраняет значение."""
    bot = _make_bot_stub()
    result = bot.update_voice_runtime_profile(speed=2.2, persist=False)
    assert bot.voice_reply_speed == 2.2
    assert result["speed"] == 2.2


def test_update_voice_profile_sets_voice() -> None:
    """update voice обновляет voice_reply_voice."""
    bot = _make_bot_stub()
    bot.update_voice_runtime_profile(voice="ru-RU-SvetlanaNeural", persist=False)
    assert bot.voice_reply_voice == "ru-RU-SvetlanaNeural"


def test_update_voice_profile_sets_delivery() -> None:
    """update delivery обновляет режим доставки."""
    bot = _make_bot_stub()
    bot.update_voice_runtime_profile(delivery="voice-only", persist=False)
    assert bot.voice_reply_delivery == "voice-only"


def test_update_voice_profile_no_persist_skips_config_update(_patch_update_setting) -> None:
    """persist=False → config.update_setting не вызывается."""
    bot = _make_bot_stub()
    calls = _patch_update_setting
    bot.update_voice_runtime_profile(enabled=True, speed=2.0, persist=False)
    assert calls == []


def test_update_voice_profile_returns_profile_dict() -> None:
    """update возвращает актуальный dict профиля, не None."""
    bot = _make_bot_stub()
    result = bot.update_voice_runtime_profile(enabled=True, persist=False)
    assert isinstance(result, dict)
    assert "enabled" in result


# ---------------------------------------------------------------------------
# Voice delivery decisions
# ---------------------------------------------------------------------------


def test_should_send_voice_reply_true_when_voice_mode_on() -> None:
    """voice_mode=True → отправлять голос."""
    bot = _make_bot_stub()
    bot.voice_mode = True
    assert bot._should_send_voice_reply() is True


def test_should_send_voice_reply_false_when_voice_mode_off() -> None:
    """voice_mode=False → не отправлять голос."""
    bot = _make_bot_stub()
    bot.voice_mode = False
    assert bot._should_send_voice_reply() is False


def test_should_send_full_text_reply_true_when_voice_off() -> None:
    """Без голоса всегда шлём полный текст."""
    bot = _make_bot_stub()
    bot.voice_mode = False
    assert bot._should_send_full_text_reply() is True


def test_should_send_full_text_reply_false_when_voice_only() -> None:
    """В режиме voice-only текстовый дубль не нужен."""
    bot = _make_bot_stub()
    bot.voice_mode = True
    bot.voice_reply_delivery = "voice-only"
    assert bot._should_send_full_text_reply() is False


def test_should_send_full_text_reply_true_when_text_plus_voice() -> None:
    """В режиме text+voice текстовый дубль нужен."""
    bot = _make_bot_stub()
    bot.voice_mode = True
    bot.voice_reply_delivery = "text+voice"
    assert bot._should_send_full_text_reply() is True


# ---------------------------------------------------------------------------
# Audio detection
# ---------------------------------------------------------------------------


def test_message_has_audio_voice_attachment() -> None:
    """Сообщение с voice → True."""
    msg = SimpleNamespace(voice=object(), audio=None)
    assert KraabUserbot._message_has_audio(msg) is True


def test_message_has_audio_audio_attachment() -> None:
    """Сообщение с audio → True."""
    msg = SimpleNamespace(voice=None, audio=object())
    assert KraabUserbot._message_has_audio(msg) is True


def test_message_has_audio_no_attachment() -> None:
    """Текстовое сообщение без вложений → False."""
    msg = SimpleNamespace(voice=None, audio=None)
    assert KraabUserbot._message_has_audio(msg) is False


# ---------------------------------------------------------------------------
# _voice_download_suffix
# ---------------------------------------------------------------------------


def test_voice_download_suffix_voice_message_is_ogg() -> None:
    """Голосовое сообщение (voice) → .ogg."""
    msg = SimpleNamespace(voice=object(), audio=None)
    assert KraabUserbot._voice_download_suffix(msg) == ".ogg"


def test_voice_download_suffix_audio_with_extension() -> None:
    """Аудио с известным расширением → берёт расширение из file_name."""
    msg = SimpleNamespace(voice=None, audio=SimpleNamespace(file_name="track.mp3"))
    assert KraabUserbot._voice_download_suffix(msg) == ".mp3"


def test_voice_download_suffix_audio_without_extension_falls_back_to_ogg() -> None:
    """Аудио без расширения в file_name → .ogg по умолчанию."""
    msg = SimpleNamespace(voice=None, audio=SimpleNamespace(file_name=""))
    assert KraabUserbot._voice_download_suffix(msg) == ".ogg"


def test_voice_download_suffix_no_audio_at_all_is_ogg() -> None:
    """Ни voice, ни audio → .ogg по умолчанию."""
    msg = SimpleNamespace(voice=None, audio=None)
    assert KraabUserbot._voice_download_suffix(msg) == ".ogg"


def test_voice_download_suffix_adds_dot_if_missing() -> None:
    """Если суффикс без точки (теоретически) — метод должен добавить её."""
    # file_name с явным расширением .wav
    msg = SimpleNamespace(voice=None, audio=SimpleNamespace(file_name="audio.wav"))
    suffix = KraabUserbot._voice_download_suffix(msg)
    assert suffix.startswith(".")
    assert "wav" in suffix
