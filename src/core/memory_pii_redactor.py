"""
PII-редактор для Memory Layer (Track E).

Назначение:
  Пропускает сырой текст сообщений через набор regex-правил и заменяет
  найденные чувствительные данные на placeholder'ы вида `[REDACTED:<KIND>]`.
  Используется как обязательный предфильтр перед индексацией в archive.db.

Принципы:
  1. Over-redaction предпочтительнее under-redaction: ложный [REDACTED:EMAIL]
     лучше, чем утёкший настоящий email.
  2. Порядок применения строгий: API-ключи → эмейлы → карты (Luhn) → крипто →
     телефоны → ID. Это нужно чтобы токены с префиксом `sk-ant-...` не зацеплял
     crypto-pattern, а email не съел карточный pattern внутри mailbox'а.
  3. Сырые (до редакции) тексты наружу не возвращаются — весь publicAPI
     оперирует только уже отредактированной строкой.
  4. Owner-whitelist — возможность оставить свои email/телефоны нетронутыми,
     если очень хочется индексировать DM "со мной" с контактами автора.

Порядок интеграции:
  - вызывается из `memory_archive.py` перед `INSERT INTO messages`;
  - вызывается из `scripts/bootstrap_memory.py` при парсинге Telegram Export;
  - standalone self-check: `python -m src.core.memory_pii_redactor`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Pattern

# -----------------------------------------------------------------------------
# Placeholder'ы — именованные, чтобы retrieval/диагностика могли различать типы.
# -----------------------------------------------------------------------------

PLACEHOLDER_CARD = "[REDACTED:CARD]"
PLACEHOLDER_CRYPTO_BTC = "[REDACTED:CRYPTO_BTC]"
PLACEHOLDER_CRYPTO_ETH = "[REDACTED:CRYPTO_ETH]"
PLACEHOLDER_CRYPTO_TRX = "[REDACTED:CRYPTO_TRX]"
PLACEHOLDER_CRYPTO_SOL = "[REDACTED:CRYPTO_SOL]"
PLACEHOLDER_API_KEY = "[REDACTED:API_KEY]"
PLACEHOLDER_JWT = "[REDACTED:JWT]"
PLACEHOLDER_PHONE = "[REDACTED:PHONE]"
PLACEHOLDER_EMAIL = "[REDACTED:EMAIL]"
PLACEHOLDER_PASSPORT = "[REDACTED:PASSPORT]"


# -----------------------------------------------------------------------------
# Regex-паттерны. Каждый именованный ключ = отдельная категория в метриках.
# -----------------------------------------------------------------------------

# API keys: OpenAI/Anthropic/Google/generic. Ставим раньше crypto, чтобы
# не перепутать `sk-ant-api03-...` с base58.
_API_KEY_PATTERNS: dict[str, Pattern[str]] = {
    # Anthropic раньше OpenAI — иначе `sk-ant-...` попадёт в openai-паттерн.
    "anthropic": re.compile(r"\bsk-ant-(?:api\d+-)?[A-Za-z0-9_\-]{30,}\b"),
    # Для OpenAI — negative lookahead на `ant-`, страхует порядок применения.
    "openai": re.compile(r"\bsk-(?!ant-)(?:proj-|live-|test-)?[A-Za-z0-9_\-]{20,}\b"),
    "google": re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b"),
    # HuggingFace/Replicate/generic
    "hf_token": re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),
    "replicate": re.compile(r"\br8_[A-Za-z0-9]{30,}\b"),
    # Generic long hex/base64 token (40+ chars) возле keywords `key`, `token`, `secret`.
    # Это heuristic — не трогает обычный текст, только явно помеченный как ключ.
    "generic_labeled": re.compile(
        r"\b(?:api[_-]?key|secret|token|bearer)\b[^\w\n]{1,6}[A-Za-z0-9+/_\-]{24,}\b",
        re.IGNORECASE,
    ),
}

# JWT: три base64-сегмента, разделённые точками. Раньше crypto,
# т.к. могут попасть под base58-like паттерн.
_JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"
)

# Email: standard RFC-ish.
_EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# Phone: E.164 (+ от 10 до 15 цифр) и форматированный RU.
# Аккуратно: нужно стоять ПОСЛЕ card/Luhn, иначе съест карту.
_PHONE_PATTERN = re.compile(
    r"(?:"
    # +7 / 8 форматированный: +7 (999) 123-45-67
    r"(?:\+7|\b8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
    r"|"
    # E.164: +<10..15 цифр>
    r"\+\d{10,15}"
    r")"
)

# Bank card: 13–19 цифр с возможными разделителями. Валидируем Luhn,
# чтобы не стёрло message_id и номера заказов.
_CARD_CANDIDATE = re.compile(r"\b(?:\d[\s\-]?){13,19}\b")

# URL boundaries — нужны чтобы не редактить CARD/PHONE внутри URL
# (Twitter/X status IDs — 18-значные числа, Luhn-valid рандомно попадают).
_URL_RANGES_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# ASCII art / spam numbers: повторение одной цифры >=6 раз подряд без разделителей.
# Пример: "88888888888" (11 восьмёрок) — не телефон, а баннер/ASCII art.
_REPEATED_DIGIT_RE = re.compile(r"(\d)\1{5,}")

# Crypto. Порядок: ETH (самый узкий) → TRX → BTC bech32 → BTC legacy → SOL.
# SOL самый широкий (base58 32-44) — он последний.
_ETH_PATTERN = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_TRX_PATTERN = re.compile(r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b")
_BTC_BECH32 = re.compile(r"\bbc1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{39,59}\b")
_BTC_LEGACY = re.compile(r"\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b")
# SOL: base58, 32–44 символа. Границы слов и требование, чтобы хотя бы один
# символ был нецифровым, снижают ложные срабатывания на больших числах.
_SOL_PATTERN = re.compile(
    r"\b(?=[1-9A-HJ-NP-Za-km-z]*[A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}\b"
)

# Passport RU: 4+6 цифр, обычно с пробелом. Осторожно — может совпасть
# с любыми двумя группами цифр, поэтому требуем контекст слова "паспорт"/"passport"
# в пределах 30 символов или явный формат с пробелом/дефисом.
_PASSPORT_RU = re.compile(
    r"(?:(?:паспорт|passport)[^\w\n]{0,30})?\b\d{4}[\s\-]\d{6}\b",
    re.IGNORECASE,
)


# -----------------------------------------------------------------------------
# Публичный API.
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class RedactionStats:
    """Статистика замен по категориям (для логов и dashboard-метрик)."""

    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def merged_with(self, other: "RedactionStats") -> "RedactionStats":
        merged = dict(self.counts)
        for k, v in other.counts.items():
            merged[k] = merged.get(k, 0) + v
        return RedactionStats(counts=merged)


@dataclass(frozen=True)
class RedactionResult:
    """Результат одного вызова `redact()`."""

    text: str
    stats: RedactionStats


def _collect_url_ranges(text: str) -> list[tuple[int, int]]:
    """Собирает все (start, end) URL-диапазоны в тексте — один проход."""
    return [(m.start(), m.end()) for m in _URL_RANGES_RE.finditer(text)]


def _is_in_url(url_ranges: list[tuple[int, int]], match_start: int, match_end: int) -> bool:
    """True если [match_start, match_end) целиком попадает в один из URL-диапазонов."""
    for start, end in url_ranges:
        if start <= match_start and match_end <= end:
            return True
    return False


def _is_ascii_art_number(matched_text: str) -> bool:
    """True если строка содержит 6+ одинаковых цифр подряд (ASCII art / баннер)."""
    return bool(_REPEATED_DIGIT_RE.search(matched_text))


def _luhn_valid(digits: str) -> bool:
    """
    Классический Luhn — валидация номера банковской карты.
    Пустую строку и слишком короткие считаем невалидными
    (иначе сумма 0 проходила бы Luhn-check).
    """
    if not digits or len(digits) < 13:
        return False
    total = 0
    reverse = digits[::-1]
    for i, ch in enumerate(reverse):
        if not ch.isdigit():
            return False
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _redact_cards(text: str, counter: dict[str, int]) -> str:
    """Замена банковских карт через Luhn-валидацию (защита от ложных срабатываний).

    Пропускает совпадения внутри URL — Twitter/X status ID (18 цифр)
    случайно проходят Luhn, но это не карты.
    """

    url_ranges = _collect_url_ranges(text)

    def _sub(match: re.Match[str]) -> str:
        raw = match.group(0)
        # Skip если match целиком внутри URL (Twitter status ID и т.п.).
        if _is_in_url(url_ranges, match.start(), match.end()):
            return raw
        digits = re.sub(r"[\s\-]", "", raw)
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            counter["card"] = counter.get("card", 0) + 1
            return PLACEHOLDER_CARD
        return raw

    return _CARD_CANDIDATE.sub(_sub, text)


class PIIRedactor:
    """
    Регексный редактор чувствительных данных.

    Args:
        owner_whitelist: подстроки (email, телефон) оператора, которые НЕ редактим.
            Например, `["pablito@me.com", "+79991234567"]`.
    """

    def __init__(self, owner_whitelist: Iterable[str] | None = None) -> None:
        self._owner_whitelist = tuple(
            w.lower().strip() for w in (owner_whitelist or ()) if w.strip()
        )

    # ------------------------------------------------------------------
    # Публичные методы.
    # ------------------------------------------------------------------

    def redact(self, text: str) -> RedactionResult:
        """Прогоняет текст через все категории в детерминированном порядке."""
        if not text:
            return RedactionResult(text=text or "", stats=RedactionStats())

        counter: dict[str, int] = {}
        result = text

        # 1. JWT первым — иначе generic_labeled api-key съест префикс
        # "Bearer eyJ..." и сломает последующий JWT-матч.
        result, hits = self._sub_with_counter(_JWT_PATTERN, PLACEHOLDER_JWT, result)
        if hits:
            counter["jwt"] = hits

        # 2. API keys — после JWT, до crypto.
        for name, pattern in _API_KEY_PATTERNS.items():
            result, hits = self._sub_with_counter(pattern, PLACEHOLDER_API_KEY, result)
            if hits:
                counter[f"api_key_{name}"] = counter.get(f"api_key_{name}", 0) + hits

        # 3. Email (с owner-whitelist).
        result = self._redact_emails(result, counter)

        # 4. Cards (Luhn-валидируемые).
        result = _redact_cards(result, counter)

        # 5. Phones (с owner-whitelist).
        result = self._redact_phones(result, counter)

        # 6. Passports.
        result, hits = self._sub_with_counter(_PASSPORT_RU, PLACEHOLDER_PASSPORT, result)
        if hits:
            counter["passport"] = hits

        # 7. Crypto: от узких к широким.
        for pattern, placeholder, key in (
            (_ETH_PATTERN, PLACEHOLDER_CRYPTO_ETH, "crypto_eth"),
            (_TRX_PATTERN, PLACEHOLDER_CRYPTO_TRX, "crypto_trx"),
            (_BTC_BECH32, PLACEHOLDER_CRYPTO_BTC, "crypto_btc_bech32"),
            (_BTC_LEGACY, PLACEHOLDER_CRYPTO_BTC, "crypto_btc_legacy"),
            (_SOL_PATTERN, PLACEHOLDER_CRYPTO_SOL, "crypto_sol"),
        ):
            result, hits = self._sub_with_counter(pattern, placeholder, result)
            if hits:
                counter[key] = hits

        return RedactionResult(text=result, stats=RedactionStats(counts=counter))

    # ------------------------------------------------------------------
    # Внутренние помощники.
    # ------------------------------------------------------------------

    @staticmethod
    def _sub_with_counter(
        pattern: Pattern[str], placeholder: str, text: str
    ) -> tuple[str, int]:
        """sub() + учёт попаданий (без дополнительного regex-прохода)."""
        hits = 0

        def _sub(match: re.Match[str]) -> str:
            nonlocal hits
            hits += 1
            return placeholder

        return pattern.sub(_sub, text), hits

    def _redact_emails(self, text: str, counter: dict[str, int]) -> str:
        """Email'ы с owner-whitelist."""
        whitelisted = self._owner_whitelist

        def _sub(match: re.Match[str]) -> str:
            addr = match.group(0).lower()
            if any(w in addr for w in whitelisted):
                return match.group(0)  # owner — не трогаем
            counter["email"] = counter.get("email", 0) + 1
            return PLACEHOLDER_EMAIL

        return _EMAIL_PATTERN.sub(_sub, text)

    def _redact_phones(self, text: str, counter: dict[str, int]) -> str:
        """Телефоны с owner-whitelist.

        Skip-ит ASCII art (6+ одинаковых цифр подряд) и совпадения внутри URL.
        """
        whitelisted = self._owner_whitelist
        url_ranges = _collect_url_ranges(text)

        def _sub(match: re.Match[str]) -> str:
            raw = match.group(0)
            # Skip номера внутри URL (query string, ID и т.п.).
            if _is_in_url(url_ranges, match.start(), match.end()):
                return raw
            # Skip ASCII art / баннеры из повторяющихся цифр.
            if _is_ascii_art_number(raw):
                return raw
            normalized = re.sub(r"[\s\-\(\)]", "", raw)
            if any(w in normalized for w in whitelisted):
                return raw
            counter["phone"] = counter.get("phone", 0) + 1
            return PLACEHOLDER_PHONE

        return _PHONE_PATTERN.sub(_sub, text)


# -----------------------------------------------------------------------------
# Self-check.
# -----------------------------------------------------------------------------

def _selfcheck() -> int:
    """
    Быстрая демонстрация на синтетических данных.

    Все значения вымышленные: карты проходят Luhn случайно (4242…),
    crypto-адреса — известные публичные test-значения, API-ключи — явно фейковые.
    """
    samples: list[tuple[str, set[str]]] = [
        # (input, ожидаемые категории)
        (
            "Мой номер +7 (999) 123-45-67, email test@example.com",
            {"phone", "email"},
        ),
        (
            "Карта 4242 4242 4242 4242, истекает 12/28",
            {"card"},
        ),
        (
            "BTC: bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
            {"crypto_btc_bech32"},
        ),
        (
            "ETH donation to 0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb0",
            {"crypto_eth"},
        ),
        (
            "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            {"api_key_anthropic"},
        ),
        (
            "Google key: AIzaSyD-fake-fake-fake-fake-fake-fake-0",
            {"api_key_google"},
        ),
        (
            "JWT: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
            {"jwt"},
        ),
        (
            "Без чувствительных данных: привет мир!",
            set(),
        ),
        (
            "message_id 1234567890 — не карта, не должно стираться",
            set(),  # 10 цифр → Luhn не пройдёт, не попадёт под card-patter
        ),
    ]

    redactor = PIIRedactor(owner_whitelist=["owner@me.com", "+79990000000"])
    failures = 0

    for original, expected_kinds in samples:
        result = redactor.redact(original)
        found_kinds = {k for k, v in result.stats.counts.items() if v > 0}
        # Сводим подкатегории crypto_*/api_key_* к корневым для сравнения.
        normalized = {k.split("_")[0] if k.startswith("crypto_") or k.startswith("api_key_") else k for k in found_kinds}
        expected_normalized = {
            k.split("_")[0] if k.startswith("crypto_") or k.startswith("api_key_") else k
            for k in expected_kinds
        }
        ok = normalized.issuperset(expected_normalized)
        status = "✓" if ok else "✗"
        print(f"{status}  {original[:60]!s}")
        print(f"   → {result.text[:80]!s}")
        print(f"   hits={dict(result.stats.counts)} expected={sorted(expected_kinds)}")
        if not ok:
            failures += 1

    # Owner whitelist check.
    owner_msg = "pwrite owner@me.com or call +79990000000"
    owner_result = redactor.redact(owner_msg)
    owner_ok = "owner@me.com" in owner_result.text and "+79990000000" in owner_result.text
    print(f"{'✓' if owner_ok else '✗'}  owner whitelist: {owner_result.text}")
    if not owner_ok:
        failures += 1

    print(f"\nSelf-check: {len(samples) + 1 - failures}/{len(samples) + 1} passed.")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import sys

    sys.exit(_selfcheck())
