# -*- coding: utf-8 -*-
"""
Memory Injection Validator — блокирует persistent инструкции до подтверждения.

Проблема: MEMORY.md/USER.md в OpenClaw workspace инжектятся в каждую LLM-сессию
как bootstrap context. Юзер может запросить через !remember сохранение
инструкции "всегда делай X", которая станет влиять на все ответы Krab.

Решение: регулярные паттерны injection блокируются до явного !confirm <hash>
от владельца (owner_only).

Security events (stage/confirm/fail) логируются через structlog как audit trail.
In-memory counters доступны через attribute `.stats` для /api/metrics.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# "Weak" паттерны — одиночные слова-маркеры. Могут быть в легитимном контексте
# ("always use python"). Требуют allowlist-проверки.
_WEAK_INJECTION_PATTERNS_RAW = [
    r"\bвсегда\b",
    r"\bникогда\s+не\b",
    r"\balways\b",
    r"\bnever\b",
    r"\bпостоянно\b",
    r"\bотныне\b",
    r"\bпо\s+умолчанию\b",
    r"\bconstantly\b",
    r"\bcontinuously\b",
]

# "Strong" паттерны — явные injection-команды. Блокируются всегда,
# без allowlist-спасения (MEDIUM-1: allowlist должен спасать только weak markers).
_STRONG_INJECTION_PATTERNS_RAW = [
    r"\bв\s+каждом\s+(ответе|сообщении)\b",
    r"\bкаждый\s+раз\b",
    r"\bпосле\s+каждого\b",
    r"\bпиши\s+только\b",
    r"\bдобавляй\s+(фразу|suffix|prefix|суффикс|префикс)\b",
    r"\bзаканчивай\s+(фразой|словом|каждый)\b",
    r"\bin\s+each\s+response\b",
    r"\bevery\s+time\b",
    r"\bafter\s+every\b",
    r"\bwrite\s+only\b",
    r"\badd\s+(phrase|suffix|prefix)\b",
    # Новые синонимы (MEDIUM-2): RU
    r"\bначиная\s+с\s+(этого\s+момента|сейчас)\b",
    # Новые синонимы (MEDIUM-2): EN
    r"\bfrom\s+now\s+on\b",
    r"\bappend\s+to\s+(every|each|all)\b",
    # `prepend to` допускает литерал-строку между ("prepend 'hi' to all responses").
    r"\bprepend\b[^.\n]{0,80}\bto\s+(every|each|all)\b",
]

_WEAK_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE | re.UNICODE) for p in _WEAK_INJECTION_PATTERNS_RAW
]
_STRONG_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE | re.UNICODE) for p in _STRONG_INJECTION_PATTERNS_RAW
]

# Allowlist: "always verify config" — legitimate. Контекстные слова рядом с weak marker.
# MEDIUM-1: убрано "use" (слишком общее, давало semantic bypass через декоративное "use python").
_LEGIT_KEYWORDS = {
    "python",
    "rust",
    "golang",
    "typescript",
    "javascript",
    "node",
    "bash",
    "version",
    "протокол",
    "protocol",
    "config",
    "конфиг",
    "setting",
    "настройка",
    "используй",
    "verify",
    "проверяй",
    "check",
    "follow",
    "тесты",
    "tests",
    "pytest",
    "ruff",
    "линт",
    "lint",
    "format",
}

# MEDIUM-1: window сужен с 50 до 30 chars — keyword должен быть в том же clause.
_ALLOWLIST_CONTEXT_CHARS = 30

DEFAULT_TTL_MIN = 30
DEFAULT_MAX_PENDING = 20


@dataclass
class PendingMemoryWrite:
    """Запрос на запись, ожидающий !confirm от владельца."""

    text: str
    source: str = ""
    author: str = ""
    reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ttl_minutes: int = DEFAULT_TTL_MIN

    @property
    def hash(self) -> str:
        # 8-символьный hash от (text + source + author + created_at) — уникален per-stage.
        raw = f"{self.text}|{self.source}|{self.author}|{self.created_at.isoformat()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:8].upper()

    @property
    def expires_at(self) -> datetime:
        return self.created_at + timedelta(minutes=self.ttl_minutes)

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        return (now or datetime.now(timezone.utc)) > self.expires_at


class MemoryInjectionValidator:
    """
    Singleton-like валидатор с pending-queue.

    Не thread-safe — используется из одного asyncio event loop (Krab userbot).

    Attribute `stats` — in-memory counters security events (для /api/metrics).
    Всё логируется structlog-ом как audit trail.
    """

    def __init__(self, max_pending: int = DEFAULT_MAX_PENDING):
        self._pending: dict[str, PendingMemoryWrite] = {}
        self._max_pending = max_pending
        # MEDIUM-3: counters для мониторинга security events.
        self.stats: dict[str, int] = {
            "safe_total": 0,
            "injection_blocked_total": 0,
            "confirmed_total": 0,
            "confirm_failed_total": 0,
        }

    def check_injection(self, text: str) -> tuple[bool, str]:
        """
        Возвращает (is_suspicious, reason).

        is_suspicious=True → требует !confirm перед записью.

        Strong patterns (явные injection-команды) блокируются всегда.
        Weak patterns (одиночные маркеры — always/всегда) требуют allowlist-спасения.
        """
        if not text or not text.strip():
            return False, ""

        # NFKC-нормализация против bypass через ZWSP / homoglyphs / зеркальные юникод-формы.
        normalized = unicodedata.normalize("NFKC", text)
        lowered = normalized.lower()

        # 1) Strong patterns — блок без allowlist-спасения.
        for pat in _STRONG_INJECTION_PATTERNS:
            m = pat.search(lowered)
            if m:
                return True, f"injection_pattern: {pat.pattern}"

        # 2) Weak patterns — спасаются allowlist keywords в ±30 chars.
        for pat in _WEAK_INJECTION_PATTERNS:
            m = pat.search(lowered)
            if not m:
                continue
            start = max(0, m.start() - _ALLOWLIST_CONTEXT_CHARS)
            end = min(len(lowered), m.end() + _ALLOWLIST_CONTEXT_CHARS)
            ctx = lowered[start:end]
            if any(kw in ctx for kw in _LEGIT_KEYWORDS):
                continue
            return True, f"injection_pattern: {pat.pattern}"
        return False, ""

    def _gc_expired(self) -> int:
        """Чистит истёкшие pending-записи. Возвращает количество удалённых."""
        now = datetime.now(timezone.utc)
        before = len(self._pending)
        self._pending = {h: p for h, p in self._pending.items() if not p.is_expired(now)}
        return before - len(self._pending)

    def stage(
        self, text: str, source: str = "", author: str = ""
    ) -> tuple[bool, str, Optional[PendingMemoryWrite]]:
        """
        Проверяет + (если suspicious) ставит в pending-queue.

        Returns (safe_to_write, user_message, pending_obj_if_staged).
        safe_to_write=True → можно писать сразу.
        safe_to_write=False → нужен !confirm <hash>.
        """
        self._gc_expired()
        is_inj, reason = self.check_injection(text)
        if not is_inj:
            self.stats["safe_total"] += 1
            return True, "", None

        if len(self._pending) >= self._max_pending:
            # Блокировка — но overflow, не ставим в очередь. Всё равно injection attempt.
            self.stats["injection_blocked_total"] += 1
            logger.warning(
                "memory_injection_blocked_overflow",
                pattern=reason,
                text_preview=text[:200],
                source=source,
                author=author,
                pending_count=len(self._pending),
            )
            return (
                False,
                "⚠️ Слишком много ожидающих подтверждений. Сначала реши существующие.",
                None,
            )

        pending = PendingMemoryWrite(text=text, source=source, author=author, reason=reason)
        self._pending[pending.hash] = pending
        self.stats["injection_blocked_total"] += 1
        logger.warning(
            "memory_injection_staged",
            pattern=reason,
            text_preview=text[:200],
            source=source,
            author=author,
            pending_hash=pending.hash,
        )
        # TODO: owner notification when send_message_to_owner() API exists.
        preview = text[:150] + ("…" if len(text) > 150 else "")
        msg = (
            f"⚠️ Обнаружена potentially persistent инструкция:\n"
            f"`{preview}`\n\n"
            f"Эта запись влияет на ВСЕ будущие ответы Krab. "
            f"Чтобы сохранить, подтверди:\n`!confirm {pending.hash}`\n\n"
            f"TTL: {DEFAULT_TTL_MIN} мин. Причина: {reason}"
        )
        return False, msg, pending

    def confirm(self, hash_code: str) -> tuple[bool, str, Optional[PendingMemoryWrite]]:
        """
        Owner confirms write. Returns (ok, user_message, pending_obj_if_ok).
        """
        self._gc_expired()
        code = hash_code.strip().upper()
        pending = self._pending.get(code)
        if not pending:
            self.stats["confirm_failed_total"] += 1
            logger.warning(
                "memory_injection_confirm_failed",
                attempted_hash=code,
                pending_count=len(self._pending),
            )
            return False, f"❌ Хэш `{code}` не найден или истёк TTL.", None
        del self._pending[code]
        self.stats["confirmed_total"] += 1
        age_min = int((datetime.now(timezone.utc) - pending.created_at).total_seconds() / 60)
        logger.info(
            "memory_injection_confirmed",
            pending_hash=code,
            text_preview=pending.text[:200],
            author=pending.author,
            age_min=age_min,
        )
        return True, f"✅ Подтверждено: `{code}`", pending

    def list_pending(self) -> list[PendingMemoryWrite]:
        """Возвращает список ожидающих подтверждения записей (после GC)."""
        self._gc_expired()
        return list(self._pending.values())


# Module-level singleton — используется из userbot обработчиков.
memory_validator = MemoryInjectionValidator()
