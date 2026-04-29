# -*- coding: utf-8 -*-
"""Unit-тесты для email_digest.py (Idea 20)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.core.email_digest import EmailDigestBuilder, EmailItem

_NOW = datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc)


def _now_fn():
    return _NOW


def _make_item(
    *,
    from_addr: str = "alice@example.com",
    subject: str = "Hello",
    body: str = "Quick question about the project.",
    minutes_ago: int = 30,
    importance: str = "standard",
    awaiting: bool = False,
) -> EmailItem:
    return EmailItem(
        from_addr=from_addr,
        subject=subject,
        body_preview=body,
        received_at=_NOW - timedelta(minutes=minutes_ago),
        importance=importance,
        awaiting_reply=awaiting,
    )


def test_empty_input_returns_empty_string():
    """Fetcher возвращает пусто → дайджест пустой."""
    builder = EmailDigestBuilder(email_fetcher=lambda hours: [], now_fn=_now_fn)
    assert builder.build_digest(hours_back=24, max_items=10) == ""


def test_promo_and_no_reply_filtered_out():
    """Промо/no-reply письма не попадают в дайджест."""
    items = [
        _make_item(from_addr="newsletter@shop.com", subject="50% off!"),
        _make_item(from_addr="no-reply@github.com", subject="PR merged"),
        _make_item(from_addr="notifications@slack.com", subject="New message"),
    ]
    builder = EmailDigestBuilder(email_fetcher=lambda hours: items, now_fn=_now_fn)
    result = builder.build_digest(hours_back=24, max_items=10)
    assert result == ""


def test_high_importance_section_and_format():
    """Subject с маркером 'urgent' попадает в 🔥 High importance."""
    items = [
        _make_item(subject="Urgent: contract review", body="Please check today"),
        _make_item(
            from_addr="bob@team.com",
            subject="Daily sync notes",
            body="See attached.",
        ),
    ]
    builder = EmailDigestBuilder(email_fetcher=lambda hours: items, now_fn=_now_fn)
    result = builder.build_digest(hours_back=24, max_items=10)
    assert result.startswith("# 📧 Email digest")
    assert "## 🔥 High importance" in result
    assert "Urgent: contract review" in result
    assert "## 📋 Standard" in result
    assert "Daily sync notes" in result


def test_awaiting_reply_detection_via_question_mark():
    """Subject с '?' детектится как awaiting reply."""
    items = [
        _make_item(subject="Можем созвониться завтра?", body="Нужно обсудить."),
    ]
    builder = EmailDigestBuilder(email_fetcher=lambda hours: items, now_fn=_now_fn)
    result = builder.build_digest(hours_back=24, max_items=10)
    assert "## 📤 Awaiting reply" in result
    assert "Можем созвониться завтра?" in result


def test_max_items_cap_respected():
    """max_items ограничивает суммарный объём элементов."""
    items = [
        _make_item(subject=f"Email {i}", body=f"Body {i}", minutes_ago=i + 1)
        for i in range(20)
    ]
    builder = EmailDigestBuilder(email_fetcher=lambda hours: items, now_fn=_now_fn)
    result = builder.build_digest(hours_back=24, max_items=3)
    # Должно быть ровно 3 пункта в Standard (не high, не awaiting).
    bullet_lines = [ln for ln in result.split("\n") if ln.startswith("- **")]
    assert len(bullet_lines) == 3


def test_outside_window_filtered():
    """Письма старше hours_back игнорируются."""
    items = [
        _make_item(subject="Recent", minutes_ago=10),
        _make_item(subject="Old one", minutes_ago=60 * 48),  # 48ч назад
    ]
    builder = EmailDigestBuilder(email_fetcher=lambda hours: items, now_fn=_now_fn)
    result = builder.build_digest(hours_back=24, max_items=10)
    assert "Recent" in result
    assert "Old one" not in result


def test_fetcher_exception_returns_empty():
    """Если fetcher падает — пустая строка, без раскрутки исключения."""

    def bad_fetch(hours):
        raise RuntimeError("imap timeout")

    builder = EmailDigestBuilder(email_fetcher=bad_fetch, now_fn=_now_fn)
    assert builder.build_digest(hours_back=24, max_items=10) == ""


@pytest.mark.parametrize("hours_back,max_items", [(0, 10), (24, 0), (-1, 5)])
def test_invalid_params_return_empty(hours_back, max_items):
    """Невалидные hours_back/max_items → пустая строка."""
    builder = EmailDigestBuilder(
        email_fetcher=lambda hours: [_make_item()], now_fn=_now_fn
    )
    assert builder.build_digest(hours_back=hours_back, max_items=max_items) == ""
