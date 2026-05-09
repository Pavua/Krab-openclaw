# -*- coding: utf-8 -*-
"""
Тесты Wave 37-B: anaphora detection helper.

Используется для двух исправлений:
1. Issue 1 (reply target): при reply на X + anaphora ("спроси его...") —
   reply_to ответа Krab'а = X, не trigger.
2. Issue 3 (anaphora prompt hint): подсказать LLM что местоимения
   относятся к автору referenced message.

Helper: src/userbot/delivery_helpers.py::_query_has_anaphora
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.userbot.delivery_helpers import _query_has_anaphora, _resolve_reply_target

# ── positive cases: должны детектиться как anaphora ───────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "Краб, спроси его как дела",
        "ответь ему по-человечески",
        "переведи её сообщение",
        "скажи ей что я перезвоню",
        "у него классная аватарка",
        "поздравь его с днём рождения",
        "Krab, ask him about Python",
        "tell her I said hi",
        "ЕГО спроси про матч",  # uppercase
        "Спроси про неё всё что можешь",
        "напиши ему в ответ",
    ],
)
def test_query_has_anaphora_detects_pronouns(query: str) -> None:
    """Anaphora pronouns должны детектироваться."""
    assert _query_has_anaphora(query), f"Должен задетектить anaphora pronoun в: {query!r}"


# ── negative cases: НЕ должны быть false positives ────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "Краб, расскажи о погоде",
        "какая сегодня дата?",
        "Например, есть отличный пример",  # "например" содержит "ему" как substring
        "Немецкий язык богат",  # "немец" не должен match
        "Гей-парад в Барселоне",  # word boundary — "гей" != "ей" (без пробела)
        "",
        "   ",
    ],
)
def test_query_has_anaphora_no_false_positives(query: str) -> None:
    """Слова внутри других слов / без anaphora — НЕ должны match."""
    assert not _query_has_anaphora(query), f"Не должен срабатывать на: {query!r}"


# ── edge cases ────────────────────────────────────────────────────────────────


def test_query_has_anaphora_handles_none() -> None:
    """None должен возвращать False, не падать."""
    assert _query_has_anaphora(None) is False


def test_query_has_anaphora_handles_empty_string() -> None:
    """Пустая строка → False."""
    assert _query_has_anaphora("") is False


def test_query_has_anaphora_with_punctuation() -> None:
    """Слово в окружении пунктуации детектится."""
    assert _query_has_anaphora("спроси, его?")
    assert _query_has_anaphora("(спроси его)")
    assert _query_has_anaphora('"скажи ему" — это просто')


def test_query_has_anaphora_word_boundary_strict() -> None:
    """'гей' не должно match'иться под 'ей'."""
    # "ей" без word boundary — но в "гей" нет boundary перед "ей"
    assert not _query_has_anaphora("гей")
    # А в "к ней" — boundary есть, должно match
    assert _query_has_anaphora("к ней обратись")


# ── _resolve_reply_target tests ──────────────────────────────────────────────


def test_resolve_reply_target_returns_referenced_when_anaphora() -> None:
    """Wave 37-B: reply на X + anaphora → reply_target = X (referenced)."""
    referenced_msg = MagicMock(id=12345)
    source_msg = MagicMock(reply_to_message=referenced_msg)

    target = _resolve_reply_target(source_msg, "Краб, спроси его")

    assert target is referenced_msg, (
        f"При anaphora reply_target должен быть referenced message, а не source ({target!r})"
    )


def test_resolve_reply_target_returns_source_when_no_anaphora() -> None:
    """Wave 37-B: reply на X без anaphora → reply_target = source_message."""
    referenced_msg = MagicMock(id=12345)
    source_msg = MagicMock(reply_to_message=referenced_msg)

    target = _resolve_reply_target(source_msg, "Краб, какая погода?")

    assert target is source_msg, "Без anaphora reply остаётся на trigger message"


def test_resolve_reply_target_no_referenced() -> None:
    """Wave 37-B: нет reply_to_message → reply_target = source_message
    (даже если anaphora есть в query)."""
    source_msg = MagicMock(reply_to_message=None)

    target = _resolve_reply_target(source_msg, "Краб, спроси его")

    assert target is source_msg, "Без referenced — fallback на source"


def test_resolve_reply_target_referenced_attr_missing() -> None:
    """Wave 37-B: если у message нет атрибута reply_to_message —
    защищённый fallback (getattr default)."""
    source_msg = MagicMock(spec=["id", "chat"])  # без reply_to_message атрибута

    target = _resolve_reply_target(source_msg, "Краб, спроси его")

    assert target is source_msg


def test_resolve_reply_target_used_in_deliver_response_parts() -> None:
    """Smoke: _resolve_reply_target должен быть использован в
    _deliver_response_parts (P1-3 integration)."""
    import pathlib  # noqa: PLC0415

    src = pathlib.Path(__file__).parent.parent.parent / "src" / "userbot" / "delivery_helpers.py"
    text = src.read_text(encoding="utf-8")
    assert text.count("_resolve_reply_target") >= 2, (
        "_resolve_reply_target должен быть определён И вызван хотя бы в _deliver_response_parts"
    )


# ── P1-5: anaphora prompt hint в build_segmented_prompt ──────────────────────


def test_build_segmented_prompt_adds_anaphora_hint() -> None:
    """Wave 37-B (P1-5): при reply + anaphora в current text → блок-подсказка
    с явным указанием на кого ссылаются местоимения."""
    from src.userbot.reply_preprocessor import ReplySegments, build_segmented_prompt

    segments = ReplySegments(
        reply_to_text="Привет, Краб, я Майк",
        reply_to_author="mike_account",
        mentions=[],
        current_text="Краб, спроси его как дела",
        has_reply=True,
    )
    result = build_segmented_prompt(
        segments=segments,
        sender_name="pavua",
        is_group=True,
    )

    assert "Контекст" in result and "mike_account" in result, (
        f"Должен быть hint с автором цитаты в результате:\n{result}"
    )
    assert "его" in result.lower() or "ему" in result.lower() or "её" in result.lower(), (
        "Hint должен упоминать pronouns которые resolved"
    )


def test_build_segmented_prompt_no_hint_without_anaphora() -> None:
    """Без anaphora pronouns в current text — подсказка не добавляется."""
    from src.userbot.reply_preprocessor import ReplySegments, build_segmented_prompt

    segments = ReplySegments(
        reply_to_text="Привет, Краб",
        reply_to_author="mike_account",
        mentions=[],
        current_text="Краб, расскажи про погоду",
        has_reply=True,
    )
    result = build_segmented_prompt(
        segments=segments,
        sender_name="pavua",
        is_group=True,
    )

    assert "Контекст: местоимения" not in result, (
        f"Без anaphora hint не должен появляться:\n{result}"
    )


def test_build_segmented_prompt_no_hint_without_reply() -> None:
    """Anaphora без reply — некого resolve'ить, hint не добавляется."""
    from src.userbot.reply_preprocessor import ReplySegments, build_segmented_prompt

    segments = ReplySegments(
        reply_to_text="",
        reply_to_author="",
        mentions=[],
        current_text="Краб, спроси его как дела",
        has_reply=False,
    )
    result = build_segmented_prompt(
        segments=segments,
        sender_name="pavua",
        is_group=True,
    )

    assert "Контекст: местоимения" not in result, "Без reply target hint не имеет смысла"


def test_build_segmented_prompt_no_hint_without_author() -> None:
    """Reply есть но author пустой (anonymous) → hint не добавляется
    (некого упомянуть)."""
    from src.userbot.reply_preprocessor import ReplySegments, build_segmented_prompt

    segments = ReplySegments(
        reply_to_text="Some message",
        reply_to_author="",  # пустой author
        mentions=[],
        current_text="спроси его",
        has_reply=True,
    )
    result = build_segmented_prompt(
        segments=segments,
        sender_name="pavua",
        is_group=True,
    )

    assert "Контекст: местоимения" not in result, "Без known author hint не имеет смысла"
