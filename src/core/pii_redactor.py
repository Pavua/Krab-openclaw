# -*- coding: utf-8 -*-
"""
Auto-redact PII (Idea 29).

Прогоняет произвольный текст через набор regex-паттернов и заменяет совпадения
маркерами вида ``[REDACTED:phone]``, ``[REDACTED:cc]``, ``[REDACTED:email]``,
``[REDACTED:ip]``, ``[REDACTED:secret]``, ``[REDACTED:tg_code]``.

### Дизайн

* **Idempotent.** Повторный вызов redact() на уже редактированном тексте — no-op
  (паттерны не матчат маркер ``[REDACTED:...]``).
* **Pure / no I/O.** Никаких config-зависимостей внутри редактора. Конфигурация
  решается выше по стеку (см. `KRAB_PII_REDACTION_ENABLED`, `chat_sensitivity`).
* **Conservative.** Лучше пропустить редкий PII, чем потереть числовой
  identifier (chat_id, message_id). Поэтому:
  - Phone требует +/00 префикс ИЛИ строго 10-11 цифр с разделителями
    (избегаем ложных срабатываний на короткие numeric ID).
  - Credit card — Luhn-checksum на 13–19 цифр.
  - Password/token берётся только в формате ``key=value``/``key: value``.

### Не решает
- Не парсит JSON / YAML контентно — работает по plaintext regex.
- Не редактирует имена/адреса/паспорта (нужен NER, тяжёлая зависимость).
- Не редактирует Bitcoin/IBAN/SSN — добавим если попросят явно.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = ["PIIRedactor", "luhn_check"]


# Маркер уже редактированного фрагмента — используется чтобы при повторном
# проходе ничего не трогать. Pattern должен матчить любой `[REDACTED:foo]`.
_REDACTED_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"\[REDACTED:[a-z_]+\]")

# --- Phone --------------------------------------------------------------
# International ИЛИ длинная RU-форма с пробелами/дефисами/скобками.
# Принципиально требуем либо +/00 prefix, либо 10–11 цифр с явными разделителями
# вида (xxx) xxx-xx-xx, чтобы не ловить обычные 6-9-значные числа.
_PHONE_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?<!\w)
    (?:
        (?:\+|00)\s?\d{1,3}[\s\-().]{0,2}\d{1,4}[\s\-().]{0,2}\d{1,4}[\s\-().]{0,2}\d{1,4}(?:[\s\-().]{0,2}\d{1,4})?
        |
        (?:\(\d{3,4}\)\s?\d{2,3}[\s\-]\d{2,3}[\s\-]\d{2,3})
        |
        (?:8[\s\-]\d{3}[\s\-]\d{3}[\s\-]\d{2}[\s\-]\d{2})
    )
    (?!\w)""",
    re.VERBOSE,
)

# --- Credit card -------------------------------------------------------
# 13–19 цифр, могут быть разделены пробелами или дефисами по 4. Сам по себе
# regex слишком жадный — финальный allow/deny решается Luhn-проверкой.
_CC_RE: Final[re.Pattern[str]] = re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")

# --- Email --------------------------------------------------------------
_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+",
)

# --- IPv4 ---------------------------------------------------------------
_IP_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?!\d)",
)

# --- Secrets (password=, token=, api_key=, bearer ...) -----------------
# Ключ слева → значение до пробела/конца строки/конца строки/кавычки.
_SECRET_RE: Final[re.Pattern[str]] = re.compile(
    r"""\b
    (?P<key>password|passwd|pwd|secret|token|api[_-]?key|access[_-]?token|auth[_-]?token|bearer)
    \s*[:=]\s*
    (?P<value>
        "[^"\n]{3,}"
        | '[^'\n]{3,}'
        | [^\s'"]{6,}
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# --- Telegram login code (e.g. "Login code: 12345") --------------------
_TG_CODE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b(?:login\s+code|код\s+(?:входа|подтверждения)|verification\s+code)\b[^\d]{0,20}\d{4,7}",
)


def luhn_check(digits: str) -> bool:
    """Luhn checksum для строки цифр. Используется как фильтр для credit card."""
    s = "".join(ch for ch in digits if ch.isdigit())
    if len(s) < 13 or len(s) > 19:
        return False
    total = 0
    parity = len(s) % 2
    for i, ch in enumerate(s):
        n = ord(ch) - 48
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


class PIIRedactor:
    """Idempotent PII redactor. Не зависит от config / I/O.

    Использование::

        redactor = PIIRedactor()
        clean = redactor.redact("call +34 600 123 456")
        # → "call [REDACTED:phone]"
    """

    def redact(self, text: str) -> str:
        if not isinstance(text, str) or not text:
            return text or ""

        # Защищаем уже редактированные участки: вынимаем их в плейсхолдеры,
        # затем вернём после прохода. Это и есть основа idempotent поведения.
        protected: list[str] = []

        def _stash(match: re.Match[str]) -> str:
            protected.append(match.group(0))
            return f"\x00PII{len(protected) - 1}\x00"

        work = _REDACTED_MARKER_RE.sub(_stash, text)

        # Порядок важен: сначала secrets/email/tg_code (явно структурные), потом
        # phone/cc/ip — иначе credit-card regex может откусить кусок токена.
        work = _SECRET_RE.sub(self._replace_secret, work)
        work = _TG_CODE_RE.sub("[REDACTED:tg_code]", work)
        work = _EMAIL_RE.sub("[REDACTED:email]", work)
        work = _CC_RE.sub(self._replace_cc, work)
        work = _PHONE_RE.sub("[REDACTED:phone]", work)
        work = _IP_RE.sub("[REDACTED:ip]", work)

        # Возвращаем защищённые участки на место.
        def _unstash(match: re.Match[str]) -> str:
            idx = int(match.group(1))
            return protected[idx]

        work = re.sub(r"\x00PII(\d+)\x00", _unstash, work)
        return work

    # ---- Internal -------------------------------------------------------

    @staticmethod
    def _replace_secret(match: re.Match[str]) -> str:
        # Сохраняем ключ + разделитель, чтобы текст оставался читаемым
        # ("password=[REDACTED:secret]"). Восстанавливаем prefix, отрезая value
        # из исходного matched фрагмента.
        whole = match.group(0)
        value = match.group("value")
        prefix = whole[: -len(value)]
        return f"{prefix}[REDACTED:secret]"

    @staticmethod
    def _replace_cc(match: re.Match[str]) -> str:
        candidate = match.group(0)
        if luhn_check(candidate):
            return "[REDACTED:cc]"
        return candidate
