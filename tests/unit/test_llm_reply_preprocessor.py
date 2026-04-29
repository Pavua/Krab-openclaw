# -*- coding: utf-8 -*-
"""Тесты для src/userbot/reply_preprocessor.py — Bug 3 (cont.) + Bug 10."""
from __future__ import annotations

from src.userbot.reply_preprocessor import (
    build_segmented_prompt,
    extract_reply_segments,
    has_persona_mention_in_reply_to,
)


def _user(username: str = "", first_name: str = "", uid: int = 0) -> dict:
    return {"username": username, "first_name": first_name, "id": uid}


def _msg(
    text: str = "",
    caption: str = "",
    reply_to: dict | None = None,
    from_user: dict | None = None,
) -> dict:
    return {
        "text": text,
        "caption": caption,
        "reply_to_message": reply_to,
        "from_user": from_user,
    }


# ---------------------------------------------------------------------------
# extract_reply_segments
# ---------------------------------------------------------------------------


def test_full_reply_to_text_preserved_no_truncation() -> None:
    """Bug 3: длинный reply_to НЕ обрезается preprocessor'ом."""
    long_quote = "А" * 2000 + " — финальный вопрос: что делать?"
    reply = _msg(text=long_quote, from_user=_user(username="callme_chado"))
    msg = _msg(text="@yung_nagato подскажи", reply_to=reply)

    segments = extract_reply_segments(msg)

    assert segments.has_reply is True
    assert segments.reply_to_text == long_quote
    assert len(segments.reply_to_text) > 1500  # не порезали
    assert segments.reply_to_author == "callme_chado"


def test_mentions_extracted_from_reply_to_body() -> None:
    """Bug 10: @yung_nagato в теле цитируемого сообщения попадает в mentions."""
    reply = _msg(text="Пиши @yung_nagato — он быстрее ответит", from_user=_user("alice"))
    msg = _msg(text="а как именно?", reply_to=reply)

    segments = extract_reply_segments(msg)

    assert "yung_nagato" in segments.mentions


def test_mentions_dedup_and_order() -> None:
    """Mentions из reply+current собираются вместе, без дублей, в порядке появления."""
    reply = _msg(text="@yung_nagato @callme_chado, кто ответит?")
    msg = _msg(text="@yung_nagato да-да", reply_to=reply)

    segments = extract_reply_segments(msg)

    # yung_nagato первым (из reply), затем callme_chado, дубль из current не добавляем
    assert segments.mentions == ["yung_nagato", "callme_chado"]


def test_no_reply_does_not_break() -> None:
    """Без reply_to preprocessor работает корректно."""
    msg = _msg(text="@yung_nagato как дела?")

    segments = extract_reply_segments(msg)

    assert segments.has_reply is False
    assert segments.reply_to_text == ""
    assert "yung_nagato" in segments.mentions
    assert segments.current_text == "@yung_nagato как дела?"


def test_caption_used_when_text_empty() -> None:
    """Если у reply_to нет text — берём caption."""
    reply = _msg(text="", caption="Фото-цитата с @yung_nagato")
    msg = _msg(text="что скажешь?", reply_to=reply)

    segments = extract_reply_segments(msg)

    assert segments.has_reply is True
    assert "yung_nagato" in segments.mentions


# ---------------------------------------------------------------------------
# build_segmented_prompt
# ---------------------------------------------------------------------------


def test_segmented_prompt_has_explicit_blocks() -> None:
    reply = _msg(text="Длинная цитата про @yung_nagato и AI", from_user=_user("alice"))
    msg = _msg(text="как считаешь, прав он?", reply_to=reply)
    segments = extract_reply_segments(msg)

    prompt = build_segmented_prompt(
        segments=segments,
        sender_name="bob",
        is_group=True,
        fallback_query="",
    )

    assert "[В ответ на сообщение @alice — полностью]" in prompt
    assert "Длинная цитата про @yung_nagato и AI" in prompt
    assert "[Адресовано (@mentions)]: @yung_nagato" in prompt
    assert "[Текущее сообщение от @bob]" in prompt
    assert "как считаешь, прав он?" in prompt


def test_segmented_prompt_skips_empty_blocks() -> None:
    """Без mentions и без reply prompt сворачивается в одну секцию."""
    msg = _msg(text="Привет, как дела?")
    segments = extract_reply_segments(msg)

    prompt = build_segmented_prompt(segments=segments, is_group=False)

    assert "[В ответ" not in prompt
    assert "[Адресовано" not in prompt
    assert "[Текущее сообщение]" in prompt
    assert "Привет, как дела?" in prompt


# ---------------------------------------------------------------------------
# has_persona_mention_in_reply_to
# ---------------------------------------------------------------------------


def test_has_persona_mention_in_reply_to_positive() -> None:
    reply = _msg(text="спросим @yung_nagato")
    msg = _msg(text="ну?", reply_to=reply)
    assert has_persona_mention_in_reply_to(msg, ["yung_nagato", "kraab"]) is True


def test_has_persona_mention_in_reply_to_case_insensitive() -> None:
    reply = _msg(text="FYI @YUNG_Nagato — посмотри")
    msg = _msg(text="ага", reply_to=reply)
    assert has_persona_mention_in_reply_to(msg, ["yung_nagato"]) is True


def test_has_persona_mention_in_reply_to_negative() -> None:
    reply = _msg(text="спросим @alice")
    msg = _msg(text="ну?", reply_to=reply)
    assert has_persona_mention_in_reply_to(msg, ["yung_nagato"]) is False


def test_has_persona_mention_no_reply() -> None:
    msg = _msg(text="@yung_nagato в новом сообщении")
    # сам по себе current text — не reply_to body
    assert has_persona_mention_in_reply_to(msg, ["yung_nagato"]) is False
