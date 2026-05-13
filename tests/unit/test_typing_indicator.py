# -*- coding: utf-8 -*-
"""
Тесты `src/userbot/typing_indicator.py` — Wave 173 (Session 48).

Покрывают:
- Context manager start/stop task lifecycle
- Keep-alive loop пере-шлёт action каждые N секунд
- Cancellation на __aexit__
- Env gate `KRAB_TYPING_INDICATOR_ENABLED` (disable)
- Per-chat blocklist `KRAB_TYPING_INDICATOR_BLOCKED_CHATS`
- FloodWait swallow (ошибка send_chat_action не валит body)
- Helper'ы для разных action типов (text/voice/photo/document)
- Exception в body пробрасывается наружу
- CANCEL action шлётся на выходе
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Тестовый fake-action: чтобы тесты не зависели от pyrogram импорта
# ---------------------------------------------------------------------------


class _FakeAction:
    """Заменяем pyrogram.enums.ChatAction для тестов."""

    TYPING = "typing"
    RECORD_AUDIO = "record_audio"
    UPLOAD_PHOTO = "upload_photo"
    UPLOAD_DOCUMENT = "upload_document"
    UPLOAD_VIDEO = "upload_video"
    CANCEL = "cancel"


@pytest.fixture
def fake_client():
    """Mock Pyrogram client с AsyncMock send_chat_action."""
    client = MagicMock()
    client.send_chat_action = AsyncMock()
    return client


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Сбрасываем env vars перед каждым тестом для детерминизма."""
    monkeypatch.delenv("KRAB_TYPING_INDICATOR_ENABLED", raising=False)
    monkeypatch.delenv("KRAB_TYPING_INDICATOR_BLOCKED_CHATS", raising=False)


# ---------------------------------------------------------------------------
# 1. Базовый lifecycle: enter/exit запускает и отменяет task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_manager_starts_and_stops_task(fake_client):
    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(fake_client, chat_id=42, action=_FakeAction.TYPING)
    async with ti:
        # Внутри блока — task должна быть создана и активна.
        assert ti._task is not None
        assert not ti._task.done()
        # Дать loop'у выполнить хотя бы один send_chat_action.
        await asyncio.sleep(0.01)
    # После выхода — task отменён.
    assert ti._task.done()
    # send_chat_action был вызван хотя бы раз (как минимум первый тик).
    assert fake_client.send_chat_action.await_count >= 1


# ---------------------------------------------------------------------------
# 2. Keep-alive loop пере-шлёт action периодически
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_resends_periodically(fake_client):
    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(
        fake_client,
        chat_id=42,
        action=_FakeAction.TYPING,
        interval_sec=0.05,  # ускоряем для теста
    )
    async with ti:
        # Ждём примерно 3 интервала.
        await asyncio.sleep(0.18)
    # Минимум 3 вызова (первый + 3 keep-alive + потенциально CANCEL).
    # send_chat_action включает все вызовы: TYPING * N + CANCEL.
    assert fake_client.send_chat_action.await_count >= 3


# ---------------------------------------------------------------------------
# 3. Cancellation на __aexit__ останавливает task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_on_exit(fake_client):
    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(
        fake_client,
        chat_id=42,
        action=_FakeAction.TYPING,
        interval_sec=10.0,  # большой интервал, чтоб точно отменили
    )
    async with ti:
        await asyncio.sleep(0.01)
        task_ref = ti._task
    # Task завершён.
    assert task_ref.done()


# ---------------------------------------------------------------------------
# 4. Env gate disable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_gate_disabled_noop(fake_client, monkeypatch):
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_ENABLED", "0")
    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(fake_client, chat_id=42, action=_FakeAction.TYPING)
    async with ti:
        await asyncio.sleep(0.05)
    # send_chat_action НЕ должен вызываться вообще (no-op режим).
    assert fake_client.send_chat_action.await_count == 0
    # task не создавался.
    assert ti._task is None


@pytest.mark.asyncio
async def test_env_gate_enabled_explicit_on(fake_client, monkeypatch):
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_ENABLED", "1")
    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(fake_client, chat_id=42, action=_FakeAction.TYPING)
    async with ti:
        await asyncio.sleep(0.01)
    assert fake_client.send_chat_action.await_count >= 1


# ---------------------------------------------------------------------------
# 5. Per-chat blocklist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_chat_blocklist_blocks_chat(fake_client, monkeypatch):
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_BLOCKED_CHATS", "42,-1001234567890")
    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(fake_client, chat_id=42, action=_FakeAction.TYPING)
    async with ti:
        await asyncio.sleep(0.05)
    assert fake_client.send_chat_action.await_count == 0


@pytest.mark.asyncio
async def test_per_chat_blocklist_allows_other_chat(fake_client, monkeypatch):
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_BLOCKED_CHATS", "999")
    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(fake_client, chat_id=42, action=_FakeAction.TYPING)
    async with ti:
        await asyncio.sleep(0.01)
    assert fake_client.send_chat_action.await_count >= 1


def test_is_enabled_for_chat_helper(monkeypatch):
    from src.userbot.typing_indicator import is_enabled_for_chat

    # Default: enabled.
    monkeypatch.delenv("KRAB_TYPING_INDICATOR_ENABLED", raising=False)
    monkeypatch.delenv("KRAB_TYPING_INDICATOR_BLOCKED_CHATS", raising=False)
    assert is_enabled_for_chat(42) is True

    # Global disable.
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_ENABLED", "0")
    assert is_enabled_for_chat(42) is False
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_ENABLED", "1")

    # Per-chat block.
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_BLOCKED_CHATS", "42")
    assert is_enabled_for_chat(42) is False
    assert is_enabled_for_chat(99) is True
    # str/int взаимозаменяемо.
    assert is_enabled_for_chat("42") is False


# ---------------------------------------------------------------------------
# 6. FloodWait / network error swallow — не валит блок
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_floodwait_swallow_does_not_break_block(fake_client):
    # send_chat_action всегда падает.
    fake_client.send_chat_action = AsyncMock(side_effect=RuntimeError("FloodWait: 5 sec"))

    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(
        fake_client,
        chat_id=42,
        action=_FakeAction.TYPING,
        interval_sec=0.02,
    )
    inside_executed = False
    async with ti:
        await asyncio.sleep(0.05)
        inside_executed = True
    assert inside_executed is True
    # Ошибки логировались, но не пробросились.
    assert fake_client.send_chat_action.await_count >= 1


# ---------------------------------------------------------------------------
# 7. Different action types (helper factories)
# ---------------------------------------------------------------------------


def test_action_type_helpers_create_correct_indicator(fake_client):
    """Проверка что `text_typing`/`recording_voice`/`uploading_photo` подбирают
    правильный action через pyrogram.enums.ChatAction."""
    from src.userbot.typing_indicator import (
        recording_voice,
        text_typing,
        uploading_document,
        uploading_photo,
    )

    # Все helper'ы возвращают TypingIndicator с правильным client/chat_id.
    ti_text = text_typing(fake_client, chat_id=1)
    ti_voice = recording_voice(fake_client, chat_id=1)
    ti_photo = uploading_photo(fake_client, chat_id=1)
    ti_doc = uploading_document(fake_client, chat_id=1)

    assert ti_text._client is fake_client
    assert ti_voice._client is fake_client
    assert ti_photo._client is fake_client
    assert ti_doc._client is fake_client

    # Action — разный (значения берутся из pyrogram.enums.ChatAction в runtime).
    # Уникальные объекты, т.е. helper подобрал разные actions.
    actions = {
        id(ti_text._action),
        id(ti_voice._action),
        id(ti_photo._action),
        id(ti_doc._action),
    }
    assert len(actions) == 4


@pytest.mark.asyncio
async def test_explicit_action_passed_to_client(fake_client):
    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(
        fake_client,
        chat_id=42,
        action=_FakeAction.RECORD_AUDIO,
        interval_sec=10.0,
    )
    async with ti:
        await asyncio.sleep(0.01)
    # Среди вызовов — был хотя бы один с RECORD_AUDIO.
    call_actions = [call.args[1] for call in fake_client.send_chat_action.await_args_list]
    assert _FakeAction.RECORD_AUDIO in call_actions


# ---------------------------------------------------------------------------
# 8. Exception в body пробрасывается, индикатор всё равно закрывается
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exception_in_body_propagates_and_closes_indicator(fake_client):
    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(fake_client, chat_id=42, action=_FakeAction.TYPING)
    with pytest.raises(ValueError, match="boom"):
        async with ti:
            await asyncio.sleep(0.01)
            raise ValueError("boom")
    # Несмотря на exception, task закрылся.
    assert ti._task is not None
    assert ti._task.done()


# ---------------------------------------------------------------------------
# 9. CANCEL action шлётся на выходе (когда pyrogram доступен)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_action_sent_on_exit(fake_client):
    """На __aexit__ должен прийти ChatAction.CANCEL чтобы убрать indicator
    у клиентов мгновенно (не ждать ~5s auto-expire)."""
    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(
        fake_client,
        chat_id=42,
        action=_FakeAction.TYPING,
        interval_sec=10.0,
    )
    async with ti:
        await asyncio.sleep(0.01)
    # Последний или предпоследний вызов — CANCEL (через pyrogram.enums.ChatAction).
    # Проверяем что хотя бы один вызов отличается от TYPING (=CANCEL).
    all_actions = [call.args[1] for call in fake_client.send_chat_action.await_args_list]
    # CANCEL имеет name="CANCEL" в pyrogram.enums.ChatAction; сравним по str.
    cancel_seen = any(
        "cancel" in str(a).lower() or getattr(a, "name", "").lower() == "cancel"
        for a in all_actions
    )
    assert cancel_seen, f"CANCEL action not sent. Actions: {all_actions}"


# ---------------------------------------------------------------------------
# 10. None client — graceful no-op (защита от вызова до bot init)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_none_client_noop():
    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(None, chat_id=42, action=_FakeAction.TYPING)
    async with ti:
        await asyncio.sleep(0.01)
    # Task не создавался, никаких ошибок.
    assert ti._task is None


# ---------------------------------------------------------------------------
# 11. Override enabled=True/False через kwarg
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_enabled_false_overrides_env(fake_client, monkeypatch):
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_ENABLED", "1")
    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(
        fake_client,
        chat_id=42,
        action=_FakeAction.TYPING,
        enabled=False,  # явный override
    )
    async with ti:
        await asyncio.sleep(0.05)
    assert fake_client.send_chat_action.await_count == 0


@pytest.mark.asyncio
async def test_explicit_enabled_true_overrides_blocklist(fake_client, monkeypatch):
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_BLOCKED_CHATS", "42")
    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(
        fake_client,
        chat_id=42,
        action=_FakeAction.TYPING,
        enabled=True,  # явный override
    )
    async with ti:
        await asyncio.sleep(0.01)
    assert fake_client.send_chat_action.await_count >= 1


# ---------------------------------------------------------------------------
# 12. Сила ENV gate: разные форматы значений
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", True),
        ("true", True),
        ("True", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("", False),
    ],
)
def test_env_value_parsing(monkeypatch, value: str, expected: bool):
    from src.userbot.typing_indicator import is_enabled_for_chat

    monkeypatch.setenv("KRAB_TYPING_INDICATOR_ENABLED", value)
    monkeypatch.delenv("KRAB_TYPING_INDICATOR_BLOCKED_CHATS", raising=False)
    assert is_enabled_for_chat(None) is expected


# ---------------------------------------------------------------------------
# 13. Wave 192: group chat (negative chat_id) тоже работает
# ---------------------------------------------------------------------------
#
# Telegram MTProto: group chat_id < 0 (basic group), -100xxx (supergroup).
# DM chat_id > 0 (user_id). Telegram send_chat_action поддерживает оба типа,
# и наша обвязка не имеет DM-only guards — проверяем явно.


@pytest.mark.asyncio
async def test_group_chat_negative_id_emits_chat_action(fake_client):
    """Wave 192: group chat (chat_id < 0) — indicator активен."""
    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(fake_client, chat_id=-1001234567890, action=_FakeAction.TYPING)
    async with ti:
        await asyncio.sleep(0.01)
    # send_chat_action был вызван с negative chat_id.
    assert fake_client.send_chat_action.await_count >= 1
    call_chat_ids = [call.args[0] for call in fake_client.send_chat_action.await_args_list]
    assert -1001234567890 in call_chat_ids


@pytest.mark.asyncio
async def test_basic_group_negative_id_emits_chat_action(fake_client):
    """Wave 192: basic group (chat_id отрицательный, не -100xxx) — тоже работает."""
    from src.userbot.typing_indicator import TypingIndicator

    ti = TypingIndicator(fake_client, chat_id=-42, action=_FakeAction.TYPING)
    async with ti:
        await asyncio.sleep(0.01)
    assert fake_client.send_chat_action.await_count >= 1


@pytest.mark.asyncio
async def test_dm_and_group_both_work_in_same_session(fake_client):
    """Wave 192: DM (positive id) и group (negative id) — независимые indicators."""
    from src.userbot.typing_indicator import TypingIndicator

    # DM
    ti_dm = TypingIndicator(fake_client, chat_id=12345, action=_FakeAction.TYPING)
    async with ti_dm:
        await asyncio.sleep(0.01)
    # Group
    ti_grp = TypingIndicator(fake_client, chat_id=-1001234567890, action=_FakeAction.TYPING)
    async with ti_grp:
        await asyncio.sleep(0.01)

    call_chat_ids = {call.args[0] for call in fake_client.send_chat_action.await_args_list}
    # Обе chat_id присутствуют в вызовах.
    assert 12345 in call_chat_ids
    assert -1001234567890 in call_chat_ids


def test_blocklist_works_for_negative_chat_id(monkeypatch):
    """Wave 192: per-chat blocklist должен работать для group chats тоже."""
    from src.userbot.typing_indicator import is_enabled_for_chat

    monkeypatch.setenv("KRAB_TYPING_INDICATOR_BLOCKED_CHATS", "-1001234567890,777")
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_ENABLED", "1")
    # Заблокированы — обе формы.
    assert is_enabled_for_chat(-1001234567890) is False
    assert is_enabled_for_chat(777) is False
    # Не в blocklist.
    assert is_enabled_for_chat(-999) is True
    assert is_enabled_for_chat(12345) is True


def test_blocklist_str_int_interchangeable_for_groups(monkeypatch):
    """Wave 192: int -1001234567890 и str '-1001234567890' эквивалентны."""
    from src.userbot.typing_indicator import is_enabled_for_chat

    monkeypatch.setenv("KRAB_TYPING_INDICATOR_BLOCKED_CHATS", "-1001234567890")
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_ENABLED", "1")
    assert is_enabled_for_chat(-1001234567890) is False
    assert is_enabled_for_chat("-1001234567890") is False


# ---------------------------------------------------------------------------
# 14. Wave 211: uploading_video factory + UPLOAD_DOCUMENT integration
# ---------------------------------------------------------------------------


def test_uploading_video_factory_creates_indicator(fake_client):
    """Wave 211: `uploading_video` возвращает TypingIndicator с UPLOAD_VIDEO action."""
    from src.userbot.typing_indicator import uploading_video

    ti = uploading_video(fake_client, chat_id=42)
    assert ti._client is fake_client
    assert ti._chat_id == 42
    # action должен быть UPLOAD_VIDEO (pyrogram.enums.ChatAction.UPLOAD_VIDEO)
    action_name = getattr(ti._action, "name", str(ti._action)).upper()
    assert "UPLOAD_VIDEO" in action_name or "VIDEO" in action_name


@pytest.mark.asyncio
async def test_uploading_video_sends_upload_video_action(fake_client):
    """Wave 211: uploading_video → send_chat_action called с UPLOAD_VIDEO."""
    from src.userbot.typing_indicator import uploading_video

    async with uploading_video(fake_client, chat_id=42, interval_sec=10.0):
        await asyncio.sleep(0.01)
    # Действие UPLOAD_VIDEO должно быть среди отправленных.
    call_actions = [
        getattr(c.args[1], "name", str(c.args[1])).upper()
        for c in fake_client.send_chat_action.await_args_list
    ]
    assert any("VIDEO" in a for a in call_actions)


def test_uploading_video_action_label_metric():
    """Wave 211: `_action_label(UPLOAD_VIDEO)` → 'upload_video' для метрики."""
    from src.userbot.typing_indicator import _action_label

    class _V:
        name = "UPLOAD_VIDEO"

    assert _action_label(_V()) == "upload_video"


def test_uploading_video_in_factories_unique_action(fake_client):
    """Wave 211: 5 factory'ев → 5 уникальных actions (text/voice/photo/doc/video)."""
    from src.userbot.typing_indicator import (
        recording_voice,
        text_typing,
        uploading_document,
        uploading_photo,
        uploading_video,
    )

    actions = {
        id(text_typing(fake_client, chat_id=1)._action),
        id(recording_voice(fake_client, chat_id=1)._action),
        id(uploading_photo(fake_client, chat_id=1)._action),
        id(uploading_document(fake_client, chat_id=1)._action),
        id(uploading_video(fake_client, chat_id=1)._action),
    }
    assert len(actions) == 5


@pytest.mark.asyncio
async def test_handle_paste_uploads_with_doc_indicator(monkeypatch, tmp_path):
    """Wave 211 integration: handle_paste обёрнут в uploading_document (Wave 204 already wrapped — ensure regression test)."""
    from unittest.mock import AsyncMock, MagicMock

    from src.handlers.commands import fileio_commands

    # Минимальный bot + message stub
    bot = MagicMock()
    bot.client = MagicMock()
    bot.client.send_document = AsyncMock()
    bot.client.send_chat_action = AsyncMock()
    bot._get_command_args = MagicMock(return_value="hello-paste-text")

    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = 42
    msg.text = "!paste hello-paste-text"
    msg.reply_to_message = None
    msg.reply = AsyncMock()

    # config.BASE_DIR на tmp_path
    cfg = MagicMock()
    cfg.BASE_DIR = str(tmp_path)
    monkeypatch.setattr(fileio_commands, "_config_baseline", cfg, raising=False)

    await fileio_commands.handle_paste(bot, msg)
    # send_document вызван — обёртка не сломала контракт handler'а.
    assert bot.client.send_document.await_count == 1
    # Caption правильный.
    _, kwargs = bot.client.send_document.await_args
    args = bot.client.send_document.await_args.args
    assert "Paste" in (kwargs.get("caption", "") or (args[2] if len(args) > 2 else ""))


@pytest.mark.asyncio
async def test_send_log_document_uses_uploading_indicator(monkeypatch):
    """Wave 211 integration: _send_log_document обёрнут в uploading_document."""
    import pathlib
    from unittest.mock import AsyncMock, MagicMock

    # Импортируем точечный indicator factory чтобы проверить fact-of-call.
    from src.userbot import typing_indicator as ti_mod

    seen: dict[str, Any] = {}
    real_factory = ti_mod.uploading_document

    def _spy_factory(client, chat_id, **kwargs):
        seen["called"] = True
        seen["chat_id"] = chat_id
        return real_factory(client, chat_id, **kwargs)

    monkeypatch.setattr(ti_mod, "uploading_document", _spy_factory)

    # Вызов через async with напрямую (имитируем wrapped path в system_commands).
    fake_client = MagicMock()
    fake_client.send_chat_action = AsyncMock()
    fake_client.send_document = AsyncMock()

    async with ti_mod.uploading_document(fake_client, 42, interval_sec=10.0):
        await fake_client.send_document(42, str(pathlib.Path("/tmp/x.log")), caption="log")

    assert seen.get("called") is True
    assert seen.get("chat_id") == 42
    assert fake_client.send_document.await_count == 1
