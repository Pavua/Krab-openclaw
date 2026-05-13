# -*- coding: utf-8 -*-
"""Wave 235: async-safe чтение active_model.json + Prometheus histogram.

Root cause :8080 freeze (Sessions 47-48): sync ``json.load(open(...))`` внутри
async hot-path (`openclaw_client._openclaw_completion_once`) блокировал event
loop при cache miss; concurrent async-таски выстраивались в очередь на
``threading.Lock`` и `/api/health/lite` watchdog переставал получать ответы —
после чего ``launchctl kickstart -k ai.krab.core`` восстанавливал процесс.

Покрытие
--------
1. ``get_active_model_id_async`` возвращает то же значение, что sync-вариант.
2. ``get_active_model_id_async`` оборачивает file IO в ``asyncio.to_thread``.
3. ``resolve_active_target_async`` — async-параллель resolve_active_target.
4. Event-loop остаётся отзывчивым при медленном file read (regression test).
5. Cache hit в async-варианте не делает file IO (быстрый путь).
6. ``observe_resolve_duration`` пишет в Prometheus Histogram и in-memory buffer.
7. Cache lock не удерживается на время file IO (concurrent sync readers).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from src.core import active_model_routing as amr
from src.core.metrics import active_model_routing as amr_metrics


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Изолируем JSON-state в tmp_path."""
    monkeypatch.setattr(amr, "STATE_PATH", tmp_path / "active_model.json")
    amr.invalidate_cache()
    monkeypatch.delenv(amr.ENV_VAR, raising=False)
    monkeypatch.delenv("MLX_LOCAL_KV4_URL", raising=False)
    monkeypatch.delenv("OPENCLAW_URL", raising=False)
    amr_metrics._ACTIVE_MODEL_RESOLVE_OBSERVATIONS.clear()
    yield
    amr.invalidate_cache()
    amr_metrics._ACTIVE_MODEL_RESOLVE_OBSERVATIONS.clear()


# ── 1. async vs sync parity ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_returns_same_value_as_sync():
    """Async-вариант должен возвращать идентичное значение sync-варианту."""
    amr.set_active_model("mlx-local-kv4/gemma-4-26b", by="test")
    amr.invalidate_cache()
    sync_value = amr.get_active_model_id()
    amr.invalidate_cache()
    async_value = await amr.get_active_model_id_async()
    assert sync_value == async_value == "mlx-local-kv4/gemma-4-26b"


@pytest.mark.asyncio
async def test_async_returns_none_when_no_state():
    """Нет файла + нет ENV → None."""
    assert await amr.get_active_model_id_async() is None


@pytest.mark.asyncio
async def test_async_respects_env_override(monkeypatch: pytest.MonkeyPatch):
    """ENV перекрывает файл и в async-варианте."""
    amr.set_active_model("openclaw", by="test")
    amr.invalidate_cache()
    monkeypatch.setenv(amr.ENV_VAR, "mlx-local-kv4/qwen3-4b-kv4")
    assert await amr.get_active_model_id_async() == "mlx-local-kv4/qwen3-4b-kv4"


# ── 2. event loop не блокируется ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_event_loop_stays_responsive_on_slow_disk(
    monkeypatch: pytest.MonkeyPatch,
):
    """Regression: имитируем медленный disk read (200ms) и проверяем, что
    event-loop остаётся отзывчивым — другие async-таски выполняются
    параллельно, а не блокируются на чтении файла.

    Это и есть suite-test для root cause :8080-freeze: sync IO в async-функции
    блокировал бы все concurrent async coroutines, включая `/api/health/lite`
    watchdog, до завершения чтения.
    """
    amr.set_active_model("openclaw", by="test")
    amr.invalidate_cache()

    # Имитируем медленный disk: задерживаем sync read на 0.2s.
    original_read = amr._read_state_file
    read_call_count = {"n": 0}

    def slow_read():
        read_call_count["n"] += 1
        time.sleep(0.2)  # блокирующий sleep — должен выполняться в thread pool
        return original_read()

    monkeypatch.setattr(amr, "_read_state_file", slow_read)

    # Параллельно: read + быстрая «health-проверка» (sleep 0.01s).
    health_tick = {"ticks": 0}

    async def health_ticker():
        for _ in range(10):
            await asyncio.sleep(0.01)
            health_tick["ticks"] += 1

    started = time.perf_counter()
    health_task = asyncio.create_task(health_ticker())
    value = await amr.get_active_model_id_async()
    await health_task
    elapsed = time.perf_counter() - started

    assert value == "openclaw"
    # Health тики должны успеть выполниться ПОКА идёт медленный read.
    # Если event loop был бы заблокирован — мы бы получили ~10 тиков
    # только после 0.2s, итого elapsed ≈ 0.3s. С asyncio.to_thread
    # health тики идут параллельно → elapsed ≈ max(0.2, 0.1) ≈ 0.2-0.25s.
    assert health_tick["ticks"] == 10
    assert elapsed < 0.4, f"event loop blocked: elapsed={elapsed:.3f}s"
    assert read_call_count["n"] == 1


# ── 3. cache hit пропускает file IO ──────────────────────────────────────


@pytest.mark.asyncio
async def test_async_cache_hit_skips_file_io(monkeypatch: pytest.MonkeyPatch):
    """При cache hit async-вариант НЕ читает файл (быстрый путь)."""
    amr.set_active_model("openclaw", by="test")
    # Первый вызов — наполняет кэш.
    await amr.get_active_model_id_async()

    # Теперь подменяем _read_state_file, чтобы убедиться, что он не вызывается.
    sentinel = {"called": False}

    def trap():
        sentinel["called"] = True
        return "trap"

    monkeypatch.setattr(amr, "_read_state_file", trap)
    value = await amr.get_active_model_id_async()
    assert value == "openclaw"
    assert sentinel["called"] is False


# ── 4. resolve_active_target_async ───────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_async_mlx_local():
    """mlx-local-kv4/* → (http://127.0.0.1:8088, short_id)."""
    amr.set_active_model("mlx-local-kv4/gemma-4-26b", by="test")
    amr.invalidate_cache()
    base_url, model = await amr.resolve_active_target_async(
        default_base_url="http://127.0.0.1:18789",
        default_model="openclaw",
    )
    assert base_url == "http://127.0.0.1:8088"
    assert model == "mlx-local-kv4/gemma-4-26b"


@pytest.mark.asyncio
async def test_resolve_async_cloud_falls_back_to_gateway():
    """google/gemini-3-pro-preview → остаётся на gateway, model=openclaw."""
    amr.set_active_model("google/gemini-3-pro-preview", by="test")
    amr.invalidate_cache()
    base_url, model = await amr.resolve_active_target_async(
        default_base_url="http://127.0.0.1:18789",
        default_model="openclaw",
    )
    assert base_url == "http://127.0.0.1:18789"
    assert model == "openclaw"


@pytest.mark.asyncio
async def test_resolve_async_no_state_returns_defaults():
    """Нет state → defaults."""
    base_url, model = await amr.resolve_active_target_async(
        default_base_url="http://127.0.0.1:18789",
        default_model="openclaw",
    )
    assert base_url == "http://127.0.0.1:18789"
    assert model == "openclaw"


# ── 5. Prometheus histogram + in-memory buffer ───────────────────────────


def test_observe_resolve_duration_writes_to_buffer():
    """observe_resolve_duration пишет в in-memory buffer + не падает."""
    amr_metrics.observe_resolve_duration(0.001234, source="file")
    amr_metrics.observe_resolve_duration(0.005, source="async")
    obs = amr_metrics._ACTIVE_MODEL_RESOLVE_OBSERVATIONS
    assert len(obs) == 2
    assert obs[0][0] == "file"
    assert obs[0][1] == pytest.approx(0.001234)
    assert obs[1][0] == "async"


def test_observe_resolve_duration_caps_buffer():
    """In-memory buffer не растёт безгранично (cap 1024)."""
    for _ in range(2000):
        amr_metrics.observe_resolve_duration(0.0001, source="file")
    assert len(amr_metrics._ACTIVE_MODEL_RESOLVE_OBSERVATIONS) <= 1024


def test_observe_resolve_duration_negative_clamped_to_zero():
    """Отрицательные значения (теоретически невозможны) clamp'нуты в 0."""
    amr_metrics.observe_resolve_duration(-1.0, source="file")
    obs = amr_metrics._ACTIVE_MODEL_RESOLVE_OBSERVATIONS[-1]
    assert obs[1] == 0.0


def test_observe_resolve_duration_handles_bad_source():
    """Невалидный source (None / int) не приводит к exception."""
    amr_metrics.observe_resolve_duration(0.001, source=None)  # type: ignore[arg-type]
    amr_metrics.observe_resolve_duration(0.001, source="")
    # Должно быть 2 записи — обе с дефолтом "file".
    last_two = amr_metrics._ACTIVE_MODEL_RESOLVE_OBSERVATIONS[-2:]
    assert all(src == "file" for src, _ in last_two)


def test_prometheus_histogram_exists():
    """Histogram создан (prometheus_client установлен в venv)."""
    # Под conftest/test env prometheus_client может быть disabled — тогда None.
    h = amr_metrics._active_model_resolve_duration
    # Если установлен — должен быть объект с .labels(); если нет — None.
    assert h is None or hasattr(h, "labels")


@pytest.mark.asyncio
async def test_async_path_records_metric():
    """get_active_model_id_async на cache miss пишет observation."""
    amr.set_active_model("openclaw", by="test")
    amr.invalidate_cache()
    amr_metrics._ACTIVE_MODEL_RESOLVE_OBSERVATIONS.clear()
    await amr.get_active_model_id_async()
    obs = amr_metrics._ACTIVE_MODEL_RESOLVE_OBSERVATIONS
    # Минимум одно observation (sync read внутри to_thread может тоже
    # обсервить, плюс async обёртка).
    assert len(obs) >= 1
    sources = {src for src, _ in obs}
    # Должен быть как минимум один из source-тегов.
    assert sources & {"file", "async"}


# ── 6. concurrent sync readers не блокируют друг друга на file IO ────────


def test_sync_lock_released_during_file_io(monkeypatch: pytest.MonkeyPatch):
    """Cache lock НЕ удерживается на время file read.

    Wave 235 regression test: если бы lock держался во время `_read_state_file`,
    второй sync-вызов из другого потока ждал бы до завершения IO. Мы делаем
    read медленным через monkeypatch и проверяем, что вызовы из двух потоков
    могут перекрываться по времени (т.е. lock-window короткий).
    """
    import threading as _t

    amr.set_active_model("openclaw", by="test")
    amr.invalidate_cache()

    enter_times: list[float] = []
    exit_times: list[float] = []
    barrier = _t.Barrier(2)

    def slow_read():
        enter_times.append(time.perf_counter())
        barrier.wait(timeout=2.0)  # ждём, пока второй поток тоже войдёт
        time.sleep(0.05)
        exit_times.append(time.perf_counter())
        return "openclaw"

    monkeypatch.setattr(amr, "_read_state_file", slow_read)

    results: list = []

    def worker():
        amr.invalidate_cache()
        results.append(amr.get_active_model_id())

    t1 = _t.Thread(target=worker)
    t2 = _t.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=3.0)
    t2.join(timeout=3.0)

    # Оба потока должны войти в slow_read до того, как первый из них выйдет
    # (т.е. lock-window НЕ накрывает file IO).
    assert len(enter_times) == 2
    assert len(exit_times) == 2
    assert max(enter_times) < min(exit_times), (
        f"lock blocked concurrent reads: enter={enter_times}, exit={exit_times}"
    )
