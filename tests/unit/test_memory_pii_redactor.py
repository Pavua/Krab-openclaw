"""
Unit-тесты PII-редактора Memory Layer.

Все строки в тестах синтетические. Карточные номера — тестовые Luhn-valid
(4242… — public Stripe test, 5555… — Mastercard test). Crypto-адреса — широко
известные публичные пулы (bc1q example address, Vitalik's donation wallet и т.п.).
API-ключи — явно фейковые (повторяющиеся символы).

Запуск без родительского conftest:
    venv/bin/python -m pytest tests/unit/test_memory_pii_redactor.py --noconftest -q
"""

from __future__ import annotations

import pytest

from src.core.memory_pii_redactor import (
    PIIRedactor,
    RedactionResult,
    _luhn_valid,
)


# ---------------------------------------------------------------------------
# Базовый fixture.
# ---------------------------------------------------------------------------

@pytest.fixture
def redactor() -> PIIRedactor:
    """Редактор без whitelist — для строгих тестов."""
    return PIIRedactor()


@pytest.fixture
def redactor_with_owner() -> PIIRedactor:
    """Редактор с owner-whitelist — для тестов pass-through."""
    return PIIRedactor(owner_whitelist=["owner@me.com", "+79990000000"])


# ---------------------------------------------------------------------------
# Luhn check.
# ---------------------------------------------------------------------------

class TestLuhn:
    @pytest.mark.parametrize(
        "digits, expected",
        [
            ("4242424242424242", True),   # Stripe Visa test
            ("5555555555554444", True),   # Stripe Mastercard test
            ("378282246310005", True),    # Amex test
            ("6011111111111117", True),   # Discover test
            ("4242424242424241", False),  # одна цифра не та
            ("1234567890123456", False),  # random
            ("1234567890", False),        # слишком короткое
            ("", False),                  # пусто
        ],
    )
    def test_luhn_validation(self, digits: str, expected: bool) -> None:
        assert _luhn_valid(digits) is expected


# ---------------------------------------------------------------------------
# Email redaction.
# ---------------------------------------------------------------------------

class TestEmailRedaction:
    def test_simple_email(self, redactor: PIIRedactor) -> None:
        result = redactor.redact("Write me at alice@example.com please.")
        assert "[REDACTED:EMAIL]" in result.text
        assert "alice@example.com" not in result.text
        assert result.stats.counts["email"] == 1

    def test_multiple_emails(self, redactor: PIIRedactor) -> None:
        result = redactor.redact("a@x.com, b@y.com, c@z.com")
        assert result.text.count("[REDACTED:EMAIL]") == 3
        assert result.stats.counts["email"] == 3

    def test_owner_whitelist_preserves_email(
        self, redactor_with_owner: PIIRedactor
    ) -> None:
        result = redactor_with_owner.redact("Reply to owner@me.com from bob@foo.com")
        assert "owner@me.com" in result.text
        assert "[REDACTED:EMAIL]" in result.text
        # Только bob@ заредактирован, не owner.
        assert result.stats.counts.get("email", 0) == 1

    def test_no_email_no_hit(self, redactor: PIIRedactor) -> None:
        result = redactor.redact("Просто текст без адресов.")
        assert "email" not in result.stats.counts


# ---------------------------------------------------------------------------
# Phone redaction.
# ---------------------------------------------------------------------------

class TestPhoneRedaction:
    @pytest.mark.parametrize(
        "raw",
        [
            "+7 (999) 123-45-67",
            "+79991234567",
            "8 999 123 45 67",
            "+442079460958",       # UK
            "+14155552671",        # US
        ],
    )
    def test_various_formats(self, redactor: PIIRedactor, raw: str) -> None:
        result = redactor.redact(f"Звони: {raw} или смс")
        assert "[REDACTED:PHONE]" in result.text, f"failed on: {raw}"
        assert raw not in result.text or "[REDACTED:PHONE]" in result.text

    def test_owner_phone_whitelist(self, redactor_with_owner: PIIRedactor) -> None:
        result = redactor_with_owner.redact(
            "Owner: +79990000000, other: +79991112233"
        )
        assert "+79990000000" in result.text
        assert "[REDACTED:PHONE]" in result.text


# ---------------------------------------------------------------------------
# Bank cards + Luhn.
# ---------------------------------------------------------------------------

class TestCardRedaction:
    @pytest.mark.parametrize(
        "card",
        [
            "4242 4242 4242 4242",
            "4242-4242-4242-4242",
            "4242424242424242",
            "5555 5555 5555 4444",
            "378282246310005",      # Amex 15 digits
        ],
    )
    def test_valid_cards_redacted(self, redactor: PIIRedactor, card: str) -> None:
        result = redactor.redact(f"Карта: {card}")
        assert "[REDACTED:CARD]" in result.text
        assert result.stats.counts["card"] >= 1

    def test_invalid_luhn_preserved(self, redactor: PIIRedactor) -> None:
        # 16 цифр но не Luhn-valid — не карта.
        result = redactor.redact("Не карта: 1234567890123456")
        assert "[REDACTED:CARD]" not in result.text
        assert "card" not in result.stats.counts

    def test_message_id_not_redacted(self, redactor: PIIRedactor) -> None:
        """Telegram message_id не должен стираться."""
        result = redactor.redact("message_id 1234567890 — не карта")
        assert "1234567890" in result.text
        assert "card" not in result.stats.counts


# ---------------------------------------------------------------------------
# Crypto addresses.
# ---------------------------------------------------------------------------

class TestCryptoRedaction:
    def test_btc_bech32(self, redactor: PIIRedactor) -> None:
        # Public BTC bech32 test address (from BIP-173 spec).
        addr = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
        result = redactor.redact(f"Send BTC to {addr}")
        assert "[REDACTED:CRYPTO_BTC]" in result.text
        assert addr not in result.text

    def test_btc_legacy(self, redactor: PIIRedactor) -> None:
        # Satoshi's genesis coinbase address.
        addr = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
        result = redactor.redact(f"Legacy: {addr}")
        assert "[REDACTED:CRYPTO_BTC]" in result.text

    def test_eth_address(self, redactor: PIIRedactor) -> None:
        # Vitalik's donation address (public).
        addr = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
        result = redactor.redact(f"ETH: {addr}")
        assert "[REDACTED:CRYPTO_ETH]" in result.text
        assert addr not in result.text

    def test_trx_address(self, redactor: PIIRedactor) -> None:
        # Public TRX foundation address.
        addr = "TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7"
        result = redactor.redact(f"TRX: {addr}")
        assert "[REDACTED:CRYPTO_TRX]" in result.text

    def test_sol_address(self, redactor: PIIRedactor) -> None:
        # Public SOL address (Serum DEX).
        addr = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
        result = redactor.redact(f"SOL: {addr}")
        # Может совпасть как crypto_sol или crypto_btc_legacy, нам важно просто удалить.
        assert any(
            ph in result.text
            for ph in ("[REDACTED:CRYPTO_SOL]", "[REDACTED:CRYPTO_BTC]")
        )

    def test_normal_hex_not_touched(self, redactor: PIIRedactor) -> None:
        """Короткий hex (commit sha) не должен редактиться."""
        result = redactor.redact("commit 0xabc123")
        assert "0xabc123" in result.text


# ---------------------------------------------------------------------------
# API keys.
# ---------------------------------------------------------------------------

class TestApiKeyRedaction:
    def test_openai_key(self, redactor: PIIRedactor) -> None:
        key = "sk-proj-" + "A" * 40
        result = redactor.redact(f"OPENAI_KEY={key}")
        assert "[REDACTED:API_KEY]" in result.text
        assert key not in result.text
        assert result.stats.counts.get("api_key_openai") == 1

    def test_anthropic_key_not_classified_as_openai(
        self, redactor: PIIRedactor
    ) -> None:
        """Регрессия: sk-ant-... не должен ловиться OpenAI-паттерном."""
        key = "sk-ant-api03-" + "B" * 40
        result = redactor.redact(f"CLAUDE={key}")
        assert "[REDACTED:API_KEY]" in result.text
        # Должно быть в anthropic, не в openai.
        assert result.stats.counts.get("api_key_anthropic") == 1
        assert "api_key_openai" not in result.stats.counts

    def test_google_key(self, redactor: PIIRedactor) -> None:
        key = "AIza" + "C" * 35
        result = redactor.redact(f"GOOGLE_API={key}")
        assert "[REDACTED:API_KEY]" in result.text
        assert result.stats.counts.get("api_key_google") == 1

    def test_hf_token(self, redactor: PIIRedactor) -> None:
        token = "hf_" + "D" * 35
        result = redactor.redact(f"HF={token}")
        assert "[REDACTED:API_KEY]" in result.text

    def test_jwt_token(self, redactor: PIIRedactor) -> None:
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        result = redactor.redact(f"Bearer {jwt}")
        assert "[REDACTED:JWT]" in result.text
        assert jwt not in result.text


# ---------------------------------------------------------------------------
# Passport RU.
# ---------------------------------------------------------------------------

class TestPassportRedaction:
    def test_passport_with_marker(self, redactor: PIIRedactor) -> None:
        result = redactor.redact("Паспорт 1234 567890, выдан давно")
        assert "[REDACTED:PASSPORT]" in result.text

    def test_passport_without_marker_4plus6(self, redactor: PIIRedactor) -> None:
        # Формат 4+6 с пробелом обычно passport-like, редактим.
        result = redactor.redact("Номер: 1234 567890")
        assert "[REDACTED:PASSPORT]" in result.text


# ---------------------------------------------------------------------------
# Регрессии / статистика / идемпотентность.
# ---------------------------------------------------------------------------

class TestRegressionsAndStats:
    def test_empty_string(self, redactor: PIIRedactor) -> None:
        result = redactor.redact("")
        assert result.text == ""
        assert result.stats.total == 0

    def test_none_like(self, redactor: PIIRedactor) -> None:
        # Пустая строка не должна падать.
        result = redactor.redact("")
        assert isinstance(result, RedactionResult)

    def test_no_pii_no_changes(self, redactor: PIIRedactor) -> None:
        msg = "Просто сообщение без чувствительных данных."
        result = redactor.redact(msg)
        assert result.text == msg
        assert result.stats.total == 0

    def test_idempotency(self, redactor: PIIRedactor) -> None:
        """Повторная редакция уже отредактированного текста не меняет его."""
        original = "email@test.com, +79991234567"
        once = redactor.redact(original)
        twice = redactor.redact(once.text)
        assert once.text == twice.text
        assert twice.stats.total == 0

    def test_stats_merge(self) -> None:
        """RedactionStats.merged_with складывает счётчики."""
        from src.core.memory_pii_redactor import RedactionStats

        a = RedactionStats(counts={"email": 1, "phone": 2})
        b = RedactionStats(counts={"email": 3, "card": 1})
        merged = a.merged_with(b)
        assert merged.counts == {"email": 4, "phone": 2, "card": 1}
        assert merged.total == 7

    def test_multi_category_single_message(self, redactor: PIIRedactor) -> None:
        msg = (
            "Привет! Напиши на alice@x.com или +7 999 123 45 67. "
            "BTC 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa, "
            "ETH 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045."
        )
        result = redactor.redact(msg)
        # В тексте не осталось оригинальных значений.
        assert "alice@x.com" not in result.text
        assert "+7 999 123 45 67" not in result.text
        assert "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa" not in result.text
        assert "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045" not in result.text
        # Все категории поймались.
        assert result.stats.counts.get("email") == 1
        assert result.stats.counts.get("phone") == 1
        assert result.stats.counts.get("crypto_btc_legacy") == 1
        assert result.stats.counts.get("crypto_eth") == 1


# ---------------------------------------------------------------------------
# False positives: URL status IDs и ASCII art.
# ---------------------------------------------------------------------------

class TestFalsePositives:
    """Регрессии для известных false positives (smoke test retrieval)."""

    def test_card_skipped_in_twitter_url(self, redactor: PIIRedactor) -> None:
        """18-значный Twitter status ID внутри URL — не должен стать CARD."""
        text = "Check https://x.com/balajis/status/1234567890123456789 for details"
        result = redactor.redact(text)
        assert "[REDACTED:CARD]" not in result.text
        assert "https://x.com/balajis/status/1234567890123456789" in result.text
        assert "card" not in result.stats.counts

    def test_card_skipped_in_http_url(self, redactor: PIIRedactor) -> None:
        """http:// (не https) тоже покрыт."""
        text = "Link http://example.com/item/1234567890123456789 raw"
        result = redactor.redact(text)
        assert "[REDACTED:CARD]" not in result.text
        assert "1234567890123456789" in result.text

    def test_card_still_redacted_outside_url(self, redactor: PIIRedactor) -> None:
        """Luhn-valid карта вне URL по-прежнему редактится."""
        text = "Моя карта: 4532015112830366"  # Luhn-valid
        result = redactor.redact(text)
        assert "[REDACTED:CARD]" in result.text
        assert result.stats.counts.get("card") == 1

    def test_card_inside_markdown_link(self, redactor: PIIRedactor) -> None:
        """Markdown ссылка: [text](https://...) — URL внутри скобок тоже skip."""
        text = "[tweet](https://x.com/user/status/1234567890123456789)"
        result = redactor.redact(text)
        assert "[REDACTED:CARD]" not in result.text
        assert "1234567890123456789" in result.text

    def test_phone_skipped_for_ascii_art(self, redactor: PIIRedactor) -> None:
        """11 повторов цифры '8' — ASCII art, не телефон."""
        text = "ASCII art: 88888888888 end"
        result = redactor.redact(text)
        assert "[REDACTED:PHONE]" not in result.text
        assert "88888888888" in result.text
        assert "phone" not in result.stats.counts

    def test_phone_skipped_for_repeated_zeros(self, redactor: PIIRedactor) -> None:
        """Последовательность нулей вида '+70000000000' — тоже ASCII art."""
        text = "Spam: +70000000000"
        result = redactor.redact(text)
        # 10 подряд идущих нулей — баннер/дефолт, не реальный телефон.
        assert "[REDACTED:PHONE]" not in result.text

    def test_phone_still_redacted_real(self, redactor: PIIRedactor) -> None:
        """Реальный телефон с разделителями — по-прежнему редактится."""
        text = "+7 999 123 45 67 звони"
        result = redactor.redact(text)
        assert "[REDACTED:PHONE]" in result.text
        assert result.stats.counts.get("phone") == 1

    def test_phone_still_redacted_e164(self, redactor: PIIRedactor) -> None:
        """E.164 формат без разделителей, но с разнообразными цифрами — phone."""
        text = "+79991234567 звони"
        result = redactor.redact(text)
        assert "[REDACTED:PHONE]" in result.text

    def test_phone_skipped_inside_url(self, redactor: PIIRedactor) -> None:
        """Номера внутри URL — query/path, не телефон."""
        text = "https://api.example.com/call/+79991234567?x=1 extra"
        result = redactor.redact(text)
        assert "[REDACTED:PHONE]" not in result.text
        assert "+79991234567" in result.text

    def test_mixed_url_and_real_phone(self, redactor: PIIRedactor) -> None:
        """В одной строке: URL с ID (skip) + реальный телефон (redact)."""
        text = (
            "Tweet https://x.com/status/1234567890123456789 "
            "звони +7 999 123 45 67"
        )
        result = redactor.redact(text)
        assert "[REDACTED:CARD]" not in result.text
        assert "1234567890123456789" in result.text
        assert "[REDACTED:PHONE]" in result.text
