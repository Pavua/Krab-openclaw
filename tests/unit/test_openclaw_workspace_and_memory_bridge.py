# -*- coding: utf-8 -*-
"""
Тесты общего workspace/memory-моста для userbot.

Покрываем:
1) prompt bundle собирается из канонического OpenClaw workspace;
2) `!remember` пишет в shared memory даже если локальный vector-store недоступен;
3) `!recall` читает shared memory workspace.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.handlers.command_handlers as command_handlers_module
from src.core.openclaw_workspace import (
    append_workspace_memory_entry,
    build_workspace_state_snapshot,
    list_workspace_memory_entries,
    load_workspace_prompt_bundle,
    recall_workspace_memory,
)
from src.handlers.command_handlers import (
    handle_memory,
    handle_recall,
    handle_remember,
    handle_watch,
)


def test_load_workspace_prompt_bundle_reads_canonical_files(tmp_path):
    (tmp_path / "SOUL.md").write_text("SOUL DATA", encoding="utf-8")
    (tmp_path / "USER.md").write_text("USER DATA", encoding="utf-8")
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "2099-01-01.md").write_text("# Memory\n\n- запись", encoding="utf-8")

    bundle = load_workspace_prompt_bundle(
        workspace_dir=tmp_path,
        include_recent_memory_days=0,
    )

    assert "SOUL DATA" in bundle
    assert "USER DATA" in bundle


def test_load_workspace_prompt_bundle_uses_recent_memory_tail(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (tmp_path / "SOUL.md").write_text("SOUL DATA", encoding="utf-8")
    day = datetime.now().date().isoformat()
    (memory_dir / f"{day}.md").write_text(
        f"# Memory {day}\n\n"
        "- 10:00 [proactive-watch] старый шум " + ("x" * 300) + "\n"
        "- 23:59 [owner-context] свежий факт про переводчик\n",
        encoding="utf-8",
    )

    bundle = load_workspace_prompt_bundle(
        workspace_dir=tmp_path,
        max_chars_per_file=140,
        include_recent_memory_days=0,
    )

    assert "свежий факт про переводчик" not in bundle

    bundle_with_recent = load_workspace_prompt_bundle(
        workspace_dir=tmp_path,
        max_chars_per_file=140,
        include_recent_memory_days=1,
    )

    assert "свежий факт про переводчик" in bundle_with_recent
    assert "старый шум" not in bundle_with_recent


def test_append_and_recall_workspace_memory_share_same_files(tmp_path):
    saved = append_workspace_memory_entry(
        "Краб должен помнить про GPT-5.4",
        workspace_dir=tmp_path,
        source="userbot",
        author="po",
    )

    assert saved is True
    recalled = recall_workspace_memory("GPT-5.4", workspace_dir=tmp_path)
    assert "GPT-5.4" in recalled


def test_list_workspace_memory_entries_returns_recent_rows(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "2026-03-12.md").write_text(
        "# Memory 2026-03-12\n\n"
        "- 10:00 [userbot:po] обычная запись\n"
        "- 11:30 [proactive-watch] watch=manual_snapshot; gateway=ON\n",
        encoding="utf-8",
    )
    (memory_dir / "2026-03-11.md").write_text(
        "# Memory 2026-03-11\n\n- 09:15 [userbot] старый факт\n",
        encoding="utf-8",
    )

    rows = list_workspace_memory_entries(workspace_dir=tmp_path, limit=2)
    filtered = list_workspace_memory_entries(
        workspace_dir=tmp_path, limit=5, source_filter="proactive-watch"
    )

    assert rows[0]["source"] == "proactive-watch"
    assert rows[0]["date"] == "2026-03-12"
    assert rows[1]["text"] == "обычная запись"
    assert len(filtered) == 1
    assert filtered[0]["source"] == "proactive-watch"


def test_build_workspace_state_snapshot_exposes_prompt_and_memory_truth(tmp_path):
    (tmp_path / "SOUL.md").write_text("soul", encoding="utf-8")
    (tmp_path / "USER.md").write_text("user", encoding="utf-8")
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "2026-03-13.md").write_text(
        "# Memory 2026-03-13\n\n- 12:00 [reserve-telegram-e2e] reserve_roundtrip=ok\n",
        encoding="utf-8",
    )

    snapshot = build_workspace_state_snapshot(workspace_dir=tmp_path, recent_entries_limit=2)

    assert snapshot["ok"] is True
    assert snapshot["shared_workspace_attached"] is True
    assert snapshot["shared_memory_ready"] is True
    assert snapshot["prompt_files"]["SOUL.md"]["exists"] is True
    assert snapshot["memory_file_count"] == 1
    assert snapshot["recent_memory_entries_count"] == 1
    assert snapshot["last_memory_entry"]["source"] == "reserve-telegram-e2e"


@pytest.mark.asyncio
async def test_handle_remember_succeeds_when_shared_workspace_saved(monkeypatch):
    message = SimpleNamespace(
        from_user=SimpleNamespace(username="po"),
        reply=AsyncMock(),
    )
    bot = SimpleNamespace(_get_command_args=lambda _: "запомни это")

    monkeypatch.setattr(
        command_handlers_module, "append_workspace_memory_entry", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(command_handlers_module.memory_manager, "save_fact", lambda text: False)

    await handle_remember(bot, message)

    message.reply.assert_awaited_once()
    assert "Запомнил" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_handle_recall_reads_shared_workspace_memory(monkeypatch):
    message = SimpleNamespace(reply=AsyncMock())
    bot = SimpleNamespace(_get_command_args=lambda _: "GPT-5.4")

    monkeypatch.setattr(
        command_handlers_module,
        "recall_workspace_memory",
        lambda query: "- [2026-03-10.md] GPT-5.4 заблокирован до появления в runtime registry",
    )
    monkeypatch.setattr(command_handlers_module.memory_manager, "recall", lambda query: "")

    await handle_recall(bot, message)

    message.reply.assert_awaited_once()
    reply_text = message.reply.await_args.args[0]
    assert "OpenClaw workspace" in reply_text
    assert "GPT-5.4" in reply_text


@pytest.mark.asyncio
async def test_handle_watch_status_renders_state(monkeypatch):
    message = SimpleNamespace(text="!watch status", reply=AsyncMock())
    bot = SimpleNamespace()
    monkeypatch.setattr(
        command_handlers_module.proactive_watch,
        "get_status",
        lambda: {
            "enabled": True,
            "interval_sec": 900,
            "alert_cooldown_sec": 1800,
            "last_reason": "route_model_changed",
            "last_digest_ts": "2026-03-12T05:00:00+00:00",
            "last_alert_ts": "",
            "last_snapshot": {
                "route_model": "openai-codex/gpt-5.4",
                "primary_model": "openai-codex/gpt-5.4",
            },
        },
    )

    await handle_watch(bot, message)

    text = message.reply.await_args.args[0]
    assert "Proactive Watch" in text
    assert "`900`" in text
    assert "openai-codex/gpt-5.4" in text


@pytest.mark.asyncio
async def test_handle_watch_now_returns_digest(monkeypatch):
    message = SimpleNamespace(text="!watch now", reply=AsyncMock())
    bot = SimpleNamespace()
    monkeypatch.setattr(
        command_handlers_module.proactive_watch,
        "capture",
        AsyncMock(return_value={"digest": "🦀 digest ok", "wrote_memory": True}),
    )

    await handle_watch(bot, message)

    text = message.reply.await_args.args[0]
    assert "digest ok" in text
    assert "workspace memory" in text


@pytest.mark.asyncio
async def test_handle_memory_recent_formats_rows(monkeypatch):
    message = SimpleNamespace(text="!memory recent proactive-watch", reply=AsyncMock())
    bot = SimpleNamespace()
    monkeypatch.setattr(
        command_handlers_module,
        "list_workspace_memory_entries",
        lambda **kwargs: [
            {
                "date": "2026-03-12",
                "time": "11:30",
                "source": "proactive-watch",
                "author": "",
                "text": "watch=manual_snapshot; gateway=ON",
            }
        ],
    )

    await handle_memory(bot, message)

    text = message.reply.await_args.args[0]
    assert "Последние записи общей памяти" in text
    assert "proactive-watch" in text
    assert "watch=manual_snapshot" in text
