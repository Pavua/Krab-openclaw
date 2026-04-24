"""
Unit-тесты ``src.bootstrap.runtime._warmup_memory_embeddings``.

Покрывают C2 Memory Phase 2 hook + pre-warm:
  * pre-warm модели всегда выполняется (flag=0 или flag=1) — _ensure_model_loaded
    + dummy encode; это устраняет 1.8s cold first query после toggle flag=1.
  * при ``KRAB_RAG_PHASE2_ENABLED != "1"`` — ``embed_all_unindexed`` НЕ вызван;
  * при ``KRAB_RAG_PHASE2_ENABLED == "1"`` — ``embed_all_unindexed`` вызван;
  * timeout → warning-лог, task не падает;
  * любое исключение embedder'а → warning-лог, task не падает;
  * исключение pre-warm — task не падает, embed_all_unindexed не вызывается.

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


def _make_fake_embedder(
    *,
    dim: int = 256,
    embed_stats: object | None = None,
) -> MagicMock:
    """Фейковый MemoryEmbedder с _model/_ensure_model_loaded/encode/embed_all_unindexed."""
    fake = MagicMock()
    fake._dim = dim
    # _model должен быть truthy чтобы code путь pre-warm делал encode()
    fake._model = MagicMock()
    fake._model.encode = MagicMock(return_value=[[0.0] * dim])
    fake._ensure_model_loaded = MagicMock(return_value=0.0)
    fake.embed_all_unindexed = MagicMock(
        return_value=embed_stats
        if embed_stats is not None
        else MagicMock(chunks_processed=0, chunks_skipped=0)
    )
    return fake


class TestPrewarmAlways:
    def test_prewarm_always_runs_when_flag_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """flag=0 → _ensure_model_loaded + encode вызваны, embed_all_unindexed — нет."""
        monkeypatch.delenv("KRAB_RAG_PHASE2_ENABLED", raising=False)

        fake = _make_fake_embedder()

        import src.core.memory_embedder as mem_embedder

        monkeypatch.setattr(mem_embedder, "MemoryEmbedder", MagicMock(return_value=fake))

        asyncio.run(bootstrap_runtime._warmup_memory_embeddings())

        fake._ensure_model_loaded.assert_called_once()
        fake._model.encode.assert_called_once_with(["warmup"])
        fake.embed_all_unindexed.assert_not_called()

    def test_prewarm_always_runs_when_flag_is_zero_string(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Явный flag=0 — pre-warm всё равно исполняется."""
        monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "0")

        fake = _make_fake_embedder()

        import src.core.memory_embedder as mem_embedder

        monkeypatch.setattr(mem_embedder, "MemoryEmbedder", MagicMock(return_value=fake))

        asyncio.run(bootstrap_runtime._warmup_memory_embeddings())

        fake._ensure_model_loaded.assert_called_once()
        fake.embed_all_unindexed.assert_not_called()

    def test_prewarm_failure_not_fatal(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Исключение при pre-warm → warning, task не падает, embed не вызван."""
        monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "1")

        class _BoomError(RuntimeError):
            pass

        fake = _make_fake_embedder()
        fake._ensure_model_loaded.side_effect = _BoomError("model2vec missing")

        import src.core.memory_embedder as mem_embedder

        monkeypatch.setattr(mem_embedder, "MemoryEmbedder", MagicMock(return_value=fake))

        warnings: list[tuple[str, dict[str, object]]] = []

        def _capture_warning(event: str, **kw: object) -> None:
            warnings.append((event, kw))

        monkeypatch.setattr(bootstrap_runtime.logger, "warning", _capture_warning)

        # Не должно поднять исключение.
        asyncio.run(bootstrap_runtime._warmup_memory_embeddings())

        prewarm_fail = [ev for ev, _ in warnings if ev == "memory_model_prewarm_failed"]
        assert prewarm_fail, "ожидаем warning memory_model_prewarm_failed"
        # Embed не должен запускаться если pre-warm упал.
        fake.embed_all_unindexed.assert_not_called()


class TestWarmupFeatureFlag:
    def test_warmup_skips_embed_when_phase2_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """KRAB_RAG_PHASE2_ENABLED=0 → embed_all_unindexed НЕ вызван (pre-warm — да)."""
        monkeypatch.delenv("KRAB_RAG_PHASE2_ENABLED", raising=False)

        fake = _make_fake_embedder()

        import src.core.memory_embedder as mem_embedder

        monkeypatch.setattr(mem_embedder, "MemoryEmbedder", MagicMock(return_value=fake))

        asyncio.run(bootstrap_runtime._warmup_memory_embeddings())

        fake.embed_all_unindexed.assert_not_called()

    def test_warmup_skips_embed_when_flag_is_zero_string(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "0")

        fake = _make_fake_embedder()

        import src.core.memory_embedder as mem_embedder

        monkeypatch.setattr(mem_embedder, "MemoryEmbedder", MagicMock(return_value=fake))

        asyncio.run(bootstrap_runtime._warmup_memory_embeddings())
        fake.embed_all_unindexed.assert_not_called()


class TestWarmupCallsEmbedder:
    def test_warmup_calls_embedder_when_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """KRAB_RAG_PHASE2_ENABLED=1 → embed_all_unindexed() вызван и залогирован."""
        monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "1")

        fake_stats = MagicMock(chunks_processed=7, chunks_skipped=65)
        fake = _make_fake_embedder(embed_stats=fake_stats)

        import src.core.memory_embedder as mem_embedder

        ctor = MagicMock(return_value=fake)
        monkeypatch.setattr(mem_embedder, "MemoryEmbedder", ctor)

        asyncio.run(bootstrap_runtime._warmup_memory_embeddings())

        ctor.assert_called_once_with()
        fake._ensure_model_loaded.assert_called_once()
        fake.embed_all_unindexed.assert_called_once_with()


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

        fake = _make_fake_embedder()

        import src.core.memory_embedder as mem_embedder

        monkeypatch.setattr(mem_embedder, "MemoryEmbedder", MagicMock(return_value=fake))

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
        """Любое исключение от embed_all_unindexed глушится в warning-лог."""
        monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "1")

        class _BoomError(RuntimeError):
            pass

        fake = _make_fake_embedder()
        fake.embed_all_unindexed.side_effect = _BoomError("embed boom")

        import src.core.memory_embedder as mem_embedder

        monkeypatch.setattr(
            mem_embedder,
            "MemoryEmbedder",
            MagicMock(return_value=fake),
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
        assert "embed boom" in str(kw.get("error", ""))
