# -*- coding: utf-8 -*-
"""
Wave 39-X: output-based reply target redirect (regression fix).

Wave 37-B (P1-3) использовал anaphora pronouns (его/ему/её/ей) как proxy для
"user references third party". Узкое решение — не покрывает случаи когда user
не использует pronouns:

09.05.2026 02:13-02:14 в YMB FAMILY FOREVER:
- pavua: "Краб, поправил, попробуй снова" (reply на 🐶's join service msg)
  — НЕТ anaphora pronouns
- Krab generated: "🐶, контрольный пинг 🦀 ..." (правильно адресует 🐶)
- Wave 37-B anaphora regex miss → reply_to = pavua (wrong)
- pavua: "бля, ты снова мне ответил"

True signal где Krab адресует — это **сам outgoing text**. LLM умный, начинает
ответ с "🐶, ..." или "@user, ..." когда хочет address кого-то конкретно.
Helper парсит начало text и matches против participant в reply chain.

Wave 37-B остаётся как fallback (если LLM начинает прямо с ответа).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.userbot.delivery_helpers import _resolve_reply_target_from_output

# ── основной regression case (скриншот 09.05.2026 02:14) ─────────────────────


def test_output_starts_with_emoji_nickname_redirects_to_referenced() -> None:
    """Wave 39-X regression case: response="🐶, контрольный пинг..." +
    referenced.from_user.first_name="🐶" → redirect на referenced."""
    referenced_user = MagicMock(id=99999, username="", first_name="🐶")
    referenced_msg = MagicMock(id=767199, from_user=referenced_user)
    source_msg = MagicMock(id=767211, reply_to_message=referenced_msg)

    response = "🐶, контрольный пинг 🦀\n\nТеперь пробую уже в правильный reply-target..."

    result = _resolve_reply_target_from_output(
        source_msg, response, fallback_query="Краб, поправил, попробуй снова"
    )

    assert result is referenced_msg, (
        "Output starts with '🐶,' и referenced user имеет first_name='🐶' — "
        "должен redirect на referenced. Это regression case из YMB."
    )


def test_output_starts_with_username_redirects() -> None:
    """Wave 39-X: response='@lekha, привет' + referenced.username='lekha' → redirect."""
    referenced_user = MagicMock(id=12345, username="lekha", first_name="Алексей")
    referenced_msg = MagicMock(id=765961, from_user=referenced_user)
    source_msg = MagicMock(id=765960, reply_to_message=referenced_msg)

    response = "@lekha, держись, брат"

    result = _resolve_reply_target_from_output(source_msg, response)

    assert result is referenced_msg


def test_output_starts_with_first_name_redirects() -> None:
    """Wave 39-X: response='Алексей, держись' + referenced.first_name='Алексей'."""
    referenced_user = MagicMock(id=12345, username="", first_name="Алексей")
    referenced_msg = MagicMock(id=765961, from_user=referenced_user)
    source_msg = MagicMock(id=765960, reply_to_message=referenced_msg)

    response = "Алексей, держись, брат. Скоро домой."

    result = _resolve_reply_target_from_output(source_msg, response)

    assert result is referenced_msg


def test_output_starts_with_markdown_link_redirects() -> None:
    """Wave 39-X: response уже содержит [name](tg://user?id=N) — это explicit address."""
    referenced_user = MagicMock(id=99999, username="", first_name="🐶")
    referenced_msg = MagicMock(id=767199, from_user=referenced_user)
    source_msg = MagicMock(id=767211, reply_to_message=referenced_msg)

    response = "[🐶](tg://user?id=99999), привет"

    result = _resolve_reply_target_from_output(source_msg, response)

    assert result is referenced_msg


# ── fallback to Wave 37-B anaphora ────────────────────────────────────────────


def test_output_without_address_falls_back_to_anaphora_redirect() -> None:
    """Wave 39-X fallback: response не starts с addressee, но fallback_query содержит
    anaphora → Wave 37-B redirect срабатывает."""
    referenced_user = MagicMock(id=99999, username="", first_name="🐶")
    referenced_msg = MagicMock(id=767199, from_user=referenced_user)
    source_msg = MagicMock(id=767211, reply_to_message=referenced_msg)

    response = "Понял, секунду"  # не начинается с addressee

    result = _resolve_reply_target_from_output(
        source_msg, response, fallback_query="Краб, спроси его"
    )

    assert result is referenced_msg, (
        "Wave 37-B anaphora должен работать как fallback когда output silent"
    )


def test_output_without_address_and_no_anaphora_returns_source() -> None:
    """Wave 39-X: ни addressee в output, ни anaphora в query → reply на trigger (default)."""
    referenced_user = MagicMock(id=99999, username="", first_name="🐶")
    referenced_msg = MagicMock(id=767199, from_user=referenced_user)
    source_msg = MagicMock(id=767211, reply_to_message=referenced_msg)

    response = "Спасибо, готово"
    result = _resolve_reply_target_from_output(source_msg, response, fallback_query="Краб, спасибо")

    assert result is source_msg, (
        "Без addressee в output и без anaphora — оставляем default reply на trigger"
    )


# ── safety: false-positive protection ────────────────────────────────────────


def test_output_addressee_does_not_match_referenced_returns_source() -> None:
    """Wave 39-X: response='Pavel, ...' но referenced.from_user.first_name='🐶' —
    не false-redirect (Pavel != 🐶)."""
    referenced_user = MagicMock(id=99999, username="", first_name="🐶")
    referenced_msg = MagicMock(id=767199, from_user=referenced_user)
    source_msg = MagicMock(id=767211, reply_to_message=referenced_msg)

    response = "Pavel, спасибо за наводку"

    result = _resolve_reply_target_from_output(source_msg, response)

    # Pavel != 🐶 — не должен redirect, и в fallback тоже нет anaphora
    assert result is source_msg


def test_no_referenced_message_returns_source() -> None:
    """Wave 39-X: source без reply_to → fallback (всегда source)."""
    source_msg = MagicMock(id=767211, reply_to_message=None)

    response = "🐶, привет"

    result = _resolve_reply_target_from_output(source_msg, response)

    assert result is source_msg, "Без referenced нечего redirect'ить"


def test_no_referenced_user_returns_source() -> None:
    """Wave 39-X: referenced без from_user (anonymous channel) → source."""
    referenced_msg = MagicMock(id=767199, from_user=None)
    source_msg = MagicMock(id=767211, reply_to_message=referenced_msg)

    response = "🐶, привет"

    result = _resolve_reply_target_from_output(source_msg, response)

    assert result is source_msg


def test_empty_response_falls_back_to_anaphora() -> None:
    """Wave 39-X: response None/пустой → fallback на anaphora."""
    referenced_user = MagicMock(id=99999, username="", first_name="🐶")
    referenced_msg = MagicMock(id=767199, from_user=referenced_user)
    source_msg = MagicMock(id=767211, reply_to_message=referenced_msg)

    # Empty response, fallback_query с anaphora → Wave 37-B redirect
    result = _resolve_reply_target_from_output(source_msg, "", fallback_query="Краб, спроси его")
    assert result is referenced_msg

    # Empty response, no anaphora → source
    result = _resolve_reply_target_from_output(source_msg, None, fallback_query="что-то")
    assert result is source_msg


# ── word boundary protection ──────────────────────────────────────────────────


def test_first_name_substring_does_not_falsely_match() -> None:
    """Wave 39-X: response='Антон, ...' с referenced.first_name='Ан' (substring)
    не должен срабатывать."""
    referenced_user = MagicMock(id=12345, username="", first_name="Ан")
    referenced_msg = MagicMock(id=100, from_user=referenced_user)
    source_msg = MagicMock(id=200, reply_to_message=referenced_msg)

    response = "Антон, привет"

    result = _resolve_reply_target_from_output(source_msg, response)

    # "Антон" начинается с "Ан" но это substring (буква 'т' после) — не word boundary
    assert result is source_msg, "Substring-match должен быть отклонён через word boundary"


# ── integration smoke ─────────────────────────────────────────────────────────


def test_used_in_deliver_response_parts() -> None:
    """Smoke: _resolve_reply_target_from_output used в delivery."""
    import pathlib  # noqa: PLC0415

    src = pathlib.Path(__file__).parent.parent.parent / "src" / "userbot" / "delivery_helpers.py"
    text = src.read_text(encoding="utf-8")
    assert text.count("_resolve_reply_target_from_output") >= 2, (
        "_resolve_reply_target_from_output должен быть defined и used в _deliver_response_parts"
    )
