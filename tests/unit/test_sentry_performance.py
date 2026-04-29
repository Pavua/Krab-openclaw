# -*- coding: utf-8 -*-
"""
tests/unit/test_sentry_performance.py

Проверяет, что Sentry Performance Monitoring корректно подключён:
1. `sentry_sdk.init()` получает `traces_sample_rate` и `profiles_sample_rate`
   (с default 0.1 или из env).
2. `HybridRetriever.search()` оборачивает свой body в sentry transaction
   через `sentry_perf.start_transaction` контекстный менеджер.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. sentry_sdk.init() получает traces_sample_rate + profiles_sample_rate
# ---------------------------------------------------------------------------


def test_sentry_sdk_init_includes_traces_sample_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    """init_sentry() пробрасывает traces_sample_rate и profiles_sample_rate
    в sentry_sdk.init(). Значения читаются из env SENTRY_TRACES_SAMPLE_RATE /
    SENTRY_PROFILES_SAMPLE_RATE; fallback на 0.1 в production, 1.0 в dev.
    """
    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/123")
    monkeypatch.setenv("KRAB_ENV", "production")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")
    monkeypatch.setenv("SENTRY_PROFILES_SAMPLE_RATE", "0.5")

    mock_sdk = MagicMock()
    with patch.dict(
        "sys.modules", {"sentry_sdk": mock_sdk, "sentry_sdk.integrations.logging": MagicMock()}
    ):
        import importlib

        import src.bootstrap.sentry_init as _mod

        importlib.reload(_mod)
        result = _mod.init_sentry()

    assert result is True
    mock_sdk.init.assert_called_once()
    kwargs = mock_sdk.init.call_args.kwargs
    # Оба ключа должны присутствовать и соответствовать env-override.
    assert kwargs["traces_sample_rate"] == pytest.approx(0.25)
    assert kwargs["profiles_sample_rate"] == pytest.approx(0.5)


def test_sentry_sdk_init_defaults_to_10_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без env-override traces/profiles_sample_rate=0.1 в production."""
    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/123")
    monkeypatch.setenv("KRAB_ENV", "production")
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    monkeypatch.delenv("SENTRY_PROFILES_SAMPLE_RATE", raising=False)

    mock_sdk = MagicMock()
    with patch.dict(
        "sys.modules", {"sentry_sdk": mock_sdk, "sentry_sdk.integrations.logging": MagicMock()}
    ):
        import importlib

        import src.bootstrap.sentry_init as _mod

        importlib.reload(_mod)
        _mod.init_sentry()

    kwargs = mock_sdk.init.call_args.kwargs
    assert kwargs["traces_sample_rate"] == pytest.approx(0.1)
    assert kwargs["profiles_sample_rate"] == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# 2. HybridRetriever.search() оборачивает retrieval в sentry transaction
# ---------------------------------------------------------------------------


def test_memory_retrieval_wraps_in_transaction() -> None:
    """`HybridRetriever.search()` должен открыть sentry transaction
    op='memory.retrieval' перед вызовом `_search_impl`. Graceful: если
    sentry_sdk недоступен — транзакция no-op, но sentry_perf.start_transaction
    всё равно вызывается как context manager.
    """
    import src.core.memory_retrieval as mr

    retr = mr.HybridRetriever.__new__(mr.HybridRetriever)  # bypass __init__
    # Подменяем _search_impl на простой stub, чтобы не запускать БД-путь.
    retr._search_impl = MagicMock(return_value=[])  # type: ignore[attr-defined]

    # Мокаем sentry_perf helpers, чтобы проверить вызов.
    fake_txn = MagicMock()
    fake_txn.__enter__ = MagicMock(return_value=None)
    fake_txn.__exit__ = MagicMock(return_value=False)
    with (
        patch.object(mr, "_sentry_txn", return_value=fake_txn) as start_txn_mock,
        patch.object(mr, "_sentry_tag") as tag_mock,
    ):
        result = retr.search("test query", chat_id="123")

    assert result == []
    # Транзакция открывалась с правильным op/name.
    start_txn_mock.assert_called_once()
    call_kwargs = start_txn_mock.call_args.kwargs
    assert call_kwargs.get("op") == "memory.retrieval"
    assert call_kwargs.get("name") == "hybrid_search"
    # Context manager был "использован" (enter + exit).
    fake_txn.__enter__.assert_called_once()
    fake_txn.__exit__.assert_called_once()
    # Теги chat_id / decay_mode проставлены.
    tagged_keys = {c.args[0] for c in tag_mock.call_args_list}
    assert "chat_id" in tagged_keys
    assert "decay_mode" in tagged_keys
    # _search_impl вызывается с ожидаемыми kwargs.
    retr._search_impl.assert_called_once()
