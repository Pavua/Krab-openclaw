# -*- coding: utf-8 -*-
"""Wave 48-A: тесты multi-chat startup catchup.

Background:
- Wave 46-A покрывал только owner DM (resolve через OWNER_NOTIFY_CHAT_ID).
- Если Krab restart происходит во время swarm session, swarm messages
  могут быть lost — никто не реагирует, дальнейшие team interactions
  срываются.
- Wave 48-A: catchup расширен на multiple chats (owner DM + Krab Swarm
  group + configurable list через ``KRAB_STARTUP_CATCHUP_CHATS``).

Coverage:
- iterate всех target chats (3 mocked → 3 client calls)
- per-chat resilience (один chat fails — остальные продолжают)
- env override ``KRAB_STARTUP_CATCHUP_CHATS`` полностью заменяет defaults
- невалидные id silently skipped + warning
- per-chat persistent state (last_seen_messages.json содержит entries
  для всех caught-up chats)
- Wave 46-C self-filter работает per chat
- финальный structured log включает per-chat counts
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.userbot.message_catchup import (
    _DEFAULT_SWARM_GROUP_ID,
    MessageCatchupMixin,
    _parse_catchup_chats_env,
    _resolve_swarm_group_id,
)


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


def _make_per_chat_history(per_chat: dict[int, list[Any]]):
    """Async iterator factory: возвращает разные msgs для разных chat_ids."""

    async def _gen(chat_id, *_args, **_kwargs):
        for m in per_chat.get(int(chat_id), []):
            yield m

    return _gen


# ─── Resolver helpers ───────────────────────────────────────────────────────


def test_resolve_swarm_group_id_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KRAB_SWARM_GROUP_ID", raising=False)
    assert _resolve_swarm_group_id() == _DEFAULT_SWARM_GROUP_ID


def test_resolve_swarm_group_id_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_SWARM_GROUP_ID", "-100999")
    assert _resolve_swarm_group_id() == -100999


def test_resolve_swarm_group_id_disabled_via_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KRAB_SWARM_GROUP_ID", "")
    assert _resolve_swarm_group_id() is None


def test_resolve_swarm_group_id_invalid_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KRAB_SWARM_GROUP_ID", "not-a-number")
    assert _resolve_swarm_group_id() == _DEFAULT_SWARM_GROUP_ID


def test_parse_catchup_chats_env_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "100,200,300")
    assert _parse_catchup_chats_env() == [100, 200, 300]


def test_parse_catchup_chats_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KRAB_STARTUP_CATCHUP_CHATS", raising=False)
    assert _parse_catchup_chats_env() is None


def test_parse_catchup_chats_env_skips_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "100,abc,200,,300")
    assert _parse_catchup_chats_env() == [100, 200, 300]


# ─── Target resolution ──────────────────────────────────────────────────────


def test_resolve_target_chats_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KRAB_STARTUP_CATCHUP_CHATS", raising=False)
    monkeypatch.delenv("KRAB_SWARM_GROUP_ID", raising=False)
    host = _Host(tmp_path / "s.json")
    host._owner_notify_target = 312322764
    targets = host._resolve_catchup_target_chats()
    assert 312322764 in targets
    assert _DEFAULT_SWARM_GROUP_ID in targets


def test_resolve_target_chats_env_override_replaces_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "100,200,300")
    host = _Host(tmp_path / "s.json")
    host._owner_notify_target = 312322764
    targets = host._resolve_catchup_target_chats()
    # env полностью заменяет defaults
    assert targets == [100, 200, 300]
    assert 312322764 not in targets
    assert _DEFAULT_SWARM_GROUP_ID not in targets


def test_resolve_target_chats_dedup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Если owner DM == swarm group (теоретически), дедупликация должна работать."""
    monkeypatch.delenv("KRAB_STARTUP_CATCHUP_CHATS", raising=False)
    monkeypatch.setenv("KRAB_SWARM_GROUP_ID", "312322764")
    host = _Host(tmp_path / "s.json")
    host._owner_notify_target = 312322764
    targets = host._resolve_catchup_target_chats()
    assert targets.count(312322764) == 1


# ─── Multi-chat catchup behaviour ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_catchup_multi_chat_iterates_all_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 target chats — get_chat_history вызвана для каждого, replay per chat."""
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "100,200,300")
    host = _Host(tmp_path / "s.json")

    per_chat = {
        100: [_mk_msg(11), _mk_msg(12)],
        200: [_mk_msg(21)],
        300: [_mk_msg(31), _mk_msg(32), _mk_msg(33)],
    }
    client = MagicMock()
    client.get_chat_history = _make_per_chat_history(per_chat)
    host.client = client

    result = await host._catchup_all_owner_chats()

    assert result == {100: 2, 200: 1, 300: 3}
    # Все 6 messages должны попасть в _processed
    assert {m.id for m in host._processed} == {11, 12, 21, 31, 32, 33}


@pytest.mark.asyncio
async def test_catchup_multi_chat_resilient_to_one_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chat 200 raises — chats 100 и 300 всё равно catchup."""
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "100,200,300")
    host = _Host(tmp_path / "s.json")

    async def _broken_for_200(chat_id, *_a, **_kw):
        if int(chat_id) == 200:
            raise RuntimeError("Telegram down for chat 200")
        # for 100 and 300 yield messages
        for m in [_mk_msg(int(chat_id) * 10 + 1)]:
            yield m

    client = MagicMock()
    client.get_chat_history = _broken_for_200
    host.client = client

    result = await host._catchup_all_owner_chats()

    # Chat 200 = 0 (failure swallowed внутри _catchup_chat_history),
    # Chats 100 и 300 — 1 каждый.
    assert result[100] == 1
    assert result[200] == 0
    assert result[300] == 1
    # processed messages from 100 (1001) и 300 (3001) пройдены
    assert {m.id for m in host._processed} == {1001, 3001}


@pytest.mark.asyncio
async def test_catchup_multi_chat_uses_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KRAB_STARTUP_CATCHUP_CHATS полностью заменяет defaults."""
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "555,666")
    host = _Host(tmp_path / "s.json")
    host._owner_notify_target = 312322764  # это не должно быть в targets

    per_chat = {555: [_mk_msg(1)], 666: [_mk_msg(2)]}
    client = MagicMock()
    client.get_chat_history = _make_per_chat_history(per_chat)
    host.client = client

    result = await host._catchup_all_owner_chats()
    assert set(result.keys()) == {555, 666}
    assert 312322764 not in result
    assert _DEFAULT_SWARM_GROUP_ID not in result


@pytest.mark.asyncio
async def test_catchup_multi_chat_skips_invalid_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Невалидные id из env CSV silently skipped, valid id используются."""
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "100,not-a-number,200")
    host = _Host(tmp_path / "s.json")

    per_chat = {100: [_mk_msg(1)], 200: [_mk_msg(2)]}
    client = MagicMock()
    client.get_chat_history = _make_per_chat_history(per_chat)
    host.client = client

    result = await host._catchup_all_owner_chats()

    assert set(result.keys()) == {100, 200}
    captured = capsys.readouterr()
    # structlog пишет в stdout — invalid id должен присутствовать
    assert "catchup_chat_id_invalid" in captured.out


@pytest.mark.asyncio
async def test_catchup_multi_chat_persists_per_chat_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """state файл должен содержать last_seen для каждого caught-up chat."""
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "100,200,300")
    host = _Host(tmp_path / "s.json")

    per_chat = {
        100: [_mk_msg(15)],
        200: [_mk_msg(25), _mk_msg(26)],
        300: [_mk_msg(99)],
    }
    client = MagicMock()
    client.get_chat_history = _make_per_chat_history(per_chat)
    host.client = client

    await host._catchup_all_owner_chats()

    state = host._load_last_seen()
    assert state[100] == 15
    assert state[200] == 26
    assert state[300] == 99


@pytest.mark.asyncio
async def test_catchup_multi_chat_self_filter_per_chat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wave 46-C self-filter применяется per-chat: own messages не replay."""
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "100,200")
    host = _Host(tmp_path / "s.json")

    per_chat = {
        100: [_mk_msg(1), _mk_msg(2, outgoing=True), _mk_msg(3)],
        200: [_mk_msg(10, from_self=True), _mk_msg(11)],
    }
    client = MagicMock()
    client.get_chat_history = _make_per_chat_history(per_chat)
    host.client = client

    result = await host._catchup_all_owner_chats()

    # 100: 1 и 3 replayed (2 — outgoing skip), 200: 11 replayed (10 — self skip).
    assert result == {100: 2, 200: 1}
    assert {m.id for m in host._processed} == {1, 3, 11}
    # state продвинут до max_id включая self-msgs
    state = host._load_last_seen()
    assert state[100] == 3
    assert state[200] == 11


@pytest.mark.asyncio
async def test_catchup_multi_chat_logs_structured_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Финальный log startup_catchup_complete_multi включает per-chat counts."""
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "100,200")
    host = _Host(tmp_path / "s.json")

    per_chat = {100: [_mk_msg(1), _mk_msg(2)], 200: [_mk_msg(3)]}
    client = MagicMock()
    client.get_chat_history = _make_per_chat_history(per_chat)
    host.client = client

    await host._catchup_all_owner_chats()
    captured = capsys.readouterr()

    # structlog пишет в stdout
    assert "startup_catchup_complete_multi" in captured.out
    # per-chat counts должны быть в выводе
    assert "total_caught_up=3" in captured.out
    assert "target_count=2" in captured.out


@pytest.mark.asyncio
async def test_catchup_multi_chat_empty_targets_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Если нет ни owner DM, ни swarm group, и env пустой — пустой dict."""
    monkeypatch.delenv("KRAB_STARTUP_CATCHUP_CHATS", raising=False)
    monkeypatch.setenv("KRAB_SWARM_GROUP_ID", "")
    monkeypatch.delenv("OWNER_NOTIFY_CHAT_ID", raising=False)
    from src.config import config

    original = config.OWNER_NOTIFY_CHAT_ID
    config.OWNER_NOTIFY_CHAT_ID = ""
    try:
        host = _Host(tmp_path / "s.json")
        host._owner_notify_target = "me"  # non-int → fallback fails
        host.client = MagicMock()
        result = await host._catchup_all_owner_chats()
        assert result == {}
    finally:
        config.OWNER_NOTIFY_CHAT_ID = original


@pytest.mark.asyncio
async def test_catchup_chat_history_no_client_returns_zero(tmp_path: Path) -> None:
    """_catchup_chat_history без client → zeros, без raise."""
    host = _Host(tmp_path / "s.json")
    host.client = None
    stats = await host._catchup_chat_history(100)
    assert stats["caught_up"] == 0
    assert stats["history_size"] == 0
