# -*- coding: utf-8 -*-
"""
Wave 44-E: PII redaction в Sentry before_send hook.

До Wave 44-E активный bootstrap/sentry_init.py НЕ редактировал PII — любой
logger.error содержащий TG bot token / Google API key / Bearer token / phone
утекал raw в Sentry SaaS. Старый sentry_integration.py имел паттерны но был
deprecated и не подключён к boot path.

Эти тесты гарантируют, что:
  1. _before_send редактирует tokens/keys/phone в message, exception.value,
     logentry.message, breadcrumbs, extra.
  2. Маркеры split_brain_reconnect_did_not_restore_updates / split_brain_escalation
     дропаются как benign (наша собственная intentional escalation, не Sentry-bug).
  3. Идемпотентность: уже редактированная строка не редактируется повторно.
"""

from __future__ import annotations

from src.bootstrap.sentry_init import _before_send, _redact_string


# ── PII redaction tests ─────────────────────────────────────────────────────


def test_redact_telegram_bot_token() -> None:
    """TG bot tokens вида 1234567890:AAH... должны заменяться маркером."""
    raw = "fail to send token=1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    out = _redact_string(raw)
    assert "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw" not in out
    assert "<TG_BOT_TOKEN>" in out


def test_redact_google_api_key() -> None:
    """Google API ключи (AIza...) → маркер."""
    raw = "auth header AIzaSyB-1234567890abcdefghijklmnopqrstuv"
    out = _redact_string(raw)
    assert "AIzaSyB-1234567890abcdefghijklmnopqrstuv" not in out
    assert "<GOOGLE_API_KEY>" in out


def test_redact_openai_style_key() -> None:
    """sk-... ключи (OpenAI/Anthropic) → маркер."""
    raw = "OPENAI_KEY=sk-proj_abcdefghijklmnopqrstuvwxyz123456"
    out = _redact_string(raw)
    assert "sk-proj_abcdefghijklmnopqrstuvwxyz123456" not in out
    assert "<API_KEY>" in out


def test_redact_bearer_token() -> None:
    """Bearer <token> в headers → маркер."""
    raw = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaa"
    out = _redact_string(raw)
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaa" not in out
    assert "Bearer <TOKEN>" in out


def test_redact_idempotent() -> None:
    """Повторное применение redact на уже редактированной строке = no-op."""
    raw = "token=1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    once = _redact_string(raw)
    twice = _redact_string(once)
    assert once == twice


def test_redact_no_pii_passthrough() -> None:
    """Строка без PII не должна изменяться."""
    raw = "userbot started successfully on port 8080"
    assert _redact_string(raw) == raw


# ── before_send integration: PII в разных полях event ───────────────────────


def test_before_send_redacts_pii_in_message() -> None:
    """logger.error message с токеном → message в Sentry событии редактирован."""
    event = {
        "message": "auth failed with key AIzaSyB-1234567890abcdefghijklmnopqrstuv real",
    }
    result = _before_send(event, {})
    assert result is not None
    assert "AIzaSyB-1234567890abcdefghijklmnopqrstuv" not in result.get("message", "")
    assert "<GOOGLE_API_KEY>" in result["message"]


def test_before_send_redacts_pii_in_exception_value() -> None:
    """exception.value с TG bot token → редактируется."""
    event = {
        "exception": {
            "values": [
                {
                    "type": "RuntimeError",
                    "value": "telegram error: 1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw failed",
                }
            ]
        }
    }
    result = _before_send(event, {})
    assert result is not None
    val = result["exception"]["values"][0]["value"]
    assert "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw" not in val
    assert "<TG_BOT_TOKEN>" in val


def test_before_send_redacts_pii_in_logentry() -> None:
    """logentry.message с Bearer token → редактируется."""
    event = {
        "logentry": {
            "message": "request headers: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaa",
        }
    }
    result = _before_send(event, {})
    assert result is not None
    msg = result["logentry"]["message"]
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaa" not in msg
    assert "Bearer <TOKEN>" in msg


def test_before_send_redacts_pii_in_breadcrumbs() -> None:
    """breadcrumbs.values[].message — также редактируем."""
    event = {
        "breadcrumbs": {
            "values": [
                {"message": "got AIzaSyB-1234567890abcdefghijklmnopqrstuv"},
                {"message": "ok"},
            ]
        }
    }
    result = _before_send(event, {})
    assert result is not None
    crumbs = result["breadcrumbs"]["values"]
    assert "AIzaSyB-1234567890abcdefghijklmnopqrstuv" not in crumbs[0]["message"]
    assert "<GOOGLE_API_KEY>" in crumbs[0]["message"]


def test_before_send_redacts_pii_in_extra() -> None:
    """extra dict — редактируем строковые values."""
    event = {
        "extra": {
            "url": "https://api/?key=AIzaSyB-1234567890abcdefghijklmnopqrstuv",
            "count": 42,
        }
    }
    result = _before_send(event, {})
    assert result is not None
    extra_url = result["extra"]["url"]
    assert "AIzaSyB-1234567890abcdefghijklmnopqrstuv" not in extra_url
    assert "<GOOGLE_API_KEY>" in extra_url
    # non-string values остаются как есть
    assert result["extra"]["count"] == 42


# ── Wave 44-C log noise: split_brain markers ────────────────────────────────


def test_before_send_drops_split_brain_reconnect_marker() -> None:
    """split_brain_reconnect_did_not_restore_updates — наш intentional escalation."""
    event = {
        "message": "split_brain_reconnect_did_not_restore_updates after 60s probe",
        "level": "error",
    }
    assert _before_send(event, {}) is None


def test_before_send_drops_split_brain_escalation_marker() -> None:
    """split_brain_escalation (Wave 39-D fallback) — также benign."""
    event = {
        "message": "split_brain_escalation triggered launchd_exit_78",
    }
    assert _before_send(event, {}) is None


def test_before_send_drops_split_brain_in_extra_error_code() -> None:
    """extra.error_code='split_brain_escalation' → drop."""
    event = {"extra": {"error_code": "split_brain_escalation"}}
    assert _before_send(event, {}) is None


def test_before_send_drops_split_brain_in_logentry() -> None:
    """logentry.message с split_brain маркером → drop."""
    event = {
        "logentry": {
            "message": "telegram_split_brain: split_brain_reconnect_did_not_restore_updates",
        }
    }
    assert _before_send(event, {}) is None
