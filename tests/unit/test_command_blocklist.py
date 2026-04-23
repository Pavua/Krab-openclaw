# -*- coding: utf-8 -*-
"""
Тесты per-chat command blocklist.

Покрываем:
1. Пустой blocklist → is_blocked False
2. add_block → is_blocked True для нужного чата
3. add_block идемпотентен
4. remove_block убирает запись
5. Global wildcard (*) блокирует в любом чате
6. Global wildcard имеет приоритет над chat-specific
7. _normalize убирает !/./ и делает lowercase
8. list_blocks (один чат и all)
9. Команды !block/!unblock/!blocklist только owner (через заглушки хендлеров)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.command_blocklist import CommandBlocklist, _normalize

# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_blocklist(tmp_path: Path):
    """Изолированный экземпляр CommandBlocklist с пустым временным файлом."""
    bl_file = tmp_path / "command_blocklist.json"
    # Патчим только _DEFAULT_CONFIG чтобы стартовать с пустым state
    with patch("src.core.command_blocklist._DEFAULT_CONFIG", {}):
        bl = CommandBlocklist(state_dir=tmp_path, blocklist_file=bl_file)
    return bl, bl_file


# ---------------------------------------------------------------------------
# _normalize
# ---------------------------------------------------------------------------


def test_normalize_strips_prefix():
    assert _normalize("!status") == "status"
    assert _normalize("/start") == "start"
    assert _normalize(".status") == "status"
    assert _normalize("STATUS") == "status"
    assert _normalize("  !  STATUS  ") == "status"


def test_normalize_plain_cmd():
    assert _normalize("status") == "status"


# ---------------------------------------------------------------------------
# is_blocked
# ---------------------------------------------------------------------------


def test_empty_blocklist_returns_false(tmp_blocklist):
    bl, _ = tmp_blocklist
    assert bl.is_blocked(-1001587432709, "status") is False
    assert bl.is_blocked(12345, "anything") is False


def test_add_and_is_blocked(tmp_blocklist):
    bl, _ = tmp_blocklist
    chat_id = -1001587432709
    bl.add_block(chat_id, "status")
    assert bl.is_blocked(chat_id, "status") is True
    # другой чат не задет
    assert bl.is_blocked(999, "status") is False


def test_is_blocked_normalizes_command(tmp_blocklist):
    bl, _ = tmp_blocklist
    bl.add_block(100, "!Status")
    assert bl.is_blocked(100, "!status") is True
    assert bl.is_blocked(100, "STATUS") is True
    assert bl.is_blocked(100, "/Status") is True


# ---------------------------------------------------------------------------
# add_block / remove_block
# ---------------------------------------------------------------------------


def test_add_block_idempotent(tmp_blocklist):
    bl, _ = tmp_blocklist
    first = bl.add_block(42, "status")
    second = bl.add_block(42, "status")
    assert first is True
    assert second is False  # уже был
    assert len(bl.list_blocks(42)) == 1


def test_remove_block(tmp_blocklist):
    bl, _ = tmp_blocklist
    bl.add_block(42, "status")
    removed = bl.remove_block(42, "status")
    assert removed is True
    assert bl.is_blocked(42, "status") is False


def test_remove_nonexistent_returns_false(tmp_blocklist):
    bl, _ = tmp_blocklist
    assert bl.remove_block(999, "status") is False


def test_empty_list_after_remove_cleans_key(tmp_blocklist):
    bl, _ = tmp_blocklist
    bl.add_block(42, "status")
    bl.remove_block(42, "status")
    all_blocks = bl.list_blocks()
    assert "42" not in all_blocks


# ---------------------------------------------------------------------------
# Global wildcard
# ---------------------------------------------------------------------------


def test_global_wildcard_blocks_all_chats(tmp_blocklist):
    bl, _ = tmp_blocklist
    bl.add_block("*", "start")
    assert bl.is_blocked(111, "start") is True
    assert bl.is_blocked(-9999, "start") is True
    assert bl.is_blocked(0, "start") is True


def test_global_wildcard_priority_over_chat_specific(tmp_blocklist):
    bl, _ = tmp_blocklist
    # Глобальный блок — всегда приоритетен
    bl.add_block("*", "cmd")
    bl.add_block(100, "cmd")  # дублируем на конкретный чат
    assert bl.is_blocked(100, "cmd") is True
    # Убираем из конкретного — глобальный остаётся
    bl.remove_block(100, "cmd")
    assert bl.is_blocked(100, "cmd") is True  # global ещё работает
    bl.remove_block("*", "cmd")
    assert bl.is_blocked(100, "cmd") is False


# ---------------------------------------------------------------------------
# list_blocks
# ---------------------------------------------------------------------------


def test_list_blocks_single_chat(tmp_blocklist):
    bl, _ = tmp_blocklist
    bl.add_block(42, "status")
    bl.add_block(42, "start")
    result = bl.list_blocks(42)
    assert set(result) == {"status", "start"}


def test_list_blocks_all(tmp_blocklist):
    bl, _ = tmp_blocklist
    bl.add_block(1, "a")
    bl.add_block(2, "b")
    all_blocks = bl.list_blocks()
    assert "1" in all_blocks
    assert "2" in all_blocks


def test_list_blocks_empty_chat(tmp_blocklist):
    bl, _ = tmp_blocklist
    assert bl.list_blocks(999) == []


# ---------------------------------------------------------------------------
# Персистентность
# ---------------------------------------------------------------------------


def test_persistence_to_file(tmp_blocklist):
    bl, bl_file = tmp_blocklist
    bl.add_block(-1001587432709, "status")
    # Файл должен быть записан
    assert bl_file.exists()
    data = json.loads(bl_file.read_text())
    assert "-1001587432709" in data
    assert "status" in data["-1001587432709"]


def test_reload_from_file(tmp_path: Path):
    """Новый экземпляр читает данные из существующего файла."""
    bl_file = tmp_path / "command_blocklist.json"
    bl_file.write_text(json.dumps({"-42": ["status", "help"]}), encoding="utf-8")

    bl2 = CommandBlocklist(state_dir=tmp_path, blocklist_file=bl_file)

    assert bl2.is_blocked(-42, "status") is True
    assert bl2.is_blocked(-42, "help") is True
    assert bl2.is_blocked(-42, "other") is False


# ---------------------------------------------------------------------------
# Thread safety — smoke test
# ---------------------------------------------------------------------------


def test_thread_safety(tmp_blocklist):
    """Конкурентные add/remove не должны гонять ошибок."""
    bl, _ = tmp_blocklist
    errors: list[Exception] = []

    def worker(chat_id: int, cmd: str):
        try:
            bl.add_block(chat_id, cmd)
            bl.is_blocked(chat_id, cmd)
            bl.remove_block(chat_id, cmd)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i % 5, f"cmd{i}")) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


# ---------------------------------------------------------------------------
# Owner-only guard (handler level) — unit через заглушку
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_cmdblock_owner_only():
    """handle_cmdblock отклоняет не-owner через UserInputError."""
    from src.core.exceptions import UserInputError
    from src.handlers.command_handlers import handle_cmdblock

    bot = MagicMock()
    from src.core.access_control import AccessLevel, AccessProfile

    non_owner_profile = AccessProfile(level=AccessLevel.GUEST)
    bot._get_access_profile.return_value = non_owner_profile

    message = MagicMock()
    message.from_user = MagicMock()
    message.text = "!block status"
    message.chat.id = -1001587432709

    with pytest.raises(UserInputError):
        await handle_cmdblock(bot, message)


@pytest.mark.asyncio
async def test_handle_cmdunblock_owner_only():
    from src.core.exceptions import UserInputError
    from src.handlers.command_handlers import handle_cmdunblock

    bot = MagicMock()
    from src.core.access_control import AccessLevel, AccessProfile

    non_owner_profile = AccessProfile(level=AccessLevel.GUEST)
    bot._get_access_profile.return_value = non_owner_profile

    message = MagicMock()
    message.from_user = MagicMock()
    message.text = "!unblock status"
    message.chat.id = -1001587432709

    with pytest.raises(UserInputError):
        await handle_cmdunblock(bot, message)


@pytest.mark.asyncio
async def test_handle_blocklist_owner_only():
    from src.core.exceptions import UserInputError
    from src.handlers.command_handlers import handle_blocklist

    bot = MagicMock()
    from src.core.access_control import AccessLevel, AccessProfile

    non_owner_profile = AccessProfile(level=AccessLevel.GUEST)
    bot._get_access_profile.return_value = non_owner_profile

    message = MagicMock()
    message.from_user = MagicMock()
    message.text = "!blocklist"
    message.chat.id = -1001587432709

    with pytest.raises(UserInputError):
        await handle_blocklist(bot, message)
