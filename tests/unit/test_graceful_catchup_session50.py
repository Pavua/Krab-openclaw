# -*- coding: utf-8 -*-
"""Session 50 P0: Pyrogram chat-drop fix via graceful-restart catchup union.

Background:
- Wave 57-A добавил `_schedule_catchup_after_graceful_restart` для recovery
  после graceful Pyrogram restart, который reuse'ил
  `_run_startup_catchup_safe` → ходил только через whitelist
  (owner DM + Krab Swarm group либо CSV env).
- 15.05.2026: YMB group `-1001804661353` silence 13ч после
  `graceful_restart_triggering_catchup` — chat не был в whitelist,
  GetDifference не восстановил старые updates (PTS gap > server retention).
- Session 50 P0: новый `_run_graceful_restart_catchup_safe` делает union
  whitelist + top-N recent active dialogs (env-tuned), чтобы покрывать
  активные чаты потерянные между reconnect'ами.

Coverage:
- env helpers (limit/hours parsing + clamping + invalid fallbacks)
- `_resolve_recent_active_chats` happy path + empty + exception isolation
- `_catchup_all_owner_chats(targets_override=...)` backward compat
- `_run_graceful_restart_catchup_safe` union + dedup + delegation
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.userbot.message_catchup import (
    MessageCatchupMixin,
    _resolve_recent_active_hours,
    _resolve_recent_active_limit,
)

# ── Env helper tests ────────────────────────────────────────────────────────


def test_resolve_recent_active_limit_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KRAB_GRACEFUL_CATCHUP_RECENT_LIMIT", raising=False)
    assert _resolve_recent_active_limit() == 30


def test_resolve_recent_active_limit_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KRAB_GRACEFUL_CATCHUP_RECENT_LIMIT", "50")
    assert _resolve_recent_active_limit() == 50


def test_resolve_recent_active_limit_zero_disables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0 = explicit disable (только whitelist, без iter_dialogs)."""
    monkeypatch.setenv("KRAB_GRACEFUL_CATCHUP_RECENT_LIMIT", "0")
    assert _resolve_recent_active_limit() == 0


def test_resolve_recent_active_limit_clamped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KRAB_GRACEFUL_CATCHUP_RECENT_LIMIT", "9999")
    assert _resolve_recent_active_limit() == 100
    monkeypatch.setenv("KRAB_GRACEFUL_CATCHUP_RECENT_LIMIT", "-5")
    assert _resolve_recent_active_limit() == 0


def test_resolve_recent_active_limit_invalid_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KRAB_GRACEFUL_CATCHUP_RECENT_LIMIT", "not-a-number")
    assert _resolve_recent_active_limit(default=42) == 42


def test_resolve_recent_active_hours_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KRAB_GRACEFUL_CATCHUP_RECENT_HOURS", raising=False)
    assert _resolve_recent_active_hours() == 6.0


def test_resolve_recent_active_hours_clamped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KRAB_GRACEFUL_CATCHUP_RECENT_HOURS", "0.1")
    assert _resolve_recent_active_hours() == 0.5
    monkeypatch.setenv("KRAB_GRACEFUL_CATCHUP_RECENT_HOURS", "9999")
    assert _resolve_recent_active_hours() == 72.0


# ── Host stub ──────────────────────────────────────────────────────────────


class _Host(MessageCatchupMixin):
    """Минимальный host для тестирования mixin."""

    def __init__(self, state_path: Path):
        self._state_path = state_path
        self.client: Any = None
        self.me = None
        self._owner_notify_target: int | str = 312322764
        self._processed: list[Any] = []

    def _last_seen_state_path(self) -> Path:
        return self._state_path

    async def _process_message(self, message):  # noqa: D401
        self._processed.append(message)


def _mk_dialog(chat_id: int, *, age_seconds: float) -> MagicMock:
    """Mock pyrogram Dialog с заданным age у top_message."""
    import time as _time

    dialog = MagicMock()
    dialog.chat = MagicMock(id=chat_id)
    top_msg = MagicMock()
    # date может быть datetime либо float — поддерживаем оба
    date_obj = MagicMock()
    date_obj.timestamp = lambda: _time.time() - age_seconds
    top_msg.date = date_obj
    dialog.top_message = top_msg
    return dialog


def _make_iter_dialogs(dialogs: list[Any]):
    """Async generator factory для iter_dialogs mock."""

    async def _gen(*_args, **_kwargs):
        for d in dialogs:
            yield d

    return _gen


# ── _resolve_recent_active_chats ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_recent_active_filters_old_dialogs(tmp_path: Path) -> None:
    """Dialogs старше hours window отбрасываются."""
    host = _Host(tmp_path / "state.json")
    client = MagicMock()
    client.iter_dialogs = _make_iter_dialogs(
        [
            _mk_dialog(-1001111111, age_seconds=60),  # 1 min ago — keep
            _mk_dialog(-1002222222, age_seconds=2 * 3600),  # 2 h — keep (under 6h)
            _mk_dialog(-1003333333, age_seconds=10 * 3600),  # 10 h — drop
            _mk_dialog(-1004444444, age_seconds=100 * 3600),  # 100 h — drop
        ]
    )
    host.client = client

    result = await host._resolve_recent_active_chats(limit=10, hours=6.0)
    assert result == [-1001111111, -1002222222]


@pytest.mark.asyncio
async def test_recent_active_empty_when_limit_zero(tmp_path: Path) -> None:
    """limit=0 → short-circuit, iter_dialogs не вызывается."""
    host = _Host(tmp_path / "state.json")
    client = MagicMock()
    client.iter_dialogs = MagicMock(side_effect=AssertionError("should not be called"))
    host.client = client

    result = await host._resolve_recent_active_chats(limit=0, hours=6.0)
    assert result == []


@pytest.mark.asyncio
async def test_recent_active_no_client_returns_empty(tmp_path: Path) -> None:
    host = _Host(tmp_path / "state.json")
    host.client = None
    result = await host._resolve_recent_active_chats(limit=10, hours=6.0)
    assert result == []


@pytest.mark.asyncio
async def test_recent_active_iter_dialogs_failure_isolated(tmp_path: Path) -> None:
    """iter_dialogs raises → возвращаем [] вместо raise."""
    host = _Host(tmp_path / "state.json")
    client = MagicMock()

    async def _bad_gen(*_args, **_kwargs):
        raise RuntimeError("flood wait simulated")
        yield  # noqa

    client.iter_dialogs = _bad_gen
    host.client = client

    result = await host._resolve_recent_active_chats(limit=10, hours=6.0)
    assert result == []


@pytest.mark.asyncio
async def test_recent_active_dedup(tmp_path: Path) -> None:
    """Дубликат chat_id из iter_dialogs не должен попасть дважды."""
    host = _Host(tmp_path / "state.json")
    client = MagicMock()
    client.iter_dialogs = _make_iter_dialogs(
        [
            _mk_dialog(-1005555555, age_seconds=60),
            _mk_dialog(-1005555555, age_seconds=120),  # дубликат
            _mk_dialog(-1006666666, age_seconds=300),
        ]
    )
    host.client = client

    result = await host._resolve_recent_active_chats(limit=10, hours=6.0)
    assert result == [-1005555555, -1006666666]


# ── _catchup_all_owner_chats targets_override ──────────────────────────────


@pytest.mark.asyncio
async def test_catchup_all_owner_chats_uses_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """targets_override полностью заменяет resolver (backward compat: None → resolver)."""
    monkeypatch.delenv("KRAB_STARTUP_CATCHUP_CHATS", raising=False)

    host = _Host(tmp_path / "state.json")
    host._catchup_chat_history = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "caught_up": 1,
            "skipped_self": 0,
            "history_size": 1,
            "last_seen_before": 0,
            "last_seen_after": 1,
        }
    )

    custom_targets = [-9001, -9002, -9003]
    result = await host._catchup_all_owner_chats(targets_override=custom_targets)

    assert set(result.keys()) == set(custom_targets)
    # _catchup_chat_history вызван 3 раза (по одному на target)
    assert host._catchup_chat_history.await_count == 3


@pytest.mark.asyncio
async def test_catchup_all_owner_chats_override_none_uses_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backward compat: targets_override=None → как раньше через resolver."""
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "111,222")

    host = _Host(tmp_path / "state.json")
    host._catchup_chat_history = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "caught_up": 0,
            "skipped_self": 0,
            "history_size": 0,
            "last_seen_before": 0,
            "last_seen_after": 0,
        }
    )

    result = await host._catchup_all_owner_chats()
    assert set(result.keys()) == {111, 222}


# ── _run_graceful_restart_catchup_safe ─────────────────────────────────────


@pytest.mark.asyncio
async def test_graceful_catchup_unions_whitelist_and_recent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Union whitelist + recent active, дедуп с сохранением порядка."""
    # Whitelist через env: 2 chats. Recent active: 3 chats (один дубликат).
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "111,222")
    monkeypatch.setenv("KRAB_GRACEFUL_CATCHUP_RECENT_LIMIT", "10")
    monkeypatch.setenv("KRAB_GRACEFUL_CATCHUP_RECENT_HOURS", "6")

    host = _Host(tmp_path / "state.json")
    client = MagicMock()
    client.iter_dialogs = _make_iter_dialogs(
        [
            _mk_dialog(222, age_seconds=60),  # дубликат с whitelist
            _mk_dialog(333, age_seconds=60),
            _mk_dialog(444, age_seconds=60),
        ]
    )
    host.client = client

    captured_targets: list[list[int]] = []

    async def _spy(*, targets_override=None):  # type: ignore[no-untyped-def]
        captured_targets.append(list(targets_override or []))
        return {cid: 0 for cid in (targets_override or [])}

    host._catchup_all_owner_chats = _spy  # type: ignore[method-assign]

    await host._run_graceful_restart_catchup_safe()

    assert len(captured_targets) == 1
    # Порядок: whitelist (111, 222), потом recent unique (333, 444)
    assert captured_targets[0] == [111, 222, 333, 444]


@pytest.mark.asyncio
async def test_graceful_catchup_recent_failure_falls_back_to_whitelist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Если recent active fails — продолжаем с whitelist-only (defensive)."""
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "111")

    host = _Host(tmp_path / "state.json")

    async def _broken_recent(**_kwargs):
        raise RuntimeError("iter_dialogs exploded")

    host._resolve_recent_active_chats = _broken_recent  # type: ignore[method-assign]

    captured_targets: list[list[int]] = []

    async def _spy(*, targets_override=None):  # type: ignore[no-untyped-def]
        captured_targets.append(list(targets_override or []))
        return {cid: 0 for cid in (targets_override or [])}

    host._catchup_all_owner_chats = _spy  # type: ignore[method-assign]

    # НЕ должно raise — defensive
    await host._run_graceful_restart_catchup_safe()
    assert captured_targets == [[111]]


@pytest.mark.asyncio
async def test_graceful_catchup_catchup_failure_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_catchup_all_owner_chats raise → log warning, не raise дальше."""
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "111")
    monkeypatch.setenv("KRAB_GRACEFUL_CATCHUP_RECENT_LIMIT", "0")

    host = _Host(tmp_path / "state.json")

    async def _broken(**_kwargs):
        raise RuntimeError("catchup boom")

    host._catchup_all_owner_chats = _broken  # type: ignore[method-assign]

    # Не должно raise (defensive wrapper)
    await host._run_graceful_restart_catchup_safe()


@pytest.mark.asyncio
async def test_ymb_scenario_regression(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Symbolic regression: воспроизводим сценарий YMB silence 15.05.

    YMB chat_id `-1001804661353` НЕ в whitelist (env пустой, defaults
    включают только owner_chat + swarm_group), но был active за 6h до
    graceful restart. Должен попасть в graceful catchup targets.
    """
    monkeypatch.delenv("KRAB_STARTUP_CATCHUP_CHATS", raising=False)
    monkeypatch.setenv("OWNER_NOTIFY_CHAT_ID", "312322764")  # owner DM
    monkeypatch.setenv("KRAB_GRACEFUL_CATCHUP_RECENT_LIMIT", "10")

    host = _Host(tmp_path / "state.json")
    client = MagicMock()
    client.iter_dialogs = _make_iter_dialogs(
        [
            _mk_dialog(-1001804661353, age_seconds=2 * 3600),  # YMB, 2h ago
            _mk_dialog(312322764, age_seconds=300),  # owner DM (дубликат)
        ]
    )
    host.client = client

    captured: list[list[int]] = []

    async def _spy(*, targets_override=None):  # type: ignore[no-untyped-def]
        captured.append(list(targets_override or []))
        return {cid: 0 for cid in (targets_override or [])}

    host._catchup_all_owner_chats = _spy  # type: ignore[method-assign]
    await host._run_graceful_restart_catchup_safe()

    assert len(captured) == 1
    # YMB должен присутствовать (был active в 6h окне)
    assert -1001804661353 in captured[0], (
        "P0 regression: YMB chat должен быть в graceful catchup targets"
    )


# ── Smoke-test: state file ничем не задет ──────────────────────────────────


@pytest.mark.asyncio
async def test_graceful_catchup_does_not_break_state_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity: state file не повреждается / не падает на write."""
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "")
    monkeypatch.setenv("KRAB_GRACEFUL_CATCHUP_RECENT_LIMIT", "0")
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"123": 99}), encoding="utf-8")

    host = _Host(state_path)

    async def _noop(**_kwargs):  # type: ignore[no-untyped-def]
        return {}

    host._catchup_all_owner_chats = _noop  # type: ignore[method-assign]

    await host._run_graceful_restart_catchup_safe()
    # State file всё ещё валидный JSON
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"123": 99}
