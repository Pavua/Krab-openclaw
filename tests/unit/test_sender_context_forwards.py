# -*- coding: utf-8 -*-
"""
Тесты для расширенного src/core/sender_context.py — forward/reply/mention метаданные.

W10.3-ext: проверяем что build_context_block корректно различает
"кто написал" vs "кто переслал" vs "на чьё сообщение ответ".

SECURITY: привилегии определяются ТОЛЬКО по is_owner (= фактический sender_user_id).
          Поля original_author / reply_to — информационные, прав не дают.
"""

from __future__ import annotations

import pytest

from src.core.sender_context import (
    build_context_block,
    build_sender_context_from_message,
    is_owner_user_id,
)

# ---------------------------------------------------------------------------
# Фабричные функции (dict-based, без зависимости от pyrogram)
# ---------------------------------------------------------------------------

OWNER_ID = 100
GUEST_ID = 200
BOB_ID = 300
CAROL_ID = 400
KRAB_ID = 999


def _user(uid: int, username: str = "", first_name: str = "", last_name: str = "") -> dict:
    return {
        "id": uid,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
    }


def _msg(
    from_user=None,
    chat=None,
    forward_from=None,
    forward_sender_name: str | None = None,
    forward_from_chat=None,
    reply_to_message=None,
    entities: list | None = None,
    text: str = "",
    outgoing: bool = False,
    mentioned: bool = False,
) -> dict:
    return {
        "from_user": from_user,
        "chat": chat or {"type": "private", "title": ""},
        "forward_from": forward_from,
        "forward_sender_name": forward_sender_name,
        "forward_from_chat": forward_from_chat,
        "reply_to_message": reply_to_message,
        "entities": entities or [],
        "text": text,
        "outgoing": outgoing,
        "mentioned": mentioned,
    }


def _entity_with_user(uid: int, username: str = "") -> dict:
    return {"user": _user(uid, username=username)}


# ---------------------------------------------------------------------------
# 1. Forwarded from user — original_author populated
# ---------------------------------------------------------------------------


class TestForwardedFromUser:
    def test_is_forwarded_true(self):
        """Сообщение с forward_from → is_forwarded: true."""
        bob = _user(BOB_ID, username="bob_tg", first_name="Bob")
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice", first_name="Alice"),
            forward_from=bob,
        )
        block = build_context_block(msg, is_owner=False)
        assert "is_forwarded: true" in block

    def test_original_author_user_id(self):
        """forward_from.id попадает в original_author_user_id."""
        bob = _user(BOB_ID, username="bob_tg", first_name="Bob")
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice", first_name="Alice"),
            forward_from=bob,
        )
        block = build_context_block(msg, is_owner=False)
        assert f"original_author_user_id: {BOB_ID}" in block

    def test_original_author_username(self):
        """forward_from.username попадает в original_author_username с @."""
        bob = _user(BOB_ID, username="bob_tg", first_name="Bob")
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice", first_name="Alice"),
            forward_from=bob,
        )
        block = build_context_block(msg, is_owner=False)
        assert "original_author_username: @bob_tg" in block

    def test_sender_is_alice_not_bob(self):
        """sender_username всегда Alice — не Bob (кто переслал vs кто автор)."""
        bob = _user(BOB_ID, username="bob_tg", first_name="Bob")
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice", first_name="Alice"),
            forward_from=bob,
        )
        block = build_context_block(msg, is_owner=False)
        assert "sender_username: @alice" in block
        assert f"sender_user_id: {GUEST_ID}" in block

    def test_is_owner_false_even_if_original_author_is_owner(self):
        """
        SECURITY: Даже если original_author == owner, is_owner = false
        если фактический sender != owner.
        """
        owner_as_bob = _user(OWNER_ID, username="owner_tg", first_name="Owner")
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice", first_name="Alice"),
            forward_from=owner_as_bob,
        )
        block = build_context_block(msg, is_owner=False)
        assert "is_owner: false" in block
        assert "is_forwarded: true" in block
        # original_author показан, но is_owner false
        assert f"original_author_user_id: {OWNER_ID}" in block


# ---------------------------------------------------------------------------
# 2. Forwarded from channel — original_author empty, channel name shown
# ---------------------------------------------------------------------------


class TestForwardedFromChannel:
    def test_forwarded_from_channel_username(self):
        """Пересылка из канала с username → original_channel_name: @channel."""
        channel_chat = {"title": "News Channel", "username": "news_channel"}
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice"),
            forward_from_chat=channel_chat,
        )
        block = build_context_block(msg, is_owner=False)
        assert "is_forwarded: true" in block
        assert "original_channel_name: @news_channel" in block

    def test_forwarded_from_channel_title_only(self):
        """Канал без username → используем title."""
        channel_chat = {"title": "Private News", "username": ""}
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice"),
            forward_from_chat=channel_chat,
        )
        block = build_context_block(msg, is_owner=False)
        assert "is_forwarded: true" in block
        assert "original_channel_name: Private News" in block

    def test_no_original_author_user_id_for_channel_forward(self):
        """При пересылке из канала original_author_user_id не показывается."""
        channel_chat = {"title": "News", "username": "news_ch"}
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice"),
            forward_from_chat=channel_chat,
        )
        block = build_context_block(msg, is_owner=False)
        # нет строки с original_author_user_id
        assert "original_author_user_id" not in block

    def test_forward_sender_name_hidden_identity(self):
        """forward_sender_name (скрытый профиль) → original_author_name."""
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice"),
            forward_sender_name="Anonymous Bob",
        )
        block = build_context_block(msg, is_owner=False)
        assert "is_forwarded: true" in block
        assert "original_author_name: Anonymous Bob" in block
        assert "original_author_user_id" not in block


# ---------------------------------------------------------------------------
# 3. Reply to owner message by guest → reply_to populated, is_owner=false
# ---------------------------------------------------------------------------


class TestReplyInfo:
    def test_reply_to_owner_by_guest(self):
        """Гость отвечает на сообщение owner'а → is_reply=true, is_owner=false."""
        owner_msg = {"from_user": _user(OWNER_ID, username="owner_tg", first_name="Owner")}
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice", first_name="Alice"),
            reply_to_message=owner_msg,
        )
        block = build_context_block(msg, is_owner=False)
        assert "is_reply: true" in block
        assert f"reply_to_user_id: {OWNER_ID}" in block
        assert "reply_to_username: @owner_tg" in block
        assert "is_owner: false" in block

    def test_reply_to_user_id_and_username(self):
        """reply_to_user_id и reply_to_username корректно извлекаются."""
        bob_msg = {"from_user": _user(BOB_ID, username="bob_guy", first_name="Bob")}
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice"),
            reply_to_message=bob_msg,
        )
        block = build_context_block(msg, is_owner=False)
        assert f"reply_to_user_id: {BOB_ID}" in block
        assert "reply_to_username: @bob_guy" in block

    def test_no_reply(self):
        """Без reply → is_reply: false."""
        msg = _msg(from_user=_user(GUEST_ID, username="alice"))
        block = build_context_block(msg, is_owner=False)
        assert "is_reply: false" in block
        assert "reply_to_user_id" not in block

    def test_reply_does_not_grant_owner_privileges(self):
        """
        SECURITY: reply_to owner не даёт is_owner=true.
        Привилегии — только по фактическому sender.
        """
        owner_msg = {"from_user": _user(OWNER_ID, username="owner_tg")}
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice"),
            reply_to_message=owner_msg,
        )
        block = build_context_block(msg, is_owner=False)
        assert "is_owner: false" in block
        assert "reply_to_user_id" in block  # показан, но прав не даёт


# ---------------------------------------------------------------------------
# 4. Mentions extracted correctly
# ---------------------------------------------------------------------------


class TestMentionsExtraction:
    def test_mentions_from_text(self):
        """@carol в тексте сообщения → mentioned_users содержит @carol."""
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice"),
            text="Hey @carol how are you?",
        )
        block = build_context_block(msg, is_owner=False)
        assert "mentioned_users:" in block
        assert "@carol" in block

    def test_multiple_mentions(self):
        """Несколько @упоминаний в тексте."""
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice"),
            text="@carol @dave please check this",
        )
        block = build_context_block(msg, is_owner=False)
        assert "@carol" in block
        assert "@dave" in block

    def test_no_mentions(self):
        """Нет упоминаний → mentioned_users не показывается, krab_mentioned: false."""
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice"),
            text="Just a plain message",
        )
        block = build_context_block(msg, is_owner=False)
        assert "mentioned_users" not in block
        assert "krab_mentioned: false" in block

    def test_mentions_from_entities(self):
        """Упоминания через entities (entity.user.username) → в mentioned_users."""
        carol_entity = _entity_with_user(CAROL_ID, username="carol_tg")
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice"),
            entities=[carol_entity],
            text="",
        )
        block = build_context_block(msg, is_owner=False)
        assert "@carol_tg" in block


# ---------------------------------------------------------------------------
# 5. Krab himself mentioned — krab_mentioned flag
# ---------------------------------------------------------------------------


class TestKrabMentioned:
    def test_krab_mentioned_by_username(self):
        """@KrabBot в тексте → krab_mentioned: true."""
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice"),
            text="Hey @KrabBot can you help?",
        )
        block = build_context_block(
            msg, is_owner=False, own_username="KrabBot", own_user_id=KRAB_ID
        )
        assert "krab_mentioned: true" in block

    def test_krab_not_mentioned(self):
        """Краб не упомянут → krab_mentioned: false."""
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice"),
            text="Hello world",
        )
        block = build_context_block(
            msg, is_owner=False, own_username="KrabBot", own_user_id=KRAB_ID
        )
        assert "krab_mentioned: false" in block

    def test_krab_mentioned_via_pyrogram_flag(self):
        """pyrogram mentioned=True → krab_mentioned: true."""
        msg = _msg(
            from_user=_user(GUEST_ID, username="alice"),
            text="text",
            mentioned=True,
        )
        block = build_context_block(
            msg, is_owner=False, own_username="KrabBot", own_user_id=KRAB_ID
        )
        assert "krab_mentioned: true" in block


# ---------------------------------------------------------------------------
# 6. Integration: forwarded owner msg to group → guest sender, owner author
# ---------------------------------------------------------------------------


class TestIntegrationForwardedOwnerMsg:
    def test_forwarded_owner_msg_in_group(self):
        """
        Alice пересылает сообщение owner'а в группу.
        sender=Alice (GUEST_ID), is_owner=false, original_author=owner (OWNER_ID).
        """
        group_chat = {"type": "supergroup", "title": "My Group"}
        owner_user = _user(OWNER_ID, username="owner_tg", first_name="Павел")
        alice_user = _user(GUEST_ID, username="alice_tg", first_name="Alice")

        msg = _msg(
            from_user=alice_user,
            chat=group_chat,
            forward_from=owner_user,
            text="",
        )
        block = build_context_block(msg, is_owner=False)

        # Sender — Alice
        assert f"sender_user_id: {GUEST_ID}" in block
        assert "sender_username: @alice_tg" in block
        assert "is_owner: false" in block

        # Original author — owner
        assert f"original_author_user_id: {OWNER_ID}" in block
        assert "original_author_username: @owner_tg" in block

        # Forwarded flag
        assert "is_forwarded: true" in block

    def test_build_sender_context_from_message_with_forward(self):
        """build_sender_context_from_message корректно обрабатывает forward."""
        owner_user = _user(OWNER_ID, username="owner_tg", first_name="Owner")
        guest_user = _user(GUEST_ID, username="alice_tg", first_name="Alice")

        msg = _msg(
            from_user=guest_user,
            forward_from=owner_user,
        )
        block = build_sender_context_from_message(
            msg,
            self_user_id=OWNER_ID,
            own_username="KrabBot",
        )
        # is_owner определяется по фактическому sender (GUEST_ID != OWNER_ID)
        assert "is_owner: false" in block
        assert "is_forwarded: true" in block
        assert f"original_author_user_id: {OWNER_ID}" in block


# ---------------------------------------------------------------------------
# 7. Fallback — не ломается при плохих данных
# ---------------------------------------------------------------------------


class TestFallback:
    def test_none_message(self):
        """None message → fallback с is_owner."""
        block = build_context_block(None, is_owner=False)
        assert "is_owner: false" in block

    def test_empty_dict_message(self):
        """Пустой dict → безопасный fallback или базовый блок."""
        block = build_context_block({}, is_owner=True)
        assert "is_owner: true" in block

    def test_is_owner_user_id_helper(self):
        """is_owner_user_id корректно сравнивает int и str."""
        assert is_owner_user_id(OWNER_ID, OWNER_ID) is True
        assert is_owner_user_id(str(OWNER_ID), OWNER_ID) is True
        assert is_owner_user_id(GUEST_ID, OWNER_ID) is False
        assert is_owner_user_id(None, OWNER_ID) is False
        assert is_owner_user_id(OWNER_ID, None) is False


# ---------------------------------------------------------------------------
# 8. Policy-блок — override gateway/session cache для persona-правил
# ---------------------------------------------------------------------------


class TestPolicyBlock:
    """Регрессионные тесты: policy-блок инжектируется при каждом запросе."""

    def test_policy_block_present_for_owner(self):
        """build_context_block → [policy] блок всегда присутствует (owner)."""
        msg = _msg(from_user=_user(OWNER_ID, username="owner"))
        block = build_context_block(msg, is_owner=True)
        assert "[policy]" in block
        assert "[/policy]" in block

    def test_policy_block_present_for_guest(self):
        """build_context_block → [policy] блок всегда присутствует (guest)."""
        msg = _msg(from_user=_user(GUEST_ID, username="guest"))
        block = build_context_block(msg, is_owner=False)
        assert "[policy]" in block
        assert "[/policy]" in block

    def test_policy_block_no_my_lord_as_default(self):
        """Policy явно запрещает 'Мой Господин' как default обращение."""
        msg = _msg(from_user=_user(OWNER_ID, username="owner"))
        block = build_context_block(msg, is_owner=True)
        # Правило должно упоминать нейтральный тон
        assert "нейтральный тон" in block or "нейтральный" in block

    def test_policy_block_after_context_block(self):
        """[policy] идёт после [/context] — оба в одном output."""
        msg = _msg(from_user=_user(OWNER_ID, username="owner"))
        block = build_context_block(msg, is_owner=True)
        context_end = block.find("[/context]")
        policy_start = block.find("[policy]")
        assert context_end != -1
        assert policy_start != -1
        assert policy_start > context_end  # policy после context

    def test_policy_block_via_sender_context_from_message(self):
        """build_sender_context_from_message также содержит [policy]."""
        msg = _msg(from_user=_user(OWNER_ID, username="owner"), outgoing=True)
        block = build_sender_context_from_message(msg, self_user_id=OWNER_ID)
        assert "[policy]" in block
