"""Тесты Wave 24-B: stagger swarm client startup + gate warmup.

Проверяем что:
- между стартами клиентов вставлен asyncio.sleep(1.5)
- warmup `get_dialogs` выполняется только один раз на team (gate)
- второй запуск `_start_swarm_team_clients` пропускает warmup для уже прогретых
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.userbot_bridge import KraabUserbot


class _FakeDialogsIter:
    """Async iterator имитирующий `cl.get_dialogs()`."""

    def __init__(self) -> None:
        self._items = [object(), object()]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop()


def _make_fake_client() -> MagicMock:
    cl = MagicMock()
    cl.start = AsyncMock(return_value=None)
    me = MagicMock(username="swarm_test", id=12345)
    cl.get_me = AsyncMock(return_value=me)
    cl.get_dialogs = MagicMock(return_value=_FakeDialogsIter())
    cl.is_connected = True
    cl.stop = AsyncMock()
    return cl


@pytest.mark.asyncio
async def test_stagger_sleep_between_clients(tmp_path):
    """asyncio.sleep(1.5) должен вызываться для каждого аккаунта."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot._session_workdir = tmp_path

    accounts = {
        "traders": {"session_name": "swarm_traders"},
        "coders": {"session_name": "swarm_coders"},
        "analysts": {"session_name": "swarm_analysts"},
    }

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with patch("src.userbot_bridge.config.load_swarm_team_accounts", return_value=accounts), \
         patch("src.userbot_bridge.Client", side_effect=lambda *a, **kw: _make_fake_client()), \
         patch("src.userbot_bridge.asyncio.sleep", side_effect=fake_sleep), \
         patch("src.userbot_bridge.asyncio.wait_for", new=AsyncMock(return_value=None)):
        started = await bot._start_swarm_team_clients()

    assert len(started) == 3
    # stagger 1.5s после каждого клиента (включая последний)
    stagger_calls = [d for d in sleep_calls if d == 1.5]
    assert len(stagger_calls) == 3


@pytest.mark.asyncio
async def test_warmup_gate_skips_on_second_start(tmp_path):
    """Второй вызов `_start_swarm_team_clients` не должен делать get_dialogs."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot._session_workdir = tmp_path

    accounts = {"traders": {"session_name": "swarm_traders"}}

    # Первый клиент
    cl1 = _make_fake_client()
    # Второй клиент (после реконнекта)
    cl2 = _make_fake_client()
    clients = iter([cl1, cl2])

    with patch("src.userbot_bridge.config.load_swarm_team_accounts", return_value=accounts), \
         patch("src.userbot_bridge.Client", side_effect=lambda *a, **kw: next(clients)), \
         patch("src.userbot_bridge.asyncio.sleep", new=AsyncMock()), \
         patch("src.userbot_bridge.asyncio.wait_for", new=AsyncMock(return_value=None)):
        await bot._start_swarm_team_clients()
        await bot._start_swarm_team_clients()

    # Первый старт: get_dialogs вызван
    assert cl1.get_dialogs.call_count == 1
    # Второй старт: пропущен, т.к. team уже в _swarm_clients_warmed
    assert cl2.get_dialogs.call_count == 0
    assert "traders" in bot._swarm_clients_warmed


@pytest.mark.asyncio
async def test_empty_accounts_returns_empty(tmp_path):
    """Пустой список аккаунтов → пустой dict, без sleep."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot._session_workdir = tmp_path

    with patch("src.userbot_bridge.config.load_swarm_team_accounts", return_value={}), \
         patch("src.userbot_bridge.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        result = await bot._start_swarm_team_clients()

    assert result == {}
    assert mock_sleep.call_count == 0
