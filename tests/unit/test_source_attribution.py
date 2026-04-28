# -*- coding: utf-8 -*-
"""Тесты Idea 16 — Source Attribution."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.source_attribution import (
    SourceAttribution,
    SourcedFact,
    attach_source_to_chunks,
    format_attribution,
    with_confidence,
)


def test_format_attribution_chat_with_title_and_date() -> None:
    src = SourceAttribution(
        origin="memory",
        chat_id=-1001234,
        chat_title="How2AI",
        timestamp=datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc),
    )
    assert format_attribution(src) == "из чата How2AI (24.04)"


def test_format_attribution_web_extracts_domain_from_url() -> None:
    src_with_domain = SourceAttribution(origin="web", domain="yandex.ru")
    assert format_attribution(src_with_domain) == "из веб-поиска (yandex.ru)"

    src_with_url = SourceAttribution(origin="web", url="https://example.org/path?x=1")
    assert format_attribution(src_with_url) == "из веб-поиска (example.org)"

    src_bare = SourceAttribution(origin="web")
    assert format_attribution(src_bare) == "из веб-поиска"


def test_attach_source_to_chunks_handles_strings_dicts_and_objects() -> None:
    default = SourceAttribution(origin="memory", chat_title="How2AI")

    class _Chunk:
        def __init__(self, text: str) -> None:
            self.text = text

    chunks = [
        "raw string fact",
        {"text": "dict fact"},
        _Chunk("object fact"),
        {"text": "dict with own source", "source": SourceAttribution(origin="web", domain="wiki.org")},
    ]
    result = attach_source_to_chunks(chunks, default)

    assert len(result) == 4
    assert all(isinstance(f, SourcedFact) for f in result)
    assert result[0].text == "raw string fact"
    assert result[0].source is default
    assert result[1].text == "dict fact"
    assert result[1].source is default
    assert result[2].text == "object fact"
    assert result[2].source is default
    # dict с собственной атрибуцией: default не должен перетереть.
    assert result[3].source.origin == "web"
    assert result[3].source.domain == "wiki.org"


def test_attach_source_to_chunks_missing_source_graceful() -> None:
    """Невалидные/странные входы не должны raise'ить, а должны fallback на str()."""
    default = SourceAttribution(origin="user")

    # None, число, dict без text — всё должно конвертироваться в SourcedFact без exception.
    chunks = [None, 42, {"no_text_key": "x"}]
    result = attach_source_to_chunks(chunks, default)

    assert len(result) == 3
    assert result[0].text == "None"
    assert result[1].text == "42"
    # dict без text → str(dict) репрезентация, чтобы caller заметил.
    assert "no_text_key" in result[2].text
    for fact in result:
        assert fact.source is default


def test_attach_source_to_chunks_idempotent_on_sourced_fact() -> None:
    """Повторный attach не перезаписывает существующую атрибуцию SourcedFact."""
    original_source = SourceAttribution(origin="web", domain="wikipedia.org")
    fact = SourcedFact(text="идемпотентный факт", source=original_source)

    new_default = SourceAttribution(origin="memory", chat_title="Other")
    once = attach_source_to_chunks([fact], new_default)
    twice = attach_source_to_chunks(once, new_default)

    assert once[0] is fact
    assert twice[0] is fact
    assert twice[0].source is original_source


def test_invalid_origin_and_confidence_raise() -> None:
    with pytest.raises(ValueError, match="invalid_source_origin"):
        SourceAttribution(origin="garbage")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="invalid_source_confidence"):
        SourceAttribution(origin="memory", confidence=1.5)


def test_with_confidence_returns_copy() -> None:
    src = SourceAttribution(origin="memory", chat_title="How2AI")
    updated = with_confidence(src, 0.87)
    assert src.confidence is None
    assert updated.confidence == 0.87
    assert updated.chat_title == "How2AI"
