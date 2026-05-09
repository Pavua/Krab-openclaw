# -*- coding: utf-8 -*-
"""Wave 46-A: тесты MessageCatchupMixin (startup catch-up).

Проверяем:
- persistent state I/O (atomic, fail-open)
- catchup filtering (только id > last_seen)
- catchup persists max id после replay
- empty history handling
- API failure не валит Krab
- owner chat_id resolution через OWNER_NOTIFY_CHAT_ID
- monotonic guarantee (не пишем меньшее значение)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.userbot.message_catchup import (
    MessageCatchupMixin,
    _resolve_max_lookback,
    _resolve_owner_chat_id,
    _resolve_state_path,
)


class _Host(MessageCatchupMixin):
    """Минимальный host для проверки mixin без всего KraabUserbot."""

    def __init__(self, state_path: Path):
        self._state_path = state_path
        self.client = None
        self.me = None
        self._owner_notify_target: int | str = "me"
        self._processed: list[Any] = []

    def _last_seen_state_path(self) -> Path:  # override для tmp_path
        return self._state_path

    async def _process_message(self, message):  # noqa: D401 (mock)
        """Mock: запоминает все processed messages."""
        self._processed.append(message)


def _mk_msg(msg_id: int) -> MagicMock:
    m = MagicMock()
    m.id = msg_id
    return m


# ─── State I/O ──────────────────────────────────────────────────────────────


def test_load_empty_state_returns_empty_dict(tmp_path: Path) -> None:
    host = _Host(tmp_path / "missing.json")
    assert host._load_last_seen() == {}


def test_load_corrupt_json_returns_empty_dict(tmp_path: Path) -> None:
    path = tmp_path / "last.json"
    path.write_text("{broken json", encoding="utf-8")
    host = _Host(path)
    assert host._load_last_seen() == {}


def test_save_and_load_atomic_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "last.json"
    host = _Host(path)
    host._save_last_seen(312322764, 1325461)
    host._save_last_seen(-1003703978531, 42)
    loaded = host._load_last_seen()
    assert loaded[312322764] == 1325461
    assert loaded[-1003703978531] == 42
    # JSON формат
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "312322764" in raw
    assert raw["312322764"]["last_seen_msg_id"] == 1325461
    assert "updated_at_utc" in raw["312322764"]


def test_save_monotonic_guarantee(tmp_path: Path) -> None:
    """_save_last_seen не пишет меньшее значение поверх большего."""
    path = tmp_path / "last.json"
    host = _Host(path)
    host._save_last_seen(100, 50)
    host._save_last_seen(100, 30)  # ниже — должно игнорироваться
    assert host._load_last_seen()[100] == 50
    host._save_last_seen(100, 75)  # выше — должно записаться
    assert host._load_last_seen()[100] == 75


def test_save_ignores_invalid_msg_id(tmp_path: Path) -> None:
    path = tmp_path / "last.json"
    host = _Host(path)
    host._save_last_seen(100, 0)
    host._save_last_seen(100, -5)
    assert host._load_last_seen() == {}


def test_record_seen_message_handles_bad_input(tmp_path: Path) -> None:
    host = _Host(tmp_path / "last.json")
    host._record_seen_message("not-an-int", 5)
    host._record_seen_message(100, "not-an-int")
    host._record_seen_message(100, 0)
    assert host._load_last_seen() == {}


def test_record_seen_message_persists(tmp_path: Path) -> None:
    host = _Host(tmp_path / "last.json")
    host._record_seen_message(312322764, 1325461)
    assert host._load_last_seen()[312322764] == 1325461


# ─── Owner chat resolution ──────────────────────────────────────────────────


def test_resolve_owner_chat_id_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OWNER_NOTIFY_CHAT_ID", "312322764")
    assert _resolve_owner_chat_id() == 312322764


def test_resolve_owner_chat_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OWNER_NOTIFY_CHAT_ID", raising=False)
    # config.OWNER_NOTIFY_CHAT_ID может быть установлено; нам важно что resolve fail-safe
    # если env пустой и config тоже пустой — возвращает None
    from src.config import config

    original = config.OWNER_NOTIFY_CHAT_ID
    config.OWNER_NOTIFY_CHAT_ID = ""
    try:
        assert _resolve_owner_chat_id() is None
    finally:
        config.OWNER_NOTIFY_CHAT_ID = original


def test_catchup_owner_dm_resolves_int_owner_target(tmp_path: Path) -> None:
    host = _Host(tmp_path / "last.json")
    host._owner_notify_target = 312322764
    assert host._resolve_catchup_owner_chat_id() == 312322764


def test_catchup_owner_dm_falls_back_to_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _Host(tmp_path / "last.json")
    host._owner_notify_target = "me"  # fallback
    monkeypatch.setenv("OWNER_NOTIFY_CHAT_ID", "111222333")
    assert host._resolve_catchup_owner_chat_id() == 111222333


# ─── Lookback resolution ────────────────────────────────────────────────────


def test_resolve_max_lookback_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KRAB_STARTUP_CATCHUP_LIMIT", raising=False)
    assert _resolve_max_lookback(20) == 20


def test_resolve_max_lookback_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_LIMIT", "50")
    assert _resolve_max_lookback() == 50


def test_resolve_max_lookback_invalid_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_LIMIT", "garbage")
    assert _resolve_max_lookback(default=15) == 15


def test_resolve_max_lookback_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_LIMIT", "9999")
    assert _resolve_max_lookback() == 200  # clamp


# ─── _catchup_owner_dm: behavioural ─────────────────────────────────────────


def _make_async_history_iter(messages: list[Any]):
    """Создаёт mock async iterator для get_chat_history."""

    async def _gen(*_args, **_kwargs):
        for m in messages:
            yield m

    return _gen


@pytest.mark.asyncio
async def test_catchup_no_owner_chat_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OWNER_NOTIFY_CHAT_ID", raising=False)
    from src.config import config

    original = config.OWNER_NOTIFY_CHAT_ID
    config.OWNER_NOTIFY_CHAT_ID = ""
    try:
        host = _Host(tmp_path / "last.json")
        host._owner_notify_target = "me"
        host.client = MagicMock()
        replayed = await host._catchup_owner_dm(max_lookback=20)
        assert replayed == 0
    finally:
        config.OWNER_NOTIFY_CHAT_ID = original


@pytest.mark.asyncio
async def test_catchup_no_client_returns_zero(tmp_path: Path) -> None:
    host = _Host(tmp_path / "last.json")
    host._owner_notify_target = 312322764
    host.client = None
    replayed = await host._catchup_owner_dm(max_lookback=20)
    assert replayed == 0


@pytest.mark.asyncio
async def test_catchup_filters_seen_messages(tmp_path: Path) -> None:
    """Если last_seen=2, то msgs 1,2 пропускаются, 3,4,5 replay."""
    host = _Host(tmp_path / "last.json")
    host._owner_notify_target = 312322764
    # Заранее заполняем last_seen
    host._save_last_seen(312322764, 2)

    msgs = [_mk_msg(1), _mk_msg(2), _mk_msg(3), _mk_msg(4), _mk_msg(5)]
    client = MagicMock()
    client.get_chat_history = _make_async_history_iter(msgs)
    host.client = client

    replayed = await host._catchup_owner_dm(max_lookback=20)
    assert replayed == 3
    assert [m.id for m in host._processed] == [3, 4, 5]


@pytest.mark.asyncio
async def test_catchup_no_messages_no_errors(tmp_path: Path) -> None:
    host = _Host(tmp_path / "last.json")
    host._owner_notify_target = 312322764
    client = MagicMock()
    client.get_chat_history = _make_async_history_iter([])
    host.client = client
    replayed = await host._catchup_owner_dm(max_lookback=20)
    assert replayed == 0


@pytest.mark.asyncio
async def test_catchup_persists_max_id(tmp_path: Path) -> None:
    host = _Host(tmp_path / "last.json")
    host._owner_notify_target = 312322764

    msgs = [_mk_msg(10), _mk_msg(11), _mk_msg(12)]
    client = MagicMock()
    client.get_chat_history = _make_async_history_iter(msgs)
    host.client = client

    await host._catchup_owner_dm(max_lookback=20)
    assert host._load_last_seen()[312322764] == 12


@pytest.mark.asyncio
async def test_catchup_handles_api_failure_gracefully(tmp_path: Path) -> None:
    host = _Host(tmp_path / "last.json")
    host._owner_notify_target = 312322764

    async def _broken_iter(*_a, **_kw):
        raise RuntimeError("Telegram API down")
        yield  # pragma: no cover (нужен для async-gen синтаксиса)

    client = MagicMock()
    client.get_chat_history = _broken_iter
    host.client = client

    # Не должно raise
    replayed = await host._catchup_owner_dm(max_lookback=20)
    assert replayed == 0


@pytest.mark.asyncio
async def test_catchup_handles_replay_exception(tmp_path: Path) -> None:
    """Если _process_message раняет на одном msg — остальные продолжают."""
    host = _Host(tmp_path / "last.json")
    host._owner_notify_target = 312322764

    msgs = [_mk_msg(10), _mk_msg(11), _mk_msg(12)]
    client = MagicMock()
    client.get_chat_history = _make_async_history_iter(msgs)
    host.client = client

    # Подменяем _process_message чтобы упасть на msg.id == 11
    original_process = host._process_message

    async def _failing_process(msg):
        if msg.id == 11:
            raise ValueError("simulated")
        await original_process(msg)

    host._process_message = _failing_process  # type: ignore[method-assign]

    replayed = await host._catchup_owner_dm(max_lookback=20)
    assert replayed == 2  # 10 и 12 прошли
    # max_id всё равно сохраняется (12, последний successfully replayed)
    assert host._load_last_seen()[312322764] == 12


@pytest.mark.asyncio
async def test_catchup_all_owner_chats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Wave 48-A: явно disable swarm group чтобы сохранить single-chat semantics теста.
    monkeypatch.setenv("KRAB_SWARM_GROUP_ID", "")
    host = _Host(tmp_path / "last.json")
    host._owner_notify_target = 312322764
    msgs = [_mk_msg(5)]
    client = MagicMock()
    client.get_chat_history = _make_async_history_iter(msgs)
    host.client = client
    result = await host._catchup_all_owner_chats()
    assert result == {312322764: 1}


@pytest.mark.asyncio
async def test_run_startup_catchup_safe_never_raises(tmp_path: Path) -> None:
    """Главный entry-point — даже при крахе всё внутри swallowед."""
    host = _Host(tmp_path / "last.json")
    host._owner_notify_target = 312322764
    # Wave 48-A: _run_startup_catchup_safe теперь вызывает _catchup_all_owner_chats.
    host._catchup_all_owner_chats = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("boom")
    )
    # Должно не raise
    await host._run_startup_catchup_safe()


# ─── State path resolution ─────────────────────────────────────────────────


def test_state_path_respects_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KRAB_RUNTIME_STATE_DIR", str(tmp_path))
    p = _resolve_state_path()
    assert p.parent == tmp_path
    assert p.name == "last_seen_messages.json"


def test_state_path_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KRAB_RUNTIME_STATE_DIR", raising=False)
    p = _resolve_state_path()
    assert "krab_runtime_state" in str(p)
    assert p.name == "last_seen_messages.json"
