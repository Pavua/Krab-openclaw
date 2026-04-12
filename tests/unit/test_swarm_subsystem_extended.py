# -*- coding: utf-8 -*-
"""
tests/unit/test_swarm_subsystem_extended.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Расширенные тесты для swarm-подсистемы.

Покрывает gap-ы в существующих тестах:
- swarm_bus: TEAM_ALIASES полный перебор, SwarmBusTask поля, singleton
- swarm_channels: _extract_topic_id, _FORUM_TOPICS/константы, register_forum_topic,
  bind с env vars, длинный текст, delegation без crossteam, JSON persistence
- swarm_memory: пустой файл, пробелы в JSON, metadata, topics_sample, compress headers,
  format_history с delegation, team case normalization при save
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.swarm_bus import (
    TEAM_ALIASES,
    TEAM_REGISTRY,
    SwarmBus,
    SwarmBusTask,
    list_teams,
    resolve_team_name,
    swarm_bus,
)
from src.core.swarm_channels import (
    _FORUM_TOPICS,
    _TOPIC_ICON_COLORS,
    SwarmChannels,
    _extract_topic_id,
    swarm_channels,
)
from src.core.swarm_memory import (
    SwarmMemory,
    swarm_memory,
)

# ===========================================================================
# swarm_bus — расширенные тесты
# ===========================================================================


class TestTeamAliasesExtended:
    """Полный перебор всех псевдонимов команд."""

    def test_all_aliases_resolve(self):
        """Каждый псевдоним резолвится в допустимую команду."""
        for alias, expected in TEAM_ALIASES.items():
            resolved = resolve_team_name(alias)
            assert resolved == expected, (
                f"Псевдоним {alias!r} должен резолвиться в {expected!r}, получили {resolved!r}"
            )

    def test_aliases_point_to_existing_teams(self):
        """Все целевые команды в псевдонимах реально существуют в TEAM_REGISTRY."""
        for alias, target in TEAM_ALIASES.items():
            assert target in TEAM_REGISTRY, (
                f"Псевдоним {alias!r} указывает на несуществующую команду {target!r}"
            )

    def test_cyrillic_aliases_for_traders(self):
        """Русскоязычные псевдонимы для traders."""
        for alias in ("трейдеры", "торговля", "торги", "крипта"):
            assert resolve_team_name(alias) == "traders"

    def test_cyrillic_aliases_for_coders(self):
        """Русскоязычные псевдонимы для coders."""
        for alias in ("кодеры", "разработка", "код"):
            assert resolve_team_name(alias) == "coders"

    def test_dev_alias(self):
        """Английский псевдоним 'dev' для coders."""
        assert resolve_team_name("dev") == "coders"

    def test_cyrillic_aliases_for_analysts(self):
        """Русскоязычные псевдонимы для analysts."""
        for alias in ("аналитика", "анализ", "исследование"):
            assert resolve_team_name(alias) == "analysts"

    def test_cyrillic_aliases_for_creative(self):
        """Русскоязычные псевдонимы для creative."""
        for alias in ("креатив", "идеи"):
            assert resolve_team_name(alias) == "creative"

    def test_resolve_unknown_returns_none(self):
        """Неизвестное имя — None."""
        assert resolve_team_name("unknown_team_xyz") is None

    def test_resolve_with_extra_spaces(self):
        """Пробелы вокруг имени обрезаются."""
        assert resolve_team_name("  traders  ") == "traders"
        assert resolve_team_name("  кодеры  ") == "coders"


class TestSwarmBusTaskFields:
    """Инициализация и поля SwarmBusTask."""

    def test_default_task_id_is_short_uuid(self):
        """task_id — 8 символов."""
        task = SwarmBusTask()
        assert len(task.task_id) == 8

    def test_unique_task_ids(self):
        """Каждый SwarmBusTask получает уникальный ID."""
        ids = {SwarmBusTask().task_id for _ in range(20)}
        assert len(ids) == 20

    def test_done_event_initially_not_set(self):
        """done_event не установлен при создании."""
        task = SwarmBusTask()
        assert not task.done_event.is_set()

    def test_result_and_error_initially_none(self):
        task = SwarmBusTask(source_team="traders", target_team="coders", topic="test")
        assert task.result is None
        assert task.error is None

    def test_created_at_is_float(self):
        """created_at — монотонное время (float)."""
        task = SwarmBusTask()
        assert isinstance(task.created_at, float)
        assert task.created_at > 0


class TestSwarmBusSingleton:
    """Проверка что swarm_bus — корректный синглтон."""

    def test_singleton_is_swarmbus_instance(self):
        assert isinstance(swarm_bus, SwarmBus)

    def test_singleton_active_count_starts_zero_or_more(self):
        """active_count доступен и возвращает int."""
        count = swarm_bus.active_count()
        assert isinstance(count, int)
        assert count >= 0


class TestListTeamsExtended:
    """list_teams() — расширенные проверки."""

    def test_contains_all_teams(self):
        output = list_teams()
        for team in ("traders", "coders", "analysts", "creative"):
            assert team in output

    def test_contains_usage_hint(self):
        """В выводе есть пример использования."""
        output = list_teams()
        assert "!swarm" in output

    def test_contains_aliases_hint(self):
        """В выводе упомянуты псевдонимы."""
        output = list_teams()
        assert "Псевдонимы" in output


# ===========================================================================
# swarm_channels — расширенные тесты
# ===========================================================================


class TestExtractTopicId:
    """_extract_topic_id — парсинг MTProto Updates."""

    def _make_updates(self, msg_ids: list[int]) -> MagicMock:
        """Создаёт mock Updates объект с update.message.id."""
        updates = MagicMock()
        mock_updates = []
        for mid in msg_ids:
            upd = MagicMock()
            upd.message = MagicMock()
            upd.message.id = mid
            mock_updates.append(upd)
        updates.updates = mock_updates
        return updates

    def test_extracts_first_message_id(self):
        updates = self._make_updates([42, 99])
        assert _extract_topic_id(updates) == 42

    def test_returns_none_when_no_updates(self):
        updates = MagicMock()
        updates.updates = []
        assert _extract_topic_id(updates) is None

    def test_returns_none_when_message_has_no_id(self):
        updates = MagicMock()
        upd = MagicMock()
        upd.message = MagicMock()
        upd.message.id = None
        updates.updates = [upd]
        assert _extract_topic_id(updates) is None

    def test_returns_none_when_no_updates_attr(self):
        """Если у объекта нет атрибута updates — None."""
        result = _extract_topic_id(object())
        assert result is None


class TestForumTopicsConstants:
    """Проверка констант _FORUM_TOPICS и _TOPIC_ICON_COLORS."""

    def test_forum_topics_has_all_teams(self):
        keys = {t["key"] for t in _FORUM_TOPICS}
        assert {"traders", "coders", "analysts", "creative", "crossteam"} == keys

    def test_forum_topics_have_required_fields(self):
        for topic in _FORUM_TOPICS:
            assert "key" in topic
            assert "title" in topic
            assert "icon_color" in topic

    def test_topic_icon_colors_for_all_teams(self):
        for team in ("traders", "coders", "analysts", "creative", "crossteam"):
            assert team in _TOPIC_ICON_COLORS
            assert isinstance(_TOPIC_ICON_COLORS[team], int)

    def test_icon_colors_are_valid_hex(self):
        """Цвета — валидные Telegram hex-коды (0x000000–0xFFFFFF)."""
        for team, color in _TOPIC_ICON_COLORS.items():
            assert 0 <= color <= 0xFFFFFF, f"Некорректный цвет для {team!r}: {hex(color)}"


class TestSwarmChannelsRegisterForumTopic:
    """register_forum_topic — регистрация топика без disk IO."""

    def test_register_forum_topic(self):
        ch = SwarmChannels()
        ch._forum_chat_id = -100999
        ch._team_topics = {}
        ch._save = MagicMock()  # мокаем диск
        ch.register_forum_topic("traders", 42)
        assert ch._team_topics["traders"] == 42
        ch._save.assert_called_once()

    def test_register_forum_topic_lowercase(self):
        ch = SwarmChannels()
        ch._team_topics = {}
        ch._save = MagicMock()
        ch.register_forum_topic("CODERS", 77)
        assert "coders" in ch._team_topics
        assert ch._team_topics["coders"] == 77

    def test_is_forum_mode_after_registration(self):
        ch = SwarmChannels()
        ch._forum_chat_id = -100999
        ch._team_topics = {}
        ch._save = MagicMock()
        assert not ch.is_forum_mode  # нет топиков — не forum mode
        ch.register_forum_topic("traders", 19)
        assert ch.is_forum_mode


class TestSwarmChannelsBindWithEnv:
    """bind() с env vars SWARM_FORUM_CHAT_ID и SWARM_TEAM_CHATS."""

    def test_bind_sets_forum_chat_id_from_env(self):
        ch = SwarmChannels()
        ch._save = MagicMock()
        mock_client = MagicMock()

        with patch.dict("os.environ", {"SWARM_FORUM_CHAT_ID": "-100777888"}):
            with patch.dict("os.environ", {"SWARM_TEAM_CHATS": ""}):
                ch.bind(mock_client, owner_id=12345)

        assert ch._forum_chat_id == -100777888

    def test_bind_sets_legacy_chats_from_env(self):
        ch = SwarmChannels()
        ch._save = MagicMock()
        mock_client = MagicMock()

        with patch.dict(
            "os.environ",
            {
                "SWARM_FORUM_CHAT_ID": "",
                "SWARM_TEAM_CHATS": "traders:-100111,coders:-100222",
            },
        ):
            ch.bind(mock_client, owner_id=0)

        assert ch._team_chats.get("traders") == -100111
        assert ch._team_chats.get("coders") == -100222

    def test_bind_invalid_forum_chat_id_ignored(self):
        ch = SwarmChannels()
        ch._save = MagicMock()
        with patch.dict("os.environ", {"SWARM_FORUM_CHAT_ID": "notanumber"}):
            with patch.dict("os.environ", {"SWARM_TEAM_CHATS": ""}):
                ch.bind(MagicMock(), owner_id=0)
        assert ch._forum_chat_id is None  # не должен был измениться


class TestSwarmChannelsSendMessageLongText:
    """_send_message обрезает текст > 4000 символов."""

    @pytest.mark.asyncio
    async def test_long_text_is_truncated(self):
        ch = SwarmChannels()
        mock_client = AsyncMock()
        ch._client = mock_client

        long_text = "A" * 5000
        await ch._send_message(chat_id=-100, text=long_text)

        mock_client.send_message.assert_called_once()
        sent_text = mock_client.send_message.call_args[0][1]
        assert len(sent_text) <= 4000
        assert "обрезано" in sent_text

    @pytest.mark.asyncio
    async def test_short_text_not_modified(self):
        ch = SwarmChannels()
        mock_client = AsyncMock()
        ch._client = mock_client

        short_text = "Hello, Krab!"
        await ch._send_message(chat_id=-100, text=short_text)
        sent_text = mock_client.send_message.call_args[0][1]
        assert sent_text == short_text


class TestSwarmChannelsDelegationFallback:
    """broadcast_delegation — fallback когда нет crossteam топика."""

    @pytest.mark.asyncio
    async def test_delegation_falls_back_to_source_team(self):
        ch = SwarmChannels()
        ch._forum_chat_id = -100999
        ch._team_topics = {"traders": 19, "coders": 20}  # нет crossteam
        ch._team_chats = {}
        ch._send_message = AsyncMock()
        ch._client = MagicMock()

        await ch.broadcast_delegation(
            source_team="traders",
            target_team="coders",
            topic="Написать бота",
        )
        ch._send_message.assert_called_once()
        args, kwargs = ch._send_message.call_args
        # Fallback на traders топик
        assert kwargs.get("topic_id") == 19

    @pytest.mark.asyncio
    async def test_delegation_no_destination_skipped(self):
        ch = SwarmChannels()
        ch._forum_chat_id = None
        ch._team_topics = {}
        ch._team_chats = {}
        ch._send_message = AsyncMock()
        ch._client = MagicMock()

        # Не должен падать, _send_message не вызывается
        await ch.broadcast_delegation(
            source_team="traders",
            target_team="coders",
            topic="тест",
        )
        ch._send_message.assert_not_called()


class TestSwarmChannelsJsonPersistence:
    """Сохранение и загрузка через JSON-файл."""

    def test_save_and_load_forum_mode(self, tmp_path: Path):
        state_file = tmp_path / "channels.json"

        with patch("src.core.swarm_channels._STATE_PATH", state_file):
            ch1 = SwarmChannels()
            ch1._forum_chat_id = -100999
            ch1._team_topics = {"traders": 19, "coders": 20}
            ch1._save()

            ch2 = SwarmChannels()
            assert ch2._forum_chat_id == -100999
            assert ch2._team_topics == {"traders": 19, "coders": 20}

    def test_load_corrupted_json_does_not_crash(self, tmp_path: Path):
        state_file = tmp_path / "channels_bad.json"
        state_file.write_text("{{broken json", encoding="utf-8")

        with patch("src.core.swarm_channels._STATE_PATH", state_file):
            ch = SwarmChannels()
            # Должен тихо проигнорировать ошибку
            assert ch._forum_chat_id is None


# ===========================================================================
# swarm_memory — расширенные тесты
# ===========================================================================


class TestSwarmMemoryEmptyFile:
    """Поведение при пустом JSON-файле."""

    def test_empty_json_file(self, tmp_path: Path):
        path = tmp_path / "empty.json"
        path.write_text("   ", encoding="utf-8")  # только пробелы
        mem = SwarmMemory(state_path=path)
        assert mem._data == {}

    def test_empty_file_zero_bytes(self, tmp_path: Path):
        path = tmp_path / "zero.json"
        path.write_text("", encoding="utf-8")
        mem = SwarmMemory(state_path=path)
        assert mem._data == {}


class TestSwarmMemoryMetadata:
    """Поддержка metadata в SwarmRunRecord."""

    def test_save_with_metadata(self, tmp_path: Path):
        mem = SwarmMemory(state_path=tmp_path / "mem.json")
        meta = {"model": "gemini-3-pro", "tokens": 1200}
        rec = mem.save_run(
            team="coders",
            topic="bot refactor",
            result="Код рефакторен",
            metadata=meta,
        )
        assert rec.metadata == meta

    def test_metadata_persisted(self, tmp_path: Path):
        path = tmp_path / "mem.json"
        mem1 = SwarmMemory(state_path=path)
        mem1.save_run(
            team="coders",
            topic="test",
            result="r",
            metadata={"key": "value"},
        )
        mem2 = SwarmMemory(state_path=path)
        recs = mem2.get_recent("coders", 1)
        assert recs[0].metadata == {"key": "value"}


class TestSwarmMemoryTopicClip:
    """Обрезка топика до 500 символов при сохранении."""

    def test_topic_clipped_to_500(self, tmp_path: Path):
        mem = SwarmMemory(state_path=tmp_path / "mem.json")
        long_topic = "T" * 600
        rec = mem.save_run(team="traders", topic=long_topic, result="r")
        assert len(rec.topic) == 500

    def test_short_topic_not_changed(self, tmp_path: Path):
        mem = SwarmMemory(state_path=tmp_path / "mem.json")
        rec = mem.save_run(team="traders", topic="BTC анализ", result="r")
        assert rec.topic == "BTC анализ"


class TestSwarmMemoryCompressHeaders:
    """_compress_result убирает оба типа заголовков swarm."""

    def test_strips_swarm_loop_header(self, tmp_path: Path):
        mem = SwarmMemory(state_path=tmp_path / "mem.json")
        result = "🐝 **Swarm Loop: coders**\n\nActual loop content"
        rec = mem.save_run(team="coders", topic="t", result=result)
        assert not rec.result_summary.startswith("🐝")
        assert "Actual loop content" in rec.result_summary

    def test_plain_result_not_modified(self, tmp_path: Path):
        mem = SwarmMemory(state_path=tmp_path / "mem.json")
        plain = "Просто результат без заголовка"
        rec = mem.save_run(team="traders", topic="t", result=plain)
        assert rec.result_summary == plain


class TestSwarmMemoryStatsTopicsSample:
    """get_team_stats — поле topics_sample."""

    def test_topics_sample_last_3(self, tmp_path: Path):
        mem = SwarmMemory(state_path=tmp_path / "mem.json")
        for i in range(5):
            mem.save_run(team="analysts", topic=f"тема {i}", result="r")
        stats = mem.get_team_stats("analysts")
        assert "topics_sample" in stats
        # Последние 3 темы (2, 3, 4)
        assert len(stats["topics_sample"]) == 3
        assert "тема 4" in stats["topics_sample"][-1]

    def test_topics_sample_shorter_list(self, tmp_path: Path):
        mem = SwarmMemory(state_path=tmp_path / "mem.json")
        mem.save_run(team="analysts", topic="единственная тема", result="r")
        stats = mem.get_team_stats("analysts")
        assert len(stats["topics_sample"]) == 1


class TestSwarmMemoryFormatHistoryDelegations:
    """format_history отображает делегирования."""

    def test_shows_delegations(self, tmp_path: Path):
        mem = SwarmMemory(state_path=tmp_path / "mem.json")
        mem.save_run(
            team="traders",
            topic="BTC стратегия",
            result="Стратегия готова",
            delegations=["coders", "analysts"],
        )
        result = mem.format_history("traders", count=1)
        assert "coders" in result
        assert "analysts" in result


class TestSwarmMemorySingleton:
    """Проверка singleton swarm_memory."""

    def test_singleton_is_swarm_memory_instance(self):
        assert isinstance(swarm_memory, SwarmMemory)

    def test_singleton_all_teams_returns_list(self):
        teams = swarm_memory.all_teams()
        assert isinstance(teams, list)


class TestSwarmChannelsSingleton:
    """Проверка singleton swarm_channels."""

    def test_singleton_is_swarm_channels_instance(self):
        assert isinstance(swarm_channels, SwarmChannels)
