# -*- coding: utf-8 -*-
"""
Wave 38: inline mention link для users без @username.

Issue: в YMB FAMILY FOREVER 09.05.2026 user "🐶" (без @username) был адресат
Krab'а ответа. Krab корректно начинал text c "🐶, ..." но это plain text,
не clickable. Tag должен указывать на user_id чтобы Telegram UI делал
mention navigable.

Helper `_inject_user_mention_link(text, user)` заменяет первое вхождение
display-name юзера на markdown link `[name](tg://user?id=N)`.

Pyrofork default parse_mode = markdown → link работает при стандартной
отправке через msg.reply().
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.userbot.delivery_helpers import _inject_user_mention_link

# ── basic: replace first occurrence of first_name ─────────────────────────────


def test_inject_mention_replaces_first_name_at_start() -> None:
    """Wave 38: text начинается с first_name юзера → replace с inline mention."""
    user = MagicMock(id=12345, username="", first_name="Алексей")

    result = _inject_user_mention_link("Алексей, держись", user)

    assert "[Алексей](tg://user?id=12345)" in result, (
        f"Должен заменить первое имя на link: {result}"
    )
    assert ", держись" in result, "Хвост text должен сохраниться"


def test_inject_mention_uses_username_when_present() -> None:
    """Wave 38: если username есть — приоритет на @username (не first_name)."""
    user = MagicMock(id=12345, username="lekha", first_name="Алексей")

    result = _inject_user_mention_link("@lekha, привет", user)

    assert "[@lekha](tg://user?id=12345)" in result, (
        f"@username имеет приоритет над first_name: {result}"
    )


def test_inject_mention_emoji_nickname() -> None:
    """Wave 38: emoji-only nickname как у 🐶 в YMB FAMILY FOREVER."""
    user = MagicMock(id=99999, username="", first_name="🐶")

    result = _inject_user_mention_link("🐶, теперь точно тебе", user)

    assert "[🐶](tg://user?id=99999)" in result, f"Emoji nickname должен стать clickable: {result}"


# ── idempotency / safety ──────────────────────────────────────────────────────


def test_inject_mention_idempotent_already_linked() -> None:
    """Wave 38: если text уже содержит link на user_id — не дублируем."""
    user = MagicMock(id=12345, username="", first_name="Алексей")

    already = "[Алексей](tg://user?id=12345), привет"
    result = _inject_user_mention_link(already, user)

    # Не должно быть double-wrapping
    assert result.count("tg://user?id=12345") == 1, f"Не должен дублировать link: {result}"


def test_inject_mention_no_user_id_returns_text_unchanged() -> None:
    """Wave 38: без user.id (например для anonymous channel) — текст не меняется."""
    user = MagicMock(id=None, username="", first_name="X")

    result = _inject_user_mention_link("X, тест", user)

    assert result == "X, тест", "Без user_id link невозможен"


def test_inject_mention_user_none_returns_text_unchanged() -> None:
    """Wave 38: user=None → возвращаем text как есть."""
    result = _inject_user_mention_link("Что-то", None)
    assert result == "Что-то"


def test_inject_mention_empty_text_returns_unchanged() -> None:
    """Wave 38: empty text → empty (or '...')."""
    user = MagicMock(id=12345, username="", first_name="X")

    assert _inject_user_mention_link("", user) == ""
    assert _inject_user_mention_link(None, user) is None


def test_inject_mention_name_not_at_start_unchanged() -> None:
    """Wave 38: имя не в начале text → не трогаем (избегаем false replacements
    в середине текста где это могло бы быть случайным совпадением)."""
    user = MagicMock(id=12345, username="", first_name="Алексей")

    text = "Привет всем, и Алексей тоже здесь"
    result = _inject_user_mention_link(text, user)

    # Не должны менять — name не в начале
    assert result == text, "Замена только при name в начале (avoid false positives)"


def test_inject_mention_first_name_substring_protection() -> None:
    """Wave 38: text начинается с substring похожего на name, но не точное —
    не должны trip'нуться (например first_name='Ан', text='Антон, ...')."""
    user = MagicMock(id=12345, username="", first_name="Ан")

    # Text начинается с "Антон," — first_name "Ан" — substring match risk.
    # Защита: смотрим что после name идёт `,`/`:`/` ` (word boundary).
    text = "Антон, привет"
    result = _inject_user_mention_link(text, user)

    # "Антон" начинается с "Ан" но это substring — мы НЕ должны заменить,
    # потому что после "Ан" идёт буква "т" (не word boundary).
    assert "[Ан](" not in result, f"Substring-match не должен срабатывать: {result}"


# ── integration smoke: helper reachable from delivery code ───────────────────


def test_inject_mention_used_in_deliver_response_parts() -> None:
    """Smoke: _inject_user_mention_link должен быть использован в
    _deliver_response_parts (Wave 38 integration с Wave 37-B redirect)."""
    import pathlib  # noqa: PLC0415

    src = pathlib.Path(__file__).parent.parent.parent / "src" / "userbot" / "delivery_helpers.py"
    text = src.read_text(encoding="utf-8")
    assert text.count("_inject_user_mention_link") >= 2, (
        "_inject_user_mention_link должен быть defined и вызываться в _deliver_response_parts"
    )
