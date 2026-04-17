# -*- coding: utf-8 -*-
"""
Memory Injection Validator — блокирует persistent инструкции до подтверждения.

Проблема: MEMORY.md/USER.md в OpenClaw workspace инжектятся в каждую LLM-сессию
как bootstrap context. Юзер может запросить через !remember сохранение
инструкции "всегда делай X", которая станет влиять на все ответы Krab.

Решение: регулярные паттерны injection блокируются до явного !confirm <hash>
от владельца (owner_only).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

# Regex-паттерны injection (ru+en). re.IGNORECASE + unicode.
_INJECTION_PATTERNS_RAW = [
    r"\bвсегда\b",
    r"\bв\s+каждом\s+(ответе|сообщении)\b",
    r"\bкаждый\s+раз\b",
    r"\bпосле\s+каждого\b",
    r"\bпиши\s+только\b",
    r"\bдобавляй\s+(фразу|suffix|prefix|суффикс|префикс)\b",
    r"\bзаканчивай\s+(фразой|словом|каждый)\b",
    r"\bникогда\s+не\b",
    r"\balways\b",
    r"\bin\s+each\s+response\b",
    r"\bevery\s+time\b",
    r"\bafter\s+every\b",
    r"\bwrite\s+only\b",
    r"\bnever\b",
    r"\badd\s+(phrase|suffix|prefix)\b",
]
_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE | re.UNICODE) for p in _INJECTION_PATTERNS_RAW
]

# Allowlist: "always use Python 3.13" — legitimate. Контекстные слова рядом с "всегда/always".
_LEGIT_KEYWORDS = {
    "python", "rust", "golang", "typescript", "javascript", "node", "bash",
    "version", "протокол", "protocol", "config", "конфиг", "setting", "настройка",
    "use", "используй", "verify", "проверяй", "check", "follow",
    "тесты", "tests", "pytest", "ruff", "линт", "lint", "format",
}

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
    """

    def __init__(self, max_pending: int = DEFAULT_MAX_PENDING):
        self._pending: dict[str, PendingMemoryWrite] = {}
        self._max_pending = max_pending

    def check_injection(self, text: str) -> tuple[bool, str]:
        """
        Возвращает (is_suspicious, reason).

        is_suspicious=True → требует !confirm перед записью.
        """
        if not text or not text.strip():
            return False, ""

        # NFKC-нормализация против bypass через ZWSP / homoglyphs / зеркальные юникод-формы.
        normalized = unicodedata.normalize("NFKC", text)
        lowered = normalized.lower()
        for pat in _INJECTION_PATTERNS:
            m = pat.search(lowered)
            if not m:
                continue
            # Контекст ±50 chars — если легитимные keywords рядом, пропускаем.
            start = max(0, m.start() - 50)
            end = min(len(lowered), m.end() + 50)
            ctx = lowered[start:end]
            if any(kw in ctx for kw in _LEGIT_KEYWORDS):
                continue
            return True, f"injection_pattern: {pat.pattern}"
        return False, ""

    def _gc_expired(self) -> int:
        """Чистит истёкшие pending-записи. Возвращает количество удалённых."""
        now = datetime.now(timezone.utc)
        before = len(self._pending)
        self._pending = {
            h: p for h, p in self._pending.items() if not p.is_expired(now)
        }
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
            return True, "", None

        if len(self._pending) >= self._max_pending:
            return (
                False,
                "⚠️ Слишком много ожидающих подтверждений. Сначала реши существующие.",
                None,
            )

        pending = PendingMemoryWrite(
            text=text, source=source, author=author, reason=reason
        )
        self._pending[pending.hash] = pending
        preview = text[:150] + ("…" if len(text) > 150 else "")
        msg = (
            f"⚠️ Обнаружена potentially persistent инструкция:\n"
            f"`{preview}`\n\n"
            f"Эта запись влияет на ВСЕ будущие ответы Krab. "
            f"Чтобы сохранить, подтверди:\n`!confirm {pending.hash}`\n\n"
            f"TTL: {DEFAULT_TTL_MIN} мин. Причина: {reason}"
        )
        return False, msg, pending

    def confirm(
        self, hash_code: str
    ) -> tuple[bool, str, Optional[PendingMemoryWrite]]:
        """
        Owner confirms write. Returns (ok, user_message, pending_obj_if_ok).
        """
        self._gc_expired()
        code = hash_code.strip().upper()
        pending = self._pending.get(code)
        if not pending:
            return False, f"❌ Хэш `{code}` не найден или истёк TTL.", None
        del self._pending[code]
        return True, f"✅ Подтверждено: `{code}`", pending

    def list_pending(self) -> list[PendingMemoryWrite]:
        """Возвращает список ожидающих подтверждения записей (после GC)."""
        self._gc_expired()
        return list(self._pending.values())


# Module-level singleton — используется из userbot обработчиков.
memory_validator = MemoryInjectionValidator()
