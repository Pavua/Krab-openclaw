"""
Unit-тесты ``src.bootstrap.runtime._warmup_memory_embeddings``.

Покрывают C2 Memory Phase 2 hook:
  * skip при ``KRAB_RAG_PHASE2_ENABLED != "1"`` — embedder не создаётся;
  * вызов ``embed_all_unindexed()`` при включённом флаге;
  * timeout → warning-лог, task не падает;
  * любое исключение embedder'а → warning-лог, task не падает.

Все тесты монкипатчат ``asyncio.sleep`` (убираем 30s задержку) и
``MemoryEmbedder`` (избегаем загрузки Model2Vec / открытия archive.db).

Запуск::

    venv/bin/python -m pytest tests/unit/test_bootstrap_memory_warmup.py -q
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Прогружаем project root, чтобы ``src.*`` импортировалось.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.bootstrap import runtime as bootstrap_runtime  # noqa: E402


@pytest.fixture(autouse=True)
def _skip_startup_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подменяем 30s стартовый sleep на no-op — тесты должны быть быстрыми."""

    async def _noop_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(bootstrap_runtime.asyncio, "sleep", _noop_sleep)


class TestWarmupFeatureFlag:
    def test_warmup_skips_when_phase2_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """KRAB_RAG_PHASE2_ENABLED=0 (default) → embedder даже не импортируется."""
        monkeypatch.delenv("KRAB_RAG_PHASE2_ENABLED", raising=False)

        created = {"count": 0}

        class _Sentinel:
            def __init__(self, *args: object, **kwargs: object) -> None:
                created["count"] += 1

            def embed_all_unindexed(self) -> object:  # pragma: no cover
                created["count"] += 100
                raise AssertionError("embedder must not be called")

        # Подставляем sentinel в будущий import
        import src.core.memory_embedder as mem_embedder

        monkeypatch.setattr(mem_embedder, "MemoryEmbedder", _Sentinel)

        asyncio.run(bootstrap_runtime._warmup_memory_embeddings())

        assert created["count"] == 0

    def test_warmup_skips_when_flag_is_zero_string(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "0")

        import src.core.memory_embedder as mem_embedder

        sentinel = MagicMock(side_effect=AssertionError("must not build"))
        monkeypatch.setattr(mem_embedder, "MemoryEmbedder", sentinel)

        asyncio.run(bootstrap_runtime._warmup_memory_embeddings())
        sentinel.assert_not_called()


class TestWarmupCallsEmbedder:
    def test_warmup_calls_embedder_when_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """KRAB_RAG_PHASE2_ENABLED=1 → embed_all_unindexed() вызван и залогирован."""
        monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "1")

        fake_stats = MagicMock(chunks_processed=7, chunks_skipped=65)
        fake_embedder = MagicMock()
        fake_embedder.embed_all_unindexed.return_value = fake_stats

        import src.core.memory_embedder as mem_embedder

        ctor = MagicMock(return_value=fake_embedder)
        monkeypatch.setattr(mem_embedder, "MemoryEmbedder", ctor)

        asyncio.run(bootstrap_runtime._warmup_memory_embeddings())

        ctor.assert_called_once_with()
        fake_embedder.embed_all_unindexed.assert_called_once_with()


class TestWarmupErrorHandling:
    def test_warmup_timeout_logs_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Если embed_all_unindexed превышает 600s — только warning, без raise."""
        monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "1")

        # Подменяем wait_for на имитацию таймаута — реально 600s ждать нельзя.
        # Закрываем coroutine-аргумент, чтобы не ловить RuntimeWarning.
        async def _raise_timeout(coro: object, *_args: object, **_kwargs: object) -> object:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            raise asyncio.TimeoutError

        monkeypatch.setattr(bootstrap_runtime.asyncio, "wait_for", _raise_timeout)

        import src.core.memory_embedder as mem_embedder

        monkeypatch.setattr(mem_embedder, "MemoryEmbedder", MagicMock())

        warnings: list[tuple[str, dict[str, object]]] = []

        def _capture_warning(event: str, **kw: object) -> None:
            warnings.append((event, kw))

        monkeypatch.setattr(bootstrap_runtime.logger, "warning", _capture_warning)

        # Должно завершиться без исключения.
        asyncio.run(bootstrap_runtime._warmup_memory_embeddings())

        assert any(event == "memory_bootstrap_embed_timeout" for event, _ in warnings)

    def test_warmup_exception_logs_warning_not_raise(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Любое исключение от embedder'а глушится в warning-лог."""
        monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "1")

        class _BoomError(RuntimeError):
            pass

        fake_embedder = MagicMock()
        fake_embedder.embed_all_unindexed.side_effect = _BoomError("model2vec missing")

        import src.core.memory_embedder as mem_embedder

        monkeypatch.setattr(
            mem_embedder,
            "MemoryEmbedder",
            MagicMock(return_value=fake_embedder),
        )

        warnings: list[tuple[str, dict[str, object]]] = []

        def _capture_warning(event: str, **kw: object) -> None:
            warnings.append((event, kw))

        monkeypatch.setattr(bootstrap_runtime.logger, "warning", _capture_warning)

        # Не должно поднять исключение.
        asyncio.run(bootstrap_runtime._warmup_memory_embeddings())

        fail_events = [
            (event, kw) for event, kw in warnings if event == "memory_bootstrap_embed_failed"
        ]
        assert fail_events, "ожидаем warning с кодом memory_bootstrap_embed_failed"
        _, kw = fail_events[0]
        assert kw.get("error_type") == "_BoomError"
        assert "model2vec missing" in str(kw.get("error", ""))
