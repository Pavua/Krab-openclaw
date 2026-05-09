# -*- coding: utf-8 -*-
"""Wave 46-C: тесты на фильтр self-messages в startup catchup.

Background:
- Wave 46-A добавила _catchup_owner_dm. После рестарта Krab наблюдалось
  ложное NLU-dispatch: catchup поднимал собственные исходящие сообщения
  Krab'а (например "📥 Открытые inbox items..."), и NLU classifier
  сматчил substring "команд" → !swarm dispatch на самого себя.
- Production log evidence (09.05.2026 23:47, 23:56):
    nlu_command_intent_dispatched cmd=!swarm
    user_id=6435872621  (= Krab's own user_id, не owner 312322764)
- Fix: skip msg.outgoing=True и msg.from_user.is_self=True.
  Но max_id tracking сохраняется (включая self), чтобы state продвигался
  и не было reprocessing на каждом restart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.userbot.message_catchup import MessageCatchupMixin


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
    """Создаёт mock-сообщение Pyrogram с настраиваемыми self-флагами."""
    m = MagicMock()
    m.id = msg_id
    m.outgoing = outgoing
    if from_self:
        from_user = MagicMock()
        from_user.is_self = True
        from_user.id = 6435872621  # из production log evidence
        m.from_user = from_user
    else:
        from_user = MagicMock()
        from_user.is_self = False
        from_user.id = 312322764  # owner
        m.from_user = from_user
    return m


def _make_async_history_iter(messages: list[Any]):
    async def _gen(*_args, **_kwargs):
        for m in messages:
            yield m

    return _gen


# ─── Skip outgoing/self ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_catchup_skips_outgoing_messages(tmp_path: Path) -> None:
    """3 outgoing + 2 incoming → _process_message вызван 2 раза."""
    host = _Host(tmp_path / "last.json")
    msgs = [
        _mk_msg(1, outgoing=True),
        _mk_msg(2, outgoing=False),
        _mk_msg(3, outgoing=True),
        _mk_msg(4, outgoing=False),
        _mk_msg(5, outgoing=True),
    ]
    client = MagicMock()
    client.get_chat_history = _make_async_history_iter(msgs)
    host.client = client

    replayed = await host._catchup_owner_dm(max_lookback=20)
    assert replayed == 2
    assert sorted(m.id for m in host._processed) == [2, 4]


@pytest.mark.asyncio
async def test_catchup_skips_self_via_from_user_is_self(tmp_path: Path) -> None:
    """msg.from_user.is_self=True → skip даже если outgoing=False."""
    host = _Host(tmp_path / "last.json")
    msgs = [
        _mk_msg(10, outgoing=False, from_self=True),
        _mk_msg(11, outgoing=False, from_self=False),
    ]
    client = MagicMock()
    client.get_chat_history = _make_async_history_iter(msgs)
    host.client = client

    replayed = await host._catchup_owner_dm(max_lookback=20)
    assert replayed == 1
    assert host._processed[0].id == 11


# ─── State persistence (включая self) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_catchup_persists_max_id_includes_self(tmp_path: Path) -> None:
    """last_seen = max(all msg ids) включая self, чтобы restart не reread их."""
    host = _Host(tmp_path / "last.json")
    msgs = [
        _mk_msg(20, outgoing=False),  # owner — processed
        _mk_msg(21, outgoing=True),  # self — skipped, но id учитывается
        _mk_msg(22, outgoing=True),  # self — skipped, но id учитывается
    ]
    client = MagicMock()
    client.get_chat_history = _make_async_history_iter(msgs)
    host.client = client

    replayed = await host._catchup_owner_dm(max_lookback=20)
    assert replayed == 1  # только owner
    # Но last_seen — 22 (max), иначе следующий restart опять увидит 21,22
    assert host._load_last_seen()[312322764] == 22


@pytest.mark.asyncio
async def test_catchup_persists_max_id_when_only_self(tmp_path: Path) -> None:
    """Если ВСЕ unseen — self, last_seen всё равно продвигается."""
    host = _Host(tmp_path / "last.json")
    msgs = [
        _mk_msg(30, outgoing=True),
        _mk_msg(31, outgoing=True, from_self=True),
        _mk_msg(32, outgoing=True),
    ]
    client = MagicMock()
    client.get_chat_history = _make_async_history_iter(msgs)
    host.client = client

    replayed = await host._catchup_owner_dm(max_lookback=20)
    assert replayed == 0
    assert host._load_last_seen()[312322764] == 32


# ─── Regression test для production bug ─────────────────────────────────────


@pytest.mark.asyncio
async def test_catchup_no_self_dispatch_regression(tmp_path: Path) -> None:
    """Regression test: production bug 09.05.2026.

    Krab's own outgoing inbox listing с substring 'команд' triggered
    NLU classifier на !swarm. После Wave 46-C self-messages не
    проходят в _process_message → NLU не запускается.
    """
    host = _Host(tmp_path / "last.json")

    # Имитация: Krab's own message текст содержит trigger "командам" слово
    self_msg = _mk_msg(1325500, outgoing=True, from_self=True)
    self_msg.text = "📥 Открытые inbox items по командам:\n- traders: 3\n- coders: 5\n- analysts: 2"
    # Owner real message
    owner_msg = _mk_msg(1325501, outgoing=False, from_self=False)
    owner_msg.text = "привет"

    msgs = [self_msg, owner_msg]
    client = MagicMock()
    client.get_chat_history = _make_async_history_iter(msgs)
    host.client = client

    await host._catchup_owner_dm(max_lookback=20)

    # _process_message должен быть вызван ТОЛЬКО на owner_msg
    assert len(host._processed) == 1
    assert host._processed[0].id == 1325501
    # И self message НЕ среди processed
    assert all(not getattr(m, "outgoing", False) for m in host._processed)


# ─── Mixed scenario ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_catchup_mixed_outgoing_and_self_with_seen_filter(tmp_path: Path) -> None:
    """Сложный кейс: last_seen=5, mix outgoing/self/incoming."""
    host = _Host(tmp_path / "last.json")
    host._save_last_seen(312322764, 5)

    msgs = [
        _mk_msg(3, outgoing=False),  # ниже last_seen — отброшен на этапе unseen
        _mk_msg(6, outgoing=True),  # self — skip, max tracked
        _mk_msg(7, outgoing=False),  # owner — process
        _mk_msg(8, outgoing=False, from_self=True),  # self via flag — skip
        _mk_msg(9, outgoing=False),  # owner — process
    ]
    client = MagicMock()
    client.get_chat_history = _make_async_history_iter(msgs)
    host.client = client

    replayed = await host._catchup_owner_dm(max_lookback=20)
    assert replayed == 2
    assert sorted(m.id for m in host._processed) == [7, 9]
    assert host._load_last_seen()[312322764] == 9
