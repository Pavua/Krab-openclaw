# -*- coding: utf-8 -*-
"""Wave 56-F: тесты параллельного startup catchup (asyncio.gather + Semaphore).

Coverage:
- test_catchup_runs_concurrently         — 3 chats с delays, total ≈ longest (не сумма)
- test_catchup_semaphore_limits_concurrency — concurrency=2 с 5 chats, только 2 одновременно
- test_catchup_per_chat_exception_isolated — chat 2 raises, chats 1+3 завершаются нормально
- test_catchup_results_preserve_order    — порядок итерации dict совпадает с targets
- test_concurrency_env_override          — KRAB_STARTUP_CATCHUP_CONCURRENCY=5 respected
- test_concurrency_clamped_to_range      — env=20 → clamp к 10; env=0 → clamp к 1
- test_concurrency_invalid_env_uses_default — нечисловое значение → default 3
- test_concurrency_default_is_three      — без env default=3
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.userbot.message_catchup import (
    MessageCatchupMixin,
    _resolve_catchup_concurrency,
)

# ─── Host fixture ───────────────────────────────────────────────────────────


class _Host(MessageCatchupMixin):
    """Минимальный host для тестирования mixin."""

    def __init__(self, state_path: Path):
        self._state_path = state_path
        self.client = None
        self.me = None
        self._owner_notify_target: int | str = 312322764
        self._processed: list[Any] = []

    def _last_seen_state_path(self) -> Path:
        return self._state_path

    async def _process_message(self, message):  # noqa: D401
        self._processed.append(message)


def _mk_msg(
    msg_id: int,
    *,
    outgoing: bool = False,
    from_self: bool = False,
) -> MagicMock:
    m = MagicMock()
    m.id = msg_id
    m.outgoing = outgoing
    if from_self:
        from_user = MagicMock()
        from_user.is_self = True
        m.from_user = from_user
    else:
        m.from_user = None
    return m


def _make_async_history(per_chat: dict[int, list[Any]]):
    """Async-iterator factory без задержек."""

    async def _gen(chat_id, *_a, **_kw):
        for m in per_chat.get(int(chat_id), []):
            yield m

    return _gen


def _make_delayed_async_history(delays: dict[int, float]):
    """Async-iterator factory с asyncio.sleep задержкой per chat.

    Каждый чат имитирует slow get_chat_history. Возвращает один msg_id=chat_id.
    """

    async def _gen(chat_id, *_a, **_kw):
        cid = int(chat_id)
        await asyncio.sleep(delays.get(cid, 0.0))
        yield _mk_msg(cid)

    return _gen


# ─── _resolve_catchup_concurrency unit tests ────────────────────────────────


def test_concurrency_default_is_three(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без env default возвращает 3."""
    monkeypatch.delenv("KRAB_STARTUP_CATCHUP_CONCURRENCY", raising=False)
    assert _resolve_catchup_concurrency() == 3


def test_concurrency_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_STARTUP_CATCHUP_CONCURRENCY=5 → 5."""
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CONCURRENCY", "5")
    assert _resolve_catchup_concurrency() == 5


def test_concurrency_clamped_to_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """env=20 → clamp к 10; env=0 → clamp к 1."""
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CONCURRENCY", "20")
    assert _resolve_catchup_concurrency() == 10

    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CONCURRENCY", "0")
    assert _resolve_catchup_concurrency() == 1

    # Отрицательное → 1
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CONCURRENCY", "-5")
    assert _resolve_catchup_concurrency() == 1


def test_concurrency_invalid_env_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Нечисловое значение → default 3."""
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CONCURRENCY", "not-a-number")
    assert _resolve_catchup_concurrency() == 3

    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CONCURRENCY", "")
    assert _resolve_catchup_concurrency() == 3


# ─── Concurrency и timing тесты ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_catchup_runs_concurrently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """3 chats с delays 0.05s каждый — parallel total ≈ 0.05s (не 0.15s).

    Serial total ≈ 3 × 0.05 = 0.15s. Parallel ≈ 0.05s (longest single).
    Порог: total < 0.12s — строго меньше двух serial intervals.
    """
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "100,200,300")
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CONCURRENCY", "3")  # все одновременно
    host = _Host(tmp_path / "s.json")

    delays = {100: 0.05, 200: 0.05, 300: 0.05}
    client = MagicMock()
    client.get_chat_history = _make_delayed_async_history(delays)
    host.client = client

    t0 = time.monotonic()
    result = await host._catchup_all_owner_chats()
    elapsed = time.monotonic() - t0

    # Все chats caught up
    assert set(result.keys()) == {100, 200, 300}
    assert all(v == 1 for v in result.values())

    # Параллельный run — существенно быстрее суммы delays
    assert elapsed < 0.12, f"Expected concurrent run, got elapsed={elapsed:.3f}s"


@pytest.mark.asyncio
async def test_catchup_semaphore_limits_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """concurrency=2 с 5 chats: максимум 2 одновременно.

    Проверяем через счётчик active coroutines в момент каждого sleep.
    """
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "1,2,3,4,5")
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CONCURRENCY", "2")
    host = _Host(tmp_path / "s.json")

    # Максимально наблюдаемое число активных одновременно
    max_concurrent = 0
    active = 0

    async def _slow_gen(chat_id, *_a, **_kw):
        nonlocal active, max_concurrent
        active += 1
        if active > max_concurrent:
            max_concurrent = active
        await asyncio.sleep(0.02)  # имитируем network latency
        yield _mk_msg(int(chat_id))
        active -= 1

    client = MagicMock()
    client.get_chat_history = _slow_gen
    host.client = client

    result = await host._catchup_all_owner_chats()

    assert set(result.keys()) == {1, 2, 3, 4, 5}
    # Семафор должен был ограничить до 2
    assert max_concurrent <= 2, f"Expected max 2 concurrent, got {max_concurrent}"


# ─── Exception isolation ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_catchup_per_chat_exception_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chat 2 raises RuntimeError — chats 1 и 3 завершаются успешно.

    Wave 56-F: return_exceptions=True в gather гарантирует изоляцию.
    """
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "1,2,3")
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CONCURRENCY", "3")
    host = _Host(tmp_path / "s.json")

    async def _gen(chat_id, *_a, **_kw):
        cid = int(chat_id)
        if cid == 2:
            raise RuntimeError("Telegram flake for chat 2")
        yield _mk_msg(cid)

    client = MagicMock()
    client.get_chat_history = _gen
    host.client = client

    result = await host._catchup_all_owner_chats()

    # Chat 1 и 3 — success
    assert result[1] == 1
    assert result[3] == 1
    # Chat 2 — failure → 0 (не propagated)
    assert result[2] == 0
    # Только msgs из chat 1 и 3 попали в _processed
    assert {m.id for m in host._processed} == {1, 3}


# ─── Order preservation ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_catchup_results_preserve_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Порядок ключей в result совпадает с порядком targets.

    asyncio.gather гарантирует порядок результатов по порядку coroutines.
    """
    # Порядок намеренно нелинейный
    targets_csv = "300,100,500,200"
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", targets_csv)
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CONCURRENCY", "4")
    host = _Host(tmp_path / "s.json")

    # Разные задержки чтобы chat с малым id мог завершиться позже
    delays = {300: 0.03, 100: 0.01, 500: 0.04, 200: 0.02}

    async def _gen(chat_id, *_a, **_kw):
        cid = int(chat_id)
        await asyncio.sleep(delays[cid])
        yield _mk_msg(cid)

    client = MagicMock()
    client.get_chat_history = _gen
    host.client = client

    result = await host._catchup_all_owner_chats()

    # Порядок ключей dict должен совпадать с targets_csv
    assert list(result.keys()) == [300, 100, 500, 200]
