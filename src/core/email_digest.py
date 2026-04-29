# -*- coding: utf-8 -*-
"""
email_digest.py — Email digest builder (Idea 20).

Чистый сборщик markdown-дайджеста по входящим письмам. Сами письма не
вычитывает: получает их через инжектируемый `email_fetcher`, что позволяет
подключать любой бэкенд (MCP gmail, IMAP-обёртку, mock в тестах).

Контракт:
- Ничего не отправляет и не сохраняет — только формирует строку.
- Промо/no-reply/auto-уведомления отфильтровываются по эвристикам.
- Bias к рабочим/личным: домены/keywords из EMAIL_BUSINESS_HINTS повышают
  importance, EMAIL_PROMO_PATTERNS — понижают/исключают.
- Секции с пустыми данными пропускаются.
- Если fetcher падает — возвращается пустая строка, ошибка логируется.

Использование (планируемое):
    builder = EmailDigestBuilder(email_fetcher=mcp_gmail_fetch)
    md = builder.build_digest(hours_back=24, max_items=10)
    # cron-задача публикует md владельцу
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from .logger import get_logger

logger = get_logger(__name__)


# Паттерны промо/служебной почты (case-insensitive по from/subject).
EMAIL_PROMO_PATTERNS: tuple[str, ...] = (
    "no-reply",
    "noreply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "newsletter",
    "notifications@",
    "marketing@",
    "promo@",
    "deals@",
    "offers@",
    "support@",
    "@mailchimp",
    "@sendgrid",
    "unsubscribe",
)

# Маркеры важности в subject (повышают importance до high).
EMAIL_HIGH_IMPORTANCE_MARKERS: tuple[str, ...] = (
    "urgent",
    "срочно",
    "asap",
    "important",
    "важно",
    "action required",
    "deadline",
    "overdue",
    "invoice",
    "payment",
    "контракт",
    "contract",
)

# Маркеры "ожидает ответа" в body/subject.
EMAIL_AWAITING_REPLY_MARKERS: tuple[str, ...] = (
    "?",
    "please reply",
    "let me know",
    "ждём ответа",
    "ждем ответа",
    "жду ответа",
    "could you",
    "can you",
    "would you",
    "когда сможешь",
    "когда сможете",
)

# Лимит общего размера дайджеста (символов).
EMAIL_DIGEST_MAX_CHARS: int = 4000
# Превью body (символов).
EMAIL_BODY_PREVIEW_CHARS: int = 160


@dataclass(frozen=True)
class EmailItem:
    """Нормализованное письмо на входе билдера."""

    from_addr: str
    subject: str
    body_preview: str
    received_at: datetime
    importance: str = "standard"  # high | standard | low
    awaiting_reply: bool = False
    extra: dict = field(default_factory=dict)


def _is_promo(item: EmailItem) -> bool:
    """Эвристика: письмо — промо/служебка, отбрасываем."""
    haystack = f"{item.from_addr} {item.subject}".lower()
    return any(pat in haystack for pat in EMAIL_PROMO_PATTERNS)


def _detect_high_importance(item: EmailItem) -> bool:
    """Поднять до high, если в subject маркеры срочности."""
    if item.importance == "high":
        return True
    subj = item.subject.lower()
    return any(marker in subj for marker in EMAIL_HIGH_IMPORTANCE_MARKERS)


def _detect_awaiting_reply(item: EmailItem) -> bool:
    """Эвристика на 'ожидает ответа': явный флаг или вопрос/просьба."""
    if item.awaiting_reply:
        return True
    haystack = f"{item.subject}\n{item.body_preview}".lower()
    # Простой вопрос в теме считаем за awaiting.
    if "?" in item.subject:
        return True
    return any(marker in haystack for marker in EMAIL_AWAITING_REPLY_MARKERS if marker != "?")


def _truncate(text: str, limit: int) -> str:
    """Аккуратно подрезать текст с многоточием."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _format_item(item: EmailItem) -> str:
    """Одна строка письма для секции."""
    subj = _truncate(item.subject or "(без темы)", 80)
    sender = _truncate(item.from_addr or "unknown", 60)
    preview = _truncate(item.body_preview, EMAIL_BODY_PREVIEW_CHARS)
    when = item.received_at.strftime("%H:%M")
    line = f"- **{subj}** — _{sender}_ ({when})"
    if preview:
        line += f"\n  > {preview}"
    return line


class EmailDigestBuilder:
    """Сборщик markdown-дайджеста по почте.

    Параметры:
    - email_fetcher: callable принимающий `hours_back` и возвращающий список
      EmailItem. Может быть синхронным; асинхронные реализации оборачиваются
      на стороне вызывающего.
    - now_fn: для инжекта часов в тестах.
    """

    def __init__(
        self,
        *,
        email_fetcher: Callable[[int], list[EmailItem]],
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._fetch = email_fetcher
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def build_digest(self, *, hours_back: int = 24, max_items: int = 10) -> str:
        """Собрать markdown-дайджест по письмам за последние `hours_back` часов.

        Возвращает пустую строку, если писем нет или fetcher упал.
        """
        if hours_back <= 0:
            return ""
        if max_items <= 0:
            return ""

        try:
            raw_items = list(self._fetch(hours_back))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "email_digest_fetch_failed",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return ""

        cutoff = self._now_fn() - timedelta(hours=hours_back)
        # Фильтрация: окно времени + промо.
        filtered: list[EmailItem] = []
        for raw in raw_items:
            if raw.received_at < cutoff:
                continue
            if _is_promo(raw):
                continue
            filtered.append(raw)

        if not filtered:
            logger.info("email_digest_empty", extra={"hours_back": hours_back})
            return ""

        # Расклассифицировать.
        high: list[EmailItem] = []
        standard: list[EmailItem] = []
        awaiting: list[EmailItem] = []

        for item in filtered:
            is_high = _detect_high_importance(item)
            is_awaiting = _detect_awaiting_reply(item)
            normalized = EmailItem(
                from_addr=item.from_addr,
                subject=item.subject,
                body_preview=item.body_preview,
                received_at=item.received_at,
                importance="high" if is_high else item.importance,
                awaiting_reply=is_awaiting,
                extra=item.extra,
            )
            if is_awaiting:
                awaiting.append(normalized)
            if is_high:
                high.append(normalized)
            else:
                standard.append(normalized)

        # Применить cap по количеству элементов суммарно.
        # Приоритет: high → awaiting → standard.
        budget = max_items
        high_capped = high[:budget]
        budget -= len(high_capped)
        awaiting_capped = [i for i in awaiting if i not in high_capped][: max(budget, 0)]
        budget -= len(awaiting_capped)
        standard_capped = [
            i for i in standard if i not in high_capped and i not in awaiting_capped
        ][: max(budget, 0)]

        # Сборка markdown.
        parts: list[str] = []
        header_when = self._now_fn().strftime("%Y-%m-%d %H:%M UTC")
        parts.append(f"# 📧 Email digest ({hours_back}h, {header_when})")

        if high_capped:
            parts.append("\n## 🔥 High importance")
            parts.extend(_format_item(i) for i in high_capped)

        if standard_capped:
            parts.append("\n## 📋 Standard")
            parts.extend(_format_item(i) for i in standard_capped)

        if awaiting_capped:
            parts.append("\n## 📤 Awaiting reply")
            parts.extend(_format_item(i) for i in awaiting_capped)

        digest = "\n".join(parts).strip()
        if len(digest) > EMAIL_DIGEST_MAX_CHARS:
            digest = digest[: EMAIL_DIGEST_MAX_CHARS - 1].rstrip() + "…"

        logger.info(
            "email_digest_built",
            extra={
                "total_items": len(filtered),
                "high": len(high_capped),
                "standard": len(standard_capped),
                "awaiting": len(awaiting_capped),
                "chars": len(digest),
            },
        )
        return digest


__all__ = [
    "EmailDigestBuilder",
    "EmailItem",
    "EMAIL_DIGEST_MAX_CHARS",
    "EMAIL_BODY_PREVIEW_CHARS",
]
