# -*- coding: utf-8 -*-
"""
Тесты для дополнительных response-чатов свёрма (additional_response_chats).

Проверяют, что:
1. ``swarm_channels.json`` парсится с полем ``additional_response_chats``.
2. ``_resolve_destination`` возвращает origin chat (без topic) при
   ``set_round_origin`` для зарегистрированного чата.
3. Listener реагирует на сообщения в additional chat без reply/mention.

Сценарий: How2AI чат (-1001587432709) добавлен в allowlist, swarm-команда
``!swarm coders <topic>``, отправленная оттуда, должна получать ответ обратно
в How2AI, а не в forum-группу 🐝 Krab Swarm.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.swarm_channels import SwarmChannels, _parse_additional_chat_entry

# -- Помощники ---------------------------------------------------------------


def _fresh_channels(tmp_path: Path) -> SwarmChannels:
    """Создаёт изолированный SwarmChannels с отдельным state-файлом."""
    ch = SwarmChannels()
    # Чистим артефакты runtime singleton'а
    ch._forum_chat_id = None
    ch._team_topics = {}
    ch._team_chats = {}
    ch._additional_chats = {}
    ch._round_origin_chats = {}
    ch._STATE_PATH = str(tmp_path / "swarm_channels.json")
    return ch


# -- 1. Парсинг конфига -----------------------------------------------------


class TestAdditionalChatsConfigParsing:
    def test_parse_valid_entry(self):
        chat_id, meta = _parse_additional_chat_entry(
            {
                "chat_id": -1001587432709,
                "title": "ЧАТ How2AI",
                "respond_in_same_chat": True,
            }
        )
        assert chat_id == -1001587432709
        assert meta["title"] == "ЧАТ How2AI"
        assert meta["respond_in_same_chat"] is True

    def test_parse_missing_respond_flag_defaults_true(self):
        _, meta = _parse_additional_chat_entry({"chat_id": -100123, "title": "x"})
        assert meta["respond_in_same_chat"] is True

    def test_parse_invalid_chat_id_returns_none(self):
        assert _parse_additional_chat_entry({"chat_id": "not_a_number"}) is None

    def test_parse_non_dict_returns_none(self):
        assert _parse_additional_chat_entry("garbage") is None

    def test_load_from_disk(self, tmp_path: Path):
        path = tmp_path / "swarm_channels.json"
        path.write_text(
            json.dumps(
                {
                    "forum_chat_id": None,
                    "team_topics": {},
                    "team_chats": {},
                    "additional_response_chats": [
                        {
                            "chat_id": -1001587432709,
                            "title": "ЧАТ How2AI",
                            "respond_in_same_chat": True,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        ch = SwarmChannels()
        ch._forum_chat_id = None
        ch._team_topics = {}
        ch._team_chats = {}
        ch._additional_chats = {}
        ch._STATE_PATH = str(path)
        ch._load()
        assert ch.is_additional_response_chat(-1001587432709) is True
        assert ch.get_additional_chats()[-1001587432709]["title"] == "ЧАТ How2AI"

    def test_save_then_reload_roundtrip(self, tmp_path: Path):
        ch = _fresh_channels(tmp_path)
        ch.register_additional_chat(
            -1001587432709, title="ЧАТ How2AI", respond_in_same_chat=True
        )
        # Перечитываем
        ch2 = SwarmChannels()
        ch2._forum_chat_id = None
        ch2._team_topics = {}
        ch2._team_chats = {}
        ch2._additional_chats = {}
        ch2._STATE_PATH = ch._STATE_PATH
        ch2._load()
        assert ch2.is_additional_response_chat(-1001587432709) is True


# -- 2. Resolve destination + round origin ----------------------------------


class TestRoundOriginResolution:
    def test_origin_overrides_forum(self, tmp_path: Path):
        ch = _fresh_channels(tmp_path)
        # Настроен forum
        ch._forum_chat_id = -100999999
        ch._team_topics = {"coders": 42}
        # И зарегистрирован How2AI как additional
        ch.register_additional_chat(-1001587432709, title="How2AI")
        ch.set_round_origin("coders", -1001587432709)

        chat_id, topic_id = ch._resolve_destination("coders")
        assert chat_id == -1001587432709
        assert topic_id is None  # без topic — обычная группа

    def test_origin_unset_falls_back_to_forum(self, tmp_path: Path):
        ch = _fresh_channels(tmp_path)
        ch._forum_chat_id = -100999999
        ch._team_topics = {"coders": 42}
        # origin не выставлен → forum
        chat_id, topic_id = ch._resolve_destination("coders")
        assert chat_id == -100999999
        assert topic_id == 42

    def test_origin_ignored_when_chat_not_in_allowlist(self, tmp_path: Path):
        ch = _fresh_channels(tmp_path)
        ch._forum_chat_id = -100999999
        ch._team_topics = {"coders": 42}
        # set_round_origin записывает, но _resolve_destination не активирует
        # его, если chat не в additional_chats.
        ch.set_round_origin("coders", -100777777)
        chat_id, topic_id = ch._resolve_destination("coders")
        assert chat_id == -100999999  # forum выиграл
        assert topic_id == 42

    def test_mark_round_done_clears_origin(self, tmp_path: Path):
        ch = _fresh_channels(tmp_path)
        ch.register_additional_chat(-1001587432709, title="How2AI")
        ch.mark_round_active("coders")
        ch.set_round_origin("coders", -1001587432709)
        assert ch.get_round_origin("coders") == -1001587432709
        ch.mark_round_done("coders")
        assert ch.get_round_origin("coders") is None

    def test_respond_flag_false_disables_override(self, tmp_path: Path):
        ch = _fresh_channels(tmp_path)
        ch._forum_chat_id = -100999999
        ch._team_topics = {"coders": 42}
        ch.register_additional_chat(
            -1001587432709, title="How2AI", respond_in_same_chat=False
        )
        ch.set_round_origin("coders", -1001587432709)
        chat_id, topic_id = ch._resolve_destination("coders")
        # respond_in_same_chat=False → fallback на forum
        assert chat_id == -100999999
        assert topic_id == 42

    def test_is_swarm_chat_recognises_additional(self, tmp_path: Path):
        ch = _fresh_channels(tmp_path)
        ch.register_additional_chat(-1001587432709, title="How2AI")
        assert ch.is_swarm_chat(-1001587432709) == "_additional"
        assert ch.is_swarm_chat(-9999) is None


# -- 3. Broadcast в origin chat ---------------------------------------------


class TestBroadcastToOriginChat:
    @pytest.mark.asyncio
    async def test_broadcast_role_step_goes_to_origin(self, tmp_path: Path):
        ch = _fresh_channels(tmp_path)
        ch._forum_chat_id = -100999999
        ch._team_topics = {"coders": 42}
        ch.register_additional_chat(-1001587432709, title="How2AI")

        client = MagicMock()
        client.send_message = AsyncMock()
        ch.bind(client, owner_id=12345)
        # Перезаписываем потерянное при bind состояние
        ch._forum_chat_id = -100999999
        ch._team_topics = {"coders": 42}

        # Запускаем раунд из How2AI
        ch.mark_round_active("coders")
        ch.set_round_origin("coders", -1001587432709)

        await ch.broadcast_role_step(
            team="coders",
            role_name="aналитик",
            role_emoji="📊",
            role_title="Analyst",
            text="Анализ готов",
        )

        # Сообщение ушло в How2AI БЕЗ message_thread_id
        client.send_message.assert_awaited()
        call_args = client.send_message.await_args
        assert call_args.args[0] == -1001587432709
        # message_thread_id отсутствует (обычная группа)
        assert "message_thread_id" not in call_args.kwargs


# -- 4. Listener ловит сообщения из additional chats ------------------------


class TestListenerAcceptsAdditionalChats:
    @pytest.mark.asyncio
    async def test_listener_responds_in_additional_chat_without_mention(
        self, tmp_path: Path, monkeypatch
    ):
        """В additional chat listener реагирует без mention/reply."""
        from src.core import swarm_team_listener

        # Подменяем глобальный singleton swarm_channels внутри модуля listener
        ch = _fresh_channels(tmp_path)
        ch.register_additional_chat(-1001587432709, title="How2AI")
        monkeypatch.setattr(swarm_team_listener, "swarm_channels", ch)
        # Owner-проверка не нужна для group-веток, но reset на всякий
        monkeypatch.setattr(swarm_team_listener, "_listeners_enabled", True)

        # Имитируем Pyrogram client/message
        me = SimpleNamespace(id=999, username="coders_bot")
        client = MagicMock()
        client.me = me
        client.send_chat_action = AsyncMock()

        message = MagicMock()
        message.from_user = SimpleNamespace(id=42, username="someone")
        message.chat = SimpleNamespace(id=-1001587432709, type="supergroup")
        message.text = "коллеги, что думаете про rust?"
        message.caption = None
        message.reply_to_message = None

        # Ловим вызов _stream_reply
        stream_mock = AsyncMock()
        monkeypatch.setattr(swarm_team_listener, "_stream_reply", stream_mock)

        # Подменяем cooldown — сразу разрешён
        monkeypatch.setattr(
            swarm_team_listener, "_check_cooldown", lambda team, cid: True
        )

        openclaw = MagicMock()
        await swarm_team_listener._handle_team_message(
            "coders", client, message, openclaw
        )

        # Без mention, без reply — но additional chat → реагируем
        stream_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_listener_ignores_regular_group_without_mention(
        self, tmp_path: Path, monkeypatch
    ):
        """В обычной группе без mention/reply listener молчит."""
        from src.core import swarm_team_listener

        ch = _fresh_channels(tmp_path)
        # НЕ регистрируем additional
        monkeypatch.setattr(swarm_team_listener, "swarm_channels", ch)
        monkeypatch.setattr(swarm_team_listener, "_listeners_enabled", True)

        me = SimpleNamespace(id=999, username="coders_bot")
        client = MagicMock()
        client.me = me
        client.send_chat_action = AsyncMock()

        message = MagicMock()
        message.from_user = SimpleNamespace(id=42, username="someone")
        message.chat = SimpleNamespace(id=-100888, type="supergroup")
        message.text = "просто разговор"
        message.caption = None
        message.reply_to_message = None

        stream_mock = AsyncMock()
        monkeypatch.setattr(swarm_team_listener, "_stream_reply", stream_mock)
        monkeypatch.setattr(
            swarm_team_listener, "_check_cooldown", lambda team, cid: True
        )

        openclaw = MagicMock()
        await swarm_team_listener._handle_team_message(
            "coders", client, message, openclaw
        )

        stream_mock.assert_not_awaited()
