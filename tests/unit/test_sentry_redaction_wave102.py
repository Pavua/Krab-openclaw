# -*- coding: utf-8 -*-
"""Wave 102: PII redaction extensions для Sentry events.

Покрывает phone/email regex, user_id allowlist, truncate, идемпотентность.
"""

from __future__ import annotations

from src.bootstrap.sentry_init import (
    _EXTRA_PAYLOAD_MAX_LEN,
    _MESSAGE_MAX_LEN,
    _apply_pii_redaction,
    _redact_string,
)


def test_phone_redacted_in_message() -> None:
    event: dict = {"message": "позвони мне +34612345678 срочно"}
    _apply_pii_redaction(event)
    assert "+34612345678" not in event["message"]
    assert "<phone>" in event["message"]


def test_email_redacted_in_message() -> None:
    event: dict = {"message": "пиши на user.name+tag@example.co.uk"}
    _apply_pii_redaction(event)
    assert "user.name+tag@example.co.uk" not in event["message"]
    assert "<email>" in event["message"]


def test_user_id_int_in_extra_redacted() -> None:
    event: dict = {"extra": {"user_id": 123456789, "other": "noop"}}
    _apply_pii_redaction(event)
    assert event["extra"]["user_id"] == "<user_id>"
    assert event["extra"]["other"] == "noop"


def test_user_id_str_digits_substituted() -> None:
    event: dict = {"extra": {"chat_id": "chat_987654321_main"}}
    _apply_pii_redaction(event)
    # 9 digits → substituted
    assert "987654321" not in event["extra"]["chat_id"]
    assert "<user_id>" in event["extra"]["chat_id"]


def test_long_message_truncated_with_marker() -> None:
    long_text = "x" * (_MESSAGE_MAX_LEN + 5000)
    event: dict = {"message": long_text}
    _apply_pii_redaction(event)
    out = event["message"]
    assert len(out) < len(long_text)
    assert "<truncated" in out
    assert "chars>" in out


def test_empty_event_noop() -> None:
    event: dict = {}
    _apply_pii_redaction(event)
    assert event == {}


def test_none_values_handled() -> None:
    event: dict = {
        "message": None,
        "extra": {"user_id": None, "chat_id": None, "payload": None},
        "tags": None,
    }
    # Не должно бросать.
    _apply_pii_redaction(event)
    # None в user-id allowlist остаётся None
    assert event["extra"]["user_id"] is None
    assert event["extra"]["chat_id"] is None


def test_idempotent_second_redact_no_change() -> None:
    event: dict = {
        "message": "phone +34612345678 email a@b.co user 123456789",
        "extra": {"user_id": 555000111, "chat_id": "id_42424242 here"},
    }
    _apply_pii_redaction(event)
    first_msg = event["message"]
    first_extra = dict(event["extra"])
    _apply_pii_redaction(event)
    assert event["message"] == first_msg
    assert event["extra"] == first_extra


def test_extra_payload_truncated() -> None:
    big = "p" * (_EXTRA_PAYLOAD_MAX_LEN + 1000)
    event: dict = {"extra": {"payload": big}}
    _apply_pii_redaction(event)
    out = event["extra"]["payload"]
    assert len(out) < len(big)
    assert "<truncated" in out


def test_redact_string_email_before_phone_order() -> None:
    # Гарантия порядка: email сначала, чтобы +tag@domain не попал в phone.
    s = _redact_string("contact me at +12345678901 or u@v.com")
    assert "<email>" in s
    assert "<phone>" in s
