# -*- coding: utf-8 -*-
"""
Sentry Performance helpers — lazy/graceful wrappers над sentry_sdk.

Motivation:
- start_transaction/start_span не должны падать, если sentry_sdk не установлен
  (dev env) или инициализация пропущена (SENTRY_DSN пустой).
- Hot-path overhead должен оставаться O(1). Если SDK отсутствует — возвращаем
  no-op контекстный менеджер, который вообще ничего не делает.
- Tags через set_tag() тоже graceful: silent no-op если SDK не подгружен.

Usage:
    from src.core.sentry_perf import start_transaction, start_span, set_tag

    with start_transaction(op="memory.retrieval", name="hybrid_search"):
        set_tag("mode", "hybrid")
        with start_span(op="memory.fts"):
            ...
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


def _get_sdk() -> Any | None:
    """Возвращает sentry_sdk модуль или None если не установлен."""
    try:
        import sentry_sdk  # type: ignore[import-not-found]

        return sentry_sdk
    except Exception:  # noqa: BLE001 - импорт best-effort
        return None


@contextmanager
def start_transaction(op: str, name: str) -> Iterator[Any]:
    """Graceful wrapper над sentry_sdk.start_transaction().

    Если sentry_sdk недоступен — yield None, никаких side-effects.
    """
    sdk = _get_sdk()
    if sdk is None:
        yield None
        return
    try:
        with sdk.start_transaction(op=op, name=name) as txn:
            yield txn
    except Exception:  # noqa: BLE001 - tracing не должен ронять runtime
        yield None


@contextmanager
def start_span(op: str, description: str | None = None) -> Iterator[Any]:
    """Graceful wrapper над sentry_sdk.start_span().

    `description` трансформируется в `name` для современных sentry_sdk
    (>=2.x), где `description` deprecated.
    """
    sdk = _get_sdk()
    if sdk is None:
        yield None
        return
    try:
        # Используем name (новый API); в старом SDK name тоже поддерживается
        # как keyword arg через description-compat shim.
        kwargs: dict[str, Any] = {"op": op}
        if description is not None:
            kwargs["name"] = description
        with sdk.start_span(**kwargs) as span:
            yield span
    except Exception:  # noqa: BLE001
        yield None


def set_tag(key: str, value: str) -> None:
    """Graceful sentry_sdk.set_tag()."""
    sdk = _get_sdk()
    if sdk is None:
        return
    try:
        sdk.set_tag(key, value)
    except Exception:  # noqa: BLE001
        pass
