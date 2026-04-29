# -*- coding: utf-8 -*-
"""
Регрессии `src/core/pii_redactor.py` (Idea 29).

Проверяем что redact():
1. Маскирует телефоны (international + RU).
2. Маскирует валидные credit card (Luhn pass) и НЕ трогает невалидные.
3. Маскирует email.
4. Маскирует IPv4.
5. Маскирует password=, token=, api_key=, bearer=.
6. Маскирует Telegram login code.
7. Idempotent — повторный вызов не плодит вложенных маркеров.
8. No false-positive: chat_id, message_id и обычные числа не редактируются.
"""

from __future__ import annotations

import pytest

from src.core.pii_redactor import PIIRedactor, luhn_check


@pytest.fixture
def redactor() -> PIIRedactor:
    return PIIRedactor()


def test_redact_phone_intl(redactor: PIIRedactor) -> None:
    out = redactor.redact("call me at +34 600 123 456 today")
    assert "[REDACTED:phone]" in out
    assert "600" not in out


def test_redact_phone_ru(redactor: PIIRedactor) -> None:
    out = redactor.redact("звони 8-915-123-45-67 после обеда")
    assert "[REDACTED:phone]" in out
    assert "915" not in out


def test_redact_credit_card_valid_luhn(redactor: PIIRedactor) -> None:
    # 4111 1111 1111 1111 — стандартный Visa test PAN, проходит Luhn
    out = redactor.redact("карта 4111 1111 1111 1111 списано")
    assert "[REDACTED:cc]" in out
    assert "4111" not in out


def test_credit_card_invalid_luhn_passes_through(redactor: PIIRedactor) -> None:
    # 16 цифр, но Luhn не проходит — не редактим (избегаем ложных срабатываний)
    out = redactor.redact("заказ 1234567890123456 готов")
    assert "[REDACTED:cc]" not in out
    assert "1234567890123456" in out
    assert luhn_check("1234567890123456") is False
    assert luhn_check("4111111111111111") is True


def test_redact_email(redactor: PIIRedactor) -> None:
    out = redactor.redact("write to user.name+spam@example.co.uk please")
    assert "[REDACTED:email]" in out
    assert "user.name" not in out


def test_redact_ip(redactor: PIIRedactor) -> None:
    out = redactor.redact("server at 192.168.1.42 is down")
    assert "[REDACTED:ip]" in out
    assert "192.168" not in out


def test_redact_secret_key_value(redactor: PIIRedactor) -> None:
    out = redactor.redact("export API_KEY=sk-abcdef123456 and continue")
    assert "[REDACTED:secret]" in out
    assert "sk-abcdef123456" not in out
    # ключ остаётся видимым для читаемости логов
    assert "API_KEY" in out


def test_redact_token_with_quotes(redactor: PIIRedactor) -> None:
    out = redactor.redact('config: token="hunter2-very-long-secret"')
    assert "[REDACTED:secret]" in out
    assert "hunter2" not in out


def test_redact_telegram_login_code(redactor: PIIRedactor) -> None:
    out = redactor.redact("Telegram login code: 12345 do not share")
    assert "[REDACTED:tg_code]" in out
    assert "12345" not in out


def test_idempotent(redactor: PIIRedactor) -> None:
    once = redactor.redact("phone +34 600 123 456 email a@b.com ip 10.0.0.1")
    twice = redactor.redact(once)
    assert once == twice
    # маркеры не вложены
    assert "[REDACTED:[REDACTED" not in twice


def test_no_false_positive_on_chat_ids(redactor: PIIRedactor) -> None:
    # Telegram chat_id / message_id — короткие numeric. Их не должно тронуть.
    text = "chat_id=-1001587432709 message_id=42 score=98765"
    out = redactor.redact(text)
    # chat_id выглядит как key=value, но "-1001..." — не secret pattern
    # (мы матчим password/token/api_key/secret/bearer; chat_id — нет).
    assert "1001587432709" in out
    assert "42" in out
    assert "98765" in out


def test_empty_and_non_str(redactor: PIIRedactor) -> None:
    assert redactor.redact("") == ""
    assert redactor.redact(None) == ""  # type: ignore[arg-type]
