# -*- coding: utf-8 -*-
"""
Тесты общего workspace/memory-моста для userbot.

Покрываем:
1) prompt bundle собирается из канонического OpenClaw workspace;
2) `!remember` пишет в shared memory даже если локальный vector-store недоступен;
3) `!recall` читает shared memory workspace.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.handlers.command_handlers as command_handlers_module
from src.core.openclaw_workspace import (
    append_workspace_memory_entry,
    load_workspace_prompt_bundle,
    recall_workspace_memory,
)
from src.handlers.command_handlers import handle_recall, handle_remember


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


@pytest.mark.asyncio
async def test_handle_remember_succeeds_when_shared_workspace_saved(monkeypatch):
    message = SimpleNamespace(
        from_user=SimpleNamespace(username="po"),
        reply=AsyncMock(),
    )
    bot = SimpleNamespace(_get_command_args=lambda _: "запомни это")

    monkeypatch.setattr(command_handlers_module, "append_workspace_memory_entry", lambda *args, **kwargs: True)
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
