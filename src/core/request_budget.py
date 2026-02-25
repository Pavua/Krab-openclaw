# -*- coding: utf-8 -*-
"""
Request Budget Guard.

Роль модуля:
    Единый бюджет времени на запрос route_query/route_stream.
    Устраняет зависания «🤔 Думаю...» при деградации cloud/local каналов.
    Применяется во ВСЕХ режимах (auto, force_cloud, force_local) — не только force_cloud.

Проблема, которую решает:
    До R24 fail-fast работал только в force_cloud через `deadline = time.monotonic() + Ns`.
    В auto-режиме при деградации cloud пользователь ждал indefinitely разворачивания
    всех kандидатов. RequestBudgetGuard унифицирует таймаут для всех веток.

Использование:
    async with RequestBudgetGuard(total_sec=40, label="route_query:chat") as budget:
        try:
            result = await cloud_call(timeout=budget.per_call_sec)
        except BudgetExceededError as exc:
            return f"❌ Время ожидания превышено ({exc.reason})."

    # Или явная проверка в цикле кандидатов:
    budget.checkpoint("before_candidate_3")  # → BudgetExceededError если истёк

Зачем эти числа (ADR_R24_routing_stability.md):
    - total_sec=40  : разумная верхняя граница для Telegram ответа
                      (Telegram показывает «typing» 5-10с, после 60с UX падает)
    - per_call_sec=22: максимальный single HTTP call к cloud API
                       (менее 22с — слишком мало для stream start, более 22с — UX страдает)
"""

from __future__ import annotations

import time
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """
    Поднимается когда бюджет времени на запрос исчерпан.

    Attributes:
        reason  : человекочитаемая причина (например, 'route_query:cloud_candidate_3')
        elapsed : фактически затраченное время (секунды)
        total   : полный бюджет (секунды)
    """

    def __init__(self, reason: str, elapsed: float, total: float) -> None:
        self.reason = reason
        self.elapsed = elapsed
        self.total = total
        super().__init__(
            f"Budget exceeded [{reason}]: {elapsed:.1f}s / {total:.1f}s"
        )


class RequestBudgetGuard:
    """
    Context manager — бюджет времени на запрос.

    Параметры (можно также передать через `from_config`):
        total_sec     — полный бюджет секунд на запрос (дефолт 40)
        per_call_sec  — лимит на один HTTP-вызов внутри бюджета (дефолт 22)
        label         — метка для логирования и BudgetExceededError.reason
    """

    def __init__(
        self,
        total_sec: float = 40.0,
        per_call_sec: float = 22.0,
        label: str = "request",
    ) -> None:
        self.total_sec = float(total_sec)
        self.per_call_sec = float(per_call_sec)
        self.label = label
        self._start: float = 0.0

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any] | None = None,
        label: str = "request",
        *,
        override_total: float | None = None,
    ) -> "RequestBudgetGuard":
        """
        Фабричный метод: читает total_sec и per_call_sec из конфига.

        Ключи конфига:
            CLOUD_FAIL_FAST_BUDGET_SECONDS   (дефолт 40)
            CLOUD_REQUEST_TIMEOUT_SECONDS    (дефолт 22)
        """
        cfg = config or {}
        try:
            total = max(5, float(cfg.get("CLOUD_FAIL_FAST_BUDGET_SECONDS", 40)))
        except (ValueError, TypeError):
            total = 40.0
        if override_total is not None:
            total = float(override_total)
        try:
            per_call = max(2, float(cfg.get("CLOUD_REQUEST_TIMEOUT_SECONDS", 22)))
        except (ValueError, TypeError):
            per_call = 22.0
        return cls(total_sec=total, per_call_sec=per_call, label=label)

    # ─────────────────────────────────────────────────────────────────── #
    # Context manager API
    # ─────────────────────────────────────────────────────────────────── #

    async def __aenter__(self) -> "RequestBudgetGuard":
        self._start = time.monotonic()
        # R25-core: positional args — совместимость с stdlib logging (не structlog).
        # structlog принимает kwargs, але stdlib logging — нет, что давало TypeError.
        logger.debug("Budget started: label=%s total_sec=%.1f", self.label, self.total_sec)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        elapsed = time.monotonic() - self._start
        if exc_type is BudgetExceededError:
            # R25-core: positional format-строки — совместимость с stdlib logging.
            logger.warning(
                "Request budget exceeded: label=%s elapsed=%.2fs total=%.1fs",
                self.label,
                round(elapsed, 2),
                self.total_sec,
            )
            return False  # Не подавляем, даём всплыть вверх
        logger.debug(
            "Budget finished: label=%s elapsed=%.2fs ok=%s",
            self.label,
            round(elapsed, 2),
            exc_type is None,
        )
        return False

    # ─────────────────────────────────────────────────────────────────── #
    # Публичный API
    # ─────────────────────────────────────────────────────────────────── #

    def remaining(self) -> float:
        """Остаток бюджета в секундах. 0.0 если исчерпан."""
        if self._start == 0.0:
            return self.total_sec
        elapsed = time.monotonic() - self._start
        return max(0.0, self.total_sec - elapsed)

    def elapsed(self) -> float:
        """Затраченное время с момента __aenter__."""
        if self._start == 0.0:
            return 0.0
        return time.monotonic() - self._start

    def is_exceeded(self) -> bool:
        """True если бюджет исчерпан."""
        return self.remaining() <= 0.0

    def checkpoint(self, reason: str = "") -> None:
        """
        Проверяет, не исчерпан ли бюджет.
        Если исчерпан — поднимает BudgetExceededError.

        Вызывать в начале каждой итерации цикла кандидатов:
            for candidate in candidates:
                budget.checkpoint(f"candidate:{candidate}")
                ...
        """
        if self.is_exceeded():
            raise BudgetExceededError(
                reason=f"{self.label}:{reason}" if reason else self.label,
                elapsed=self.elapsed(),
                total=self.total_sec,
            )

    @property
    def per_call_sec(self) -> float:
        """Лимит на один HTTP-вызов (не более остатка бюджета)."""
        return self._per_call_sec

    @per_call_sec.setter
    def per_call_sec(self, value: float) -> None:
        self._per_call_sec = float(value)

    def effective_call_timeout(self) -> float:
        """
        Возвращает min(per_call_sec, remaining()) — таймаут для следующего call.
        Используется как аргумент timeout_seconds в chat_completions().
        """
        remaining = self.remaining()
        if remaining <= 0.0:
            return 0.1  # Почти ноль — следующий checkpoint сработает
        return min(self._per_call_sec, remaining)
