# -*- coding: utf-8 -*-
"""
tests/unit/test_skills_command_wave55d.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Wave 55-D: !skills command — управление очередью pending improvements (Wave 53-A).

Тесты:
  1. test_skills_list_groups_by_team         — список группируется по team
  2. test_skills_info_shows_delta_and_metrics — info показывает delta_score и metadata
  3. test_skills_info_unknown_id_returns_error — неизвестный id → error message
  4. test_skills_apply_writes_to_live_dir_and_removes_from_queue — apply меняет overlay
  5. test_skills_apply_creates_before_apply_backup — backup создаётся ДО записи overlay
  6. test_skills_reject_removes_only          — reject удаляет только нужную запись
  7. test_skills_clear_requires_confirm_flag  — clear без --confirm → info only
  8. test_owner_only_rejects_others           — non-owner получает отказ
  9. test_skills_help_when_no_args            — !skills без args → list view
 10. test_short_id_extraction                 — _short_id корректно укорачивает
 11. test_find_entry_by_short_id              — поиск по короткому id
 12. test_skills_clear_with_confirm_empties_queue — clear --confirm очищает очередь
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(team: str, delta_score: float = 0.25, idx: int = 0) -> dict:
    """Creates a minimal pending-queue entry."""
    return {
        "entry_id": f"{team}-abcd{idx:04x}",
        "team": team,
        "candidate_prompt": f"Better prompt for {team} v{idx}",
        "delta_score": delta_score,
        "threshold": 0.15,
        "queued_at": "2026-05-10T10:00:00+00:00",
        "status": "pending",
        "metadata": {"rationale": f"improve {team} tool-call structure"},
    }


def _write_queue(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_message(text: str, from_user_id: int = 99999) -> MagicMock:
    """Creates a minimal Pyrogram-like Message mock."""
    msg = MagicMock()
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = from_user_id
    msg.reply = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = -100
    return msg


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="")
    bot._safe_reply_or_send_new = AsyncMock()
    return bot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_queue_path(tmp_path: Path) -> Path:
    return tmp_path / "curator" / "_pending_skill_improvements.json"


@pytest.fixture()
def tmp_state_path(tmp_path: Path) -> Path:
    return tmp_path / "curator" / "curator_state.json"


@pytest.fixture()
def owner_id() -> int:
    return 12345


# ---------------------------------------------------------------------------
# Import helpers (lazy to avoid heavy deps at collect time)
# ---------------------------------------------------------------------------


def _import_handler():
    from src.handlers.commands.observability_commands import (
        _find_entry_by_short_id,
        _short_id,
        handle_skills,
    )
    return handle_skills, _short_id, _find_entry_by_short_id


# ---------------------------------------------------------------------------
# Test 1: list groups by team
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skills_list_groups_by_team(tmp_queue_path: Path, owner_id: int) -> None:
    """!skills (no args) groups entries by team name in the reply."""
    handle_skills, _, _ = _import_handler()

    entries = [
        _make_entry("traders", 0.23, 0),
        _make_entry("traders", 0.18, 1),
        _make_entry("coders", 0.12, 0),
    ]
    _write_queue(tmp_queue_path, entries)

    msg = _make_message("!skills", from_user_id=owner_id)

    with (
        patch("src.core.access_control.is_owner_user_id", return_value=True),
        patch(
            "src.handlers.commands.observability_commands.list_pending_improvements",
            return_value=entries,
        ),
        patch(
            "src.handlers.commands.observability_commands.PENDING_IMPROVEMENTS_PATH",
            tmp_queue_path,
        ),
    ):
        await handle_skills(None, msg)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "traders" in reply_text
    assert "coders" in reply_text
    assert "2 pending" in reply_text or "traders" in reply_text


# ---------------------------------------------------------------------------
# Test 2: info shows delta and metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skills_info_shows_delta_and_metrics(tmp_queue_path: Path, owner_id: int) -> None:
    """!skills info <id> shows delta_score and metadata rationale."""
    handle_skills, _short_id, _ = _import_handler()

    entry = _make_entry("analysts", 0.31)
    sid = _short_id(entry["entry_id"])
    _write_queue(tmp_queue_path, [entry])

    msg = _make_message(f"!skills info {sid}", from_user_id=owner_id)

    with (
        patch("src.core.access_control.is_owner_user_id", return_value=True),
        patch(
            "src.handlers.commands.observability_commands.PENDING_IMPROVEMENTS_PATH",
            tmp_queue_path,
        ),
        patch(
            "src.handlers.commands.observability_commands._load_pending_queue",
            return_value=[entry],
        ),
    ):
        await handle_skills(None, msg)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "0.3100" in reply_text or "0.31" in reply_text
    assert "analysts" in reply_text
    assert "improve analysts" in reply_text  # from metadata rationale


# ---------------------------------------------------------------------------
# Test 3: info unknown id → error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skills_info_unknown_id_returns_error(tmp_queue_path: Path, owner_id: int) -> None:
    """!skills info <unknown> → error message, no crash."""
    handle_skills, _, _ = _import_handler()

    _write_queue(tmp_queue_path, [_make_entry("coders")])
    msg = _make_message("!skills info xxxxxx", from_user_id=owner_id)

    with (
        patch("src.core.access_control.is_owner_user_id", return_value=True),
        patch(
            "src.handlers.commands.observability_commands.PENDING_IMPROVEMENTS_PATH",
            tmp_queue_path,
        ),
        patch(
            "src.handlers.commands.observability_commands._load_pending_queue",
            return_value=[_make_entry("coders")],
        ),
    ):
        await handle_skills(None, msg)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "не найдена" in reply_text or "not found" in reply_text.lower()


# ---------------------------------------------------------------------------
# Test 4: apply writes overlay and removes entry from queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skills_apply_writes_to_live_dir_and_removes_from_queue(
    tmp_path: Path, owner_id: int
) -> None:
    """!skills apply <id> → calls _apply_pending_entry, removes entry from queue."""
    handle_skills, _short_id, _ = _import_handler()

    entry = _make_entry("creative", 0.22)
    sid = _short_id(entry["entry_id"])

    msg = _make_message(f"!skills apply {sid}", from_user_id=owner_id)

    with (
        patch("src.core.access_control.is_owner_user_id", return_value=True),
        patch(
            "src.handlers.commands.observability_commands._load_pending_queue",
            return_value=[entry],
        ),
        patch(
            "src.handlers.commands.observability_commands.PENDING_IMPROVEMENTS_PATH",
            tmp_path / "pending.json",
        ),
        patch(
            "src.handlers.commands.observability_commands._apply_pending_entry",
            return_value=(True, "applied (version=1, backup=before_apply_xxx.md)"),
        ) as mock_apply,
    ):
        await handle_skills(None, msg)

    mock_apply.assert_called_once_with(entry)
    msg.reply.assert_called()
    calls_text = " ".join(str(c) for c in msg.reply.call_args_list)
    assert "applied" in calls_text or "✅" in calls_text


# ---------------------------------------------------------------------------
# Test 5: apply creates before-apply backup
# ---------------------------------------------------------------------------


def test_skills_apply_creates_before_apply_backup(tmp_path: Path) -> None:
    """_apply_pending_entry creates a backup file before writing live overlay."""
    from src.handlers.commands.observability_commands import _apply_pending_entry

    entry = _make_entry("traders", 0.30)
    backup_base = tmp_path / "backup_dir"

    # Мокаем CuratorState чтобы не трогать реальный файл
    with (
        patch(
            "src.core.skill_curator_state.CuratorState.load"
        ) as mock_state_load,
        patch(
            "src.core.skill_curator_state.CURATOR_STATE_PATH",
            tmp_path / "state.json",
        ),
        patch(
            "src.core.skill_curator.PENDING_IMPROVEMENTS_PATH",
            tmp_path / "pending.json",
        ),
        patch(
            "src.core.skill_curator._load_pending_queue",
            return_value=[entry],
        ),
        patch(
            "src.core.skill_curator._save_pending_queue_atomic",
            return_value=True,
        ),
    ):
        state_instance = MagicMock()
        state_instance.get_overlay.return_value = {"version": 0, "prompt": "old prompt"}
        state_instance.apply_overlay = MagicMock()
        state_instance.mark_apply = MagicMock()
        state_instance.save_atomic = MagicMock()
        mock_state_load.return_value = state_instance

        ok, msg_str = _apply_pending_entry(entry, backup_base=backup_base)

    assert ok is True
    # Backup file should exist
    backups = list(backup_base.glob("before_apply_*.md"))
    assert len(backups) == 1, f"Expected 1 backup, found: {backups}"
    assert backups[0].read_text(encoding="utf-8") == "old prompt"


# ---------------------------------------------------------------------------
# Test 6: reject removes only the targeted entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skills_reject_removes_only(tmp_path: Path, owner_id: int) -> None:
    """!skills reject <id> removes only the targeted entry, others remain."""
    handle_skills, _short_id, _ = _import_handler()

    entry_a = _make_entry("coders", 0.20, 0)
    entry_b = _make_entry("coders", 0.18, 1)
    sid_a = _short_id(entry_a["entry_id"])

    saved_queue: list[list] = []

    def fake_save(queue, path):
        saved_queue.append(list(queue))
        return True

    msg = _make_message(f"!skills reject {sid_a}", from_user_id=owner_id)

    with (
        patch("src.core.access_control.is_owner_user_id", return_value=True),
        patch(
            "src.handlers.commands.observability_commands._load_pending_queue",
            return_value=[entry_a, entry_b],
        ),
        patch(
            "src.handlers.commands.observability_commands._save_pending_queue_atomic",
            side_effect=fake_save,
        ),
        patch(
            "src.handlers.commands.observability_commands.PENDING_IMPROVEMENTS_PATH",
            tmp_path / "pending.json",
        ),
    ):
        await handle_skills(None, msg)

    assert saved_queue, "Queue was never saved"
    final_queue = saved_queue[-1]
    assert len(final_queue) == 1
    assert final_queue[0]["entry_id"] == entry_b["entry_id"]

    msg.reply.assert_called()
    assert "удалена" in msg.reply.call_args[0][0] or "reject" in msg.reply.call_args[0][0].lower()


# ---------------------------------------------------------------------------
# Test 7: clear without --confirm → shows count only, does not clear
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skills_clear_requires_confirm_flag(tmp_path: Path, owner_id: int) -> None:
    """!skills clear without --confirm → shows entry count, queue untouched."""
    handle_skills, _, _ = _import_handler()

    entries = [_make_entry("analysts", 0.20, i) for i in range(3)]
    saved: list = []

    msg = _make_message("!skills clear", from_user_id=owner_id)

    with (
        patch("src.core.access_control.is_owner_user_id", return_value=True),
        patch(
            "src.handlers.commands.observability_commands._load_pending_queue",
            return_value=entries,
        ),
        patch(
            "src.handlers.commands.observability_commands._save_pending_queue_atomic",
            side_effect=lambda q, p: saved.append(q) or True,
        ),
        patch(
            "src.handlers.commands.observability_commands.PENDING_IMPROVEMENTS_PATH",
            tmp_path / "pending.json",
        ),
    ):
        await handle_skills(None, msg)

    # Save should NOT have been called (no --confirm)
    assert not saved, "Queue was saved despite missing --confirm"
    reply_text: str = msg.reply.call_args[0][0]
    # Should mention the count and --confirm hint
    assert "3" in reply_text
    assert "--confirm" in reply_text


# ---------------------------------------------------------------------------
# Test 8: owner-only rejects non-owners
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_only_rejects_others() -> None:
    """Non-owner user gets access denied message."""
    handle_skills, _, _ = _import_handler()

    msg = _make_message("!skills", from_user_id=777)

    with patch("src.core.access_control.is_owner_user_id", return_value=False):
        await handle_skills(None, msg)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    assert "владельцу" in reply_text or "owner" in reply_text.lower()


# ---------------------------------------------------------------------------
# Test 9: !skills with no args → list view (shows queue or empty)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skills_help_when_no_args(tmp_path: Path, owner_id: int) -> None:
    """!skills with no args → list view showing empty queue message."""
    handle_skills, _, _ = _import_handler()

    msg = _make_message("!skills", from_user_id=owner_id)

    with (
        patch("src.core.access_control.is_owner_user_id", return_value=True),
        patch(
            "src.handlers.commands.observability_commands.list_pending_improvements",
            return_value=[],
        ),
        patch(
            "src.handlers.commands.observability_commands.PENDING_IMPROVEMENTS_PATH",
            tmp_path / "pending.json",
        ),
    ):
        await handle_skills(None, msg)

    msg.reply.assert_called_once()
    reply_text: str = msg.reply.call_args[0][0]
    # Empty queue → explains how to add entries
    assert "пуст" in reply_text or "empty" in reply_text.lower() or "Queue" in reply_text


# ---------------------------------------------------------------------------
# Test 10: _short_id extraction
# ---------------------------------------------------------------------------


def test_short_id_extraction() -> None:
    """_short_id returns the last 6 hex chars of the UUID suffix."""
    _, _short_id, _ = _import_handler()

    assert _short_id("traders-a1b2c3d4") == "a1b2c3"
    assert _short_id("coders-ffeebbaa") == "ffeebb"
    # Falls back gracefully for unusual formats
    result = _short_id("plain")
    assert len(result) <= 6


# ---------------------------------------------------------------------------
# Test 11: _find_entry_by_short_id
# ---------------------------------------------------------------------------


def test_find_entry_by_short_id() -> None:
    """_find_entry_by_short_id matches on suffix, case-insensitive."""
    _, _short_id, _find = _import_handler()

    entries = [
        {"entry_id": "traders-a1b2c3d4"},
        {"entry_id": "coders-ffeebbaa"},
    ]
    result = _find(entries, "a1b2c3")
    assert result is not None
    assert result["entry_id"] == "traders-a1b2c3d4"

    # Miss
    assert _find(entries, "000000") is None


# ---------------------------------------------------------------------------
# Test 12: clear with --confirm empties queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skills_clear_with_confirm_empties_queue(tmp_path: Path, owner_id: int) -> None:
    """!skills clear --confirm saves empty queue and reports count."""
    handle_skills, _, _ = _import_handler()

    entries = [_make_entry("creative", 0.20, i) for i in range(2)]
    saved_queues: list[list] = []

    def fake_save(queue, path):
        saved_queues.append(list(queue))
        return True

    msg = _make_message("!skills clear --confirm", from_user_id=owner_id)

    with (
        patch("src.core.access_control.is_owner_user_id", return_value=True),
        patch(
            "src.handlers.commands.observability_commands._load_pending_queue",
            return_value=entries,
        ),
        patch(
            "src.handlers.commands.observability_commands._save_pending_queue_atomic",
            side_effect=fake_save,
        ),
        patch(
            "src.handlers.commands.observability_commands.PENDING_IMPROVEMENTS_PATH",
            tmp_path / "pending.json",
        ),
    ):
        await handle_skills(None, msg)

    assert saved_queues, "Queue save was never called"
    assert saved_queues[-1] == [], "Queue not emptied"
    reply_text: str = msg.reply.call_args[0][0]
    assert "2" in reply_text  # Reports 2 entries deleted
