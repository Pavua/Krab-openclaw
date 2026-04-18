"""
Регрессионный тест: handle_confirm и handle_bench должны быть доступны
для прямого импорта из command_handlers (Wave 29-E).

ImportError на эти имена вызывал spam в krab_launchd.err.log.
"""
from __future__ import annotations

import inspect


def test_handle_confirm_importable() -> None:
    """handle_confirm должна импортироваться и быть корутиной."""
    from src.handlers.command_handlers import handle_confirm  # noqa: PLC0415

    assert callable(handle_confirm)
    assert inspect.iscoroutinefunction(handle_confirm)


def test_handle_bench_importable() -> None:
    """handle_bench должна импортироваться и быть корутиной."""
    from src.handlers.command_handlers import handle_bench  # noqa: PLC0415

    assert callable(handle_bench)
    assert inspect.iscoroutinefunction(handle_bench)


def test_handlers_init_exports_both() -> None:
    """Пакет src.handlers должен реэкспортировать обе функции."""
    from src.handlers import handle_bench, handle_confirm  # noqa: PLC0415

    assert callable(handle_confirm)
    assert callable(handle_bench)
