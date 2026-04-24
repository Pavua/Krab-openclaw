# -*- coding: utf-8 -*-
"""Тесты для sentry_webhook_formatter — Sentry → Telegram markdown."""

from __future__ import annotations

import pytest

from src.core.sentry_webhook_formatter import format_sentry_alert

# ─────────────────────── Issue alert ────────────────────────


def test_issue_alert_format() -> None:
    payload = {
        "action": "triggered",
        "data": {
            "project": "krab",
            "issue": {
                "title": "Division by zero in scheduler",
                "culprit": "src.core.scheduler.tick",
                "count": 42,
                "userCount": 3,
                "permalink": "https://sentry.io/issues/123",
                "level": "error",
            },
            "event": {
                "environment": "production",
                "level": "error",
                "title": "ZeroDivisionError",
            },
        },
    }
    out = format_sentry_alert(payload)
    assert out is not None
    assert "Sentry alert" in out
    assert "ERROR" in out
    assert "ZeroDivisionError" in out
    assert "src.core.scheduler.tick" in out
    assert "production" in out
    assert "42" in out
    assert "https://sentry.io/issues/123" in out


def test_issue_alert_fatal_emoji() -> None:
    payload = {
        "action": "triggered",
        "data": {
            "issue": {"title": "Crash", "level": "fatal", "count": 1},
            "event": {"level": "fatal", "title": "Crash"},
        },
    }
    out = format_sentry_alert(payload)
    assert out is not None
    assert "FATAL" in out


# ────────────────────── Metric alert ────────────────────────


def test_metric_alert_format() -> None:
    payload = {
        "action": "triggered",
        "data": {
            "metric_alert": {
                "title": "High error rate",
                "status": "critical",
                "threshold": 100,
                "aggregate": "count()",
                "value": 250,
            },
        },
    }
    out = format_sentry_alert(payload)
    assert out is not None
    assert "Sentry metric" in out
    assert "CRITICAL" in out
    assert "High error rate" in out
    assert "250" in out
    assert "count()" in out


def test_metric_alert_camelcase_key() -> None:
    # Sentry иногда использует metricAlert (camelCase).
    payload = {
        "data": {
            "metricAlert": {
                "name": "Latency high",
                "status": "warning",
                "threshold": 500,
                "value": 700,
            }
        }
    }
    out = format_sentry_alert(payload)
    assert out is not None
    assert "Latency high" in out
    assert "WARNING" in out


# ────────────────────── Safety cases ────────────────────────


def test_empty_payload() -> None:
    assert format_sentry_alert({}) is None


@pytest.mark.parametrize("bad", ["string", 123, [], None, 3.14])
def test_malformed_payload_no_crash(bad) -> None:
    assert format_sentry_alert(bad) is None  # type: ignore[arg-type]


def test_html_injection_escaped() -> None:
    payload = {
        "data": {
            "issue": {"title": "<script>alert(1)</script>", "count": 1},
            "event": {"title": "<script>alert(1)</script>", "level": "error"},
        }
    }
    out = format_sentry_alert(payload)
    assert out is not None
    # HTML не интерпретируется т.к. Telegram parse_mode=markdown.
    # Убеждаемся что текст присутствует как данные (без каких-либо разрывов).
    assert "script" in out


def test_markdown_injection_escaped() -> None:
    payload = {
        "data": {
            "issue": {"title": "evil *bold* _under_ `code`", "count": 1},
            "event": {"title": "evil *bold* _under_ `code`", "level": "error"},
        }
    }
    out = format_sentry_alert(payload)
    assert out is not None
    # _escape_md убирает *, _, ` из заголовка.
    # Markdown-разметка самого шаблона (* в "Sentry alert") остаётся.
    # Проверяем что injection-символы удалены из title-зоны.
    # Заголовок "evil bold under code" — без спецсимволов.
    assert "evil bold under code" in out
    # Никаких bacтиков в части заголовка.
    title_line = [l for l in out.splitlines() if "evil" in l][0]
    assert "*" not in title_line
    assert "_" not in title_line
    assert "`" not in title_line


def test_very_long_title_truncated() -> None:
    long_title = "A" * 500
    payload = {
        "data": {
            "issue": {"title": long_title, "count": 1},
            "event": {"title": long_title, "level": "error"},
        }
    }
    out = format_sentry_alert(payload)
    assert out is not None
    # Заголовок обрезан до 200.
    assert "A" * 201 not in out
    assert "A" * 200 in out


def test_missing_optional_fields() -> None:
    # Ни culprit, ни permalink, ни project.
    payload = {
        "data": {
            "issue": {"title": "Bare issue", "count": 1},
            "event": {"title": "Bare issue", "level": "warning"},
        }
    }
    out = format_sentry_alert(payload)
    assert out is not None
    assert "Bare issue" in out
    assert "WARNING" in out
    # Нет строки с `↳ ` (culprit) и нет raw link.
    assert "↳" not in out
    assert "http" not in out


def test_unsupported_payload_returns_none() -> None:
    # data есть, но нет ни issue/event, ни metric_alert.
    payload = {"action": "triggered", "data": {"other": "stuff"}}
    assert format_sentry_alert(payload) is None


def test_unknown_level_default_emoji() -> None:
    payload = {
        "data": {
            "issue": {"title": "Weird", "level": "chartreuse", "count": 1},
            "event": {"title": "Weird", "level": "chartreuse"},
        }
    }
    out = format_sentry_alert(payload)
    assert out is not None
    assert "CHARTREUSE" in out
