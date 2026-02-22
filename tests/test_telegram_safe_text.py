# -*- coding: utf-8 -*-
"""
Тесты безопасного извлечения текста из Telegram Message.
"""

from src.utils.telegram_safe_text import extract_message_text_safe


class _BrokenTextMessage:
    """Сообщение, где text ломается, но caption доступен."""

    @property
    def text(self):
        raise UnicodeDecodeError("utf-16-le", b"\x00\x00", 0, 2, "bad surrogate")

    @property
    def caption(self):
        return "caption-ok"


class _BrokenTextAndCaptionMessage:
    """Сообщение, где оба поля недоступны."""

    @property
    def text(self):
        raise UnicodeDecodeError("utf-16-le", b"\x00\x00", 0, 2, "bad surrogate")

    @property
    def caption(self):
        raise UnicodeDecodeError("utf-16-le", b"\x00\x00", 0, 2, "bad surrogate")


def test_extract_message_text_safe_uses_caption_when_text_broken():
    """При падении text helper должен корректно вернуть caption."""
    payload = extract_message_text_safe(_BrokenTextMessage(), fallback_label="Text")
    assert payload == "caption-ok"


def test_extract_message_text_safe_returns_fallback_when_all_sources_broken():
    """При полной деградации text/caption helper возвращает fallback-маркер."""
    payload = extract_message_text_safe(_BrokenTextAndCaptionMessage(), fallback_label="Voice")
    assert payload == "[Voice]"

