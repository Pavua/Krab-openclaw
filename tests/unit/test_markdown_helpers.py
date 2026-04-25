"""Тесты markdown-escape helper и parse-error detection."""

from __future__ import annotations

import pytest

from src.core.markdown_escape import escape_markdown, looks_like_parse_error


def test_escape_markdown_basic():
    assert escape_markdown("hello") == "hello"
    assert escape_markdown("**bold**") == "\\*\\*bold\\*\\*"


def test_escape_markdown_underscore():
    # `_` и `.` — оба спецсимвола в markdown
    assert escape_markdown("file_name.py") == "file\\_name\\.py"


def test_escape_markdown_empty():
    assert escape_markdown("") == ""
    assert escape_markdown(None) is None


def test_escape_markdown_all_specials():
    src = "_*[]()~`>#+-=|{}.!"
    out = escape_markdown(src)
    # Каждый символ экранирован
    assert out is not None
    for ch in src:
        assert "\\" + ch in out
    assert len(out) == len(src) * 2


def test_escape_markdown_unicode():
    # Русский текст и emoji не затрагиваются
    assert escape_markdown("привет мир") == "привет мир"
    assert escape_markdown("🦀 краб") == "🦀 краб"


def test_escape_markdown_mixed():
    # "path/file.py: error!" — `.` и `!` экранируются, остальное нетронуто
    out = escape_markdown("path/file.py: error!")
    assert out == "path/file\\.py: error\\!"


def test_looks_like_parse_error_positive():
    # Типичные сообщения Telegram при битой разметке
    assert looks_like_parse_error(Exception("Can't parse entities"))
    assert looks_like_parse_error(Exception("unclosed entity at byte 42"))
    assert looks_like_parse_error(Exception("BAD_REQUEST: MESSAGE_PARSE_FAILED"))
    assert looks_like_parse_error(Exception("unsupported start tag"))
    assert looks_like_parse_error(Exception("markdown error"))


def test_looks_like_parse_error_negative():
    # Flood/timeout/auth/прочее — не парс-ошибка
    assert not looks_like_parse_error(Exception("FLOOD_WAIT_42"))
    assert not looks_like_parse_error(Exception("CHAT_WRITE_FORBIDDEN"))
    assert not looks_like_parse_error(Exception(""))
    assert not looks_like_parse_error(Exception("timeout"))


# Integration: verify _safe_edit/_safe_reply pass parse_mode через queue.
@pytest.mark.skip(
    reason="Wave 11: parse_mode markdown-default + auto-fallback не реализованы; future feature"
)
@pytest.mark.asyncio
async def test_safe_edit_passes_parse_mode_by_default():
    """_safe_edit по умолчанию передаёт parse_mode=MARKDOWN в msg.edit."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pyrogram.enums import ParseMode

    from src.userbot_bridge import KraabUserbot

    bridge = KraabUserbot.__new__(KraabUserbot)
    captured: dict = {}

    async def _edit(text, parse_mode=None):
        captured["text"] = text
        captured["parse_mode"] = parse_mode
        return SimpleNamespace(text=text, chat=SimpleNamespace(id=1))

    msg = SimpleNamespace(
        text="old",
        caption=None,
        chat=SimpleNamespace(id=1),
        id=42,
        edit=_edit,
    )

    # Замокаем client (не используется в happy-path).
    bridge.client = AsyncMock()

    result = await bridge._safe_edit(msg, "**hello**")

    assert captured["text"] == "**hello**"
    assert captured["parse_mode"] == ParseMode.MARKDOWN
    assert result.text == "**hello**"


@pytest.mark.skip(
    reason="Wave 11: parse_mode auto-fallback не реализован в _safe_edit; future feature"
)
@pytest.mark.asyncio
async def test_safe_edit_fallback_plain_on_parse_error():
    """При parse-ошибке _safe_edit пробует plain-text edit."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.userbot_bridge import KraabUserbot

    bridge = KraabUserbot.__new__(KraabUserbot)
    call_log: list[tuple] = []

    async def _edit(text, parse_mode=None):
        call_log.append(("edit", text, parse_mode))
        # Первый вызов (markdown) — падает с parse error.
        # Второй (plain) — успех.
        if parse_mode is not None:
            raise Exception("Can't parse entities: unclosed bold at byte 5")
        return SimpleNamespace(text=text, chat=SimpleNamespace(id=1))

    msg = SimpleNamespace(
        text="old",
        caption=None,
        chat=SimpleNamespace(id=1),
        id=42,
        edit=_edit,
    )
    bridge.client = AsyncMock()

    result = await bridge._safe_edit(msg, "**broken")

    # Первый вызов — MARKDOWN, второй — None (plain).
    assert len(call_log) == 2
    assert call_log[0][2] is not None  # markdown
    assert call_log[1][2] is None  # plain
    assert result.text == "**broken"


@pytest.mark.skip(
    reason="Wave 11: parse_mode auto-fallback не реализован в _safe_reply_or_send_new"
)
@pytest.mark.asyncio
async def test_safe_reply_fallback_plain_on_parse_error():
    """При parse-ошибке _safe_reply_or_send_new пробует plain-text reply."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.userbot_bridge import KraabUserbot

    bridge = KraabUserbot.__new__(KraabUserbot)
    call_log: list[tuple] = []

    async def _reply(text, parse_mode=None):
        call_log.append(("reply", text, parse_mode))
        if parse_mode is not None:
            raise Exception("can't parse entities")
        return SimpleNamespace(text=text, chat=SimpleNamespace(id=1))

    msg = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        id=42,
        reply=_reply,
    )
    bridge.client = AsyncMock()

    result = await bridge._safe_reply_or_send_new(msg, "**broken")

    # Два вызова reply: сначала markdown (упал), потом plain (успех).
    assert len(call_log) == 2
    assert call_log[0][2] is not None
    assert call_log[1][2] is None
    assert result.text == "**broken"
