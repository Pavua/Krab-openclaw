# -*- coding: utf-8 -*-
"""Тесты для TelegramChatResolver."""

from src.core.telegram_chat_resolver import TelegramChatResolver
from src.utils.black_box import BlackBox


def test_normalize_target():
    resolver = TelegramChatResolver(black_box=type("BB", (), {"db_path": ""})())
    assert resolver.normalize_target("@test_chat") == "@test_chat"
    assert resolver.normalize_target("test_chat") == "@test_chat"
    assert resolver.normalize_target("-100123") == "-100123"
    assert resolver.normalize_target("https://t.me/mychannel") == "@mychannel"


def test_recent_chats_from_black_box(tmp_path):
    db_path = tmp_path / "bb.db"
    bb = BlackBox(db_path=str(db_path))
    bb.log_message(
        chat_id=-1001,
        chat_title="Group A",
        sender_id=1,
        sender_name="User",
        username="user1",
        direction="INCOMING",
        text="hello",
    )
    bb.log_message(
        chat_id=777,
        chat_title="Private B",
        sender_id=2,
        sender_name="User2",
        username="user2",
        direction="INCOMING",
        text="hi",
    )
    resolver = TelegramChatResolver(black_box=bb, max_picker_items=5)
    items = resolver.get_recent_chats()
    chat_ids = {item["chat_id"] for item in items}
    assert -1001 in chat_ids
    assert 777 in chat_ids
