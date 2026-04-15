"""
Unit-тесты для chunking сообщений.

Покрывают:
  - reply_to chain группировка (включая глубокие цепочки);
  - time-gap fallback (gap > 5 мин → новый chunk);
  - max_messages / max_chars лимиты;
  - смена chat_id;
  - lookback eviction (старые chunks закрываются при переполнении окна);
  - порядок: сообщения в chunk'е хронологические;
  - идемпотентность: flush на пустом builder'е.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.core.memory_chunking import (
    Chunk,
    ChunkBuilder,
    Message,
    chunk_messages,
)


# ---------------------------------------------------------------------------
# Хелперы.
# ---------------------------------------------------------------------------

BASE_TIME = datetime(2026, 4, 15, 12, 0, 0)


def _msg(
    mid: str,
    *,
    chat: str = "chat_1",
    text: str = "hi",
    offset_sec: int = 0,
    reply: str | None = None,
) -> Message:
    """Фабрика тестовых сообщений (timestamps относительно BASE_TIME)."""
    return Message(
        message_id=mid,
        chat_id=chat,
        timestamp=BASE_TIME + timedelta(seconds=offset_sec),
        text=text,
        reply_to_message_id=reply,
    )


# ---------------------------------------------------------------------------
# Time-gap fallback.
# ---------------------------------------------------------------------------

class TestTimeGap:
    def test_messages_close_in_time_same_chunk(self) -> None:
        msgs = [
            _msg("1", offset_sec=0),
            _msg("2", offset_sec=60),    # +1 минута
            _msg("3", offset_sec=180),   # +3 минуты
        ]
        chunks = list(chunk_messages(msgs))
        assert len(chunks) == 1
        assert len(chunks[0].messages) == 3

    def test_large_gap_splits_chunks(self) -> None:
        msgs = [
            _msg("1", offset_sec=0),
            _msg("2", offset_sec=60),
            _msg("3", offset_sec=600),  # +10 минут → новый chunk
            _msg("4", offset_sec=660),  # +11 минут → тот же 2-й chunk
        ]
        chunks = list(chunk_messages(msgs))
        assert len(chunks) == 2
        assert {m.message_id for m in chunks[0].messages} == {"1", "2"}
        assert {m.message_id for m in chunks[1].messages} == {"3", "4"}

    def test_custom_time_gap(self) -> None:
        msgs = [
            _msg("1", offset_sec=0),
            _msg("2", offset_sec=120),  # +2 минуты, но gap=1мин → split
        ]
        chunks = list(chunk_messages(msgs, time_gap=timedelta(minutes=1)))
        assert len(chunks) == 2


# ---------------------------------------------------------------------------
# Reply-to chains.
# ---------------------------------------------------------------------------

class TestReplyToChains:
    def test_reply_keeps_in_same_chunk_across_gap(self) -> None:
        # Сообщения с 10-минутным разрывом, но reply_to держит вместе.
        msgs = [
            _msg("1", offset_sec=0, text="исходный вопрос"),
            _msg(
                "2", offset_sec=600, text="ответ через 10 мин", reply="1"
            ),
        ]
        chunks = list(chunk_messages(msgs))
        assert len(chunks) == 1
        assert {m.message_id for m in chunks[0].messages} == {"1", "2"}

    def test_deep_reply_chain(self) -> None:
        msgs = [
            _msg("1", offset_sec=0),
            _msg("2", offset_sec=60, reply="1"),
            _msg("3", offset_sec=120, reply="2"),
            _msg("4", offset_sec=180, reply="3"),
        ]
        chunks = list(chunk_messages(msgs))
        assert len(chunks) == 1
        assert len(chunks[0].messages) == 4

    def test_reply_to_missing_falls_back_to_time_gap(self) -> None:
        """Если reply_to указывает в пустоту — срабатывает time-gap."""
        msgs = [
            # Разрыв > 5 мин и reply-target не в окне.
            _msg("1", offset_sec=0),
            _msg("2", offset_sec=700, reply="missing-id"),
        ]
        chunks = list(chunk_messages(msgs))
        assert len(chunks) == 2


# ---------------------------------------------------------------------------
# Size limits.
# ---------------------------------------------------------------------------

class TestSizeLimits:
    def test_max_messages_limit(self) -> None:
        msgs = [_msg(str(i), offset_sec=i) for i in range(5)]
        chunks = list(chunk_messages(msgs, max_messages=2))
        # 5 сообщений / limit=2 → 3 chunks (2+2+1).
        assert len(chunks) == 3
        assert [len(c.messages) for c in chunks] == [2, 2, 1]

    def test_max_chars_limit(self) -> None:
        long_text = "x" * 100
        msgs = [_msg(str(i), offset_sec=i, text=long_text) for i in range(5)]
        # max_chars=150 → chunks по 1 сообщению (т.к. каждое 100 chars,
        # 2-е не влезает: 100 + 100 = 200 > 150).
        chunks = list(chunk_messages(msgs, max_chars=150))
        assert all(len(c.messages) == 1 for c in chunks)
        assert len(chunks) == 5


# ---------------------------------------------------------------------------
# Multi-chat поведение.
# ---------------------------------------------------------------------------

class TestMultiChat:
    def test_chat_id_change_closes_current_chunk(self) -> None:
        msgs = [
            _msg("1", chat="A", offset_sec=0),
            _msg("2", chat="A", offset_sec=60),
            _msg("3", chat="B", offset_sec=90),  # другой чат — форс-split
            _msg("4", chat="B", offset_sec=120),
        ]
        chunks = list(chunk_messages(msgs))
        assert len(chunks) == 2
        assert {c.chat_id for c in chunks} == {"A", "B"}
        assert len(chunks[0].messages) == 2
        assert len(chunks[1].messages) == 2


# ---------------------------------------------------------------------------
# Lookback eviction.
# ---------------------------------------------------------------------------

class TestLookbackEviction:
    def test_lookback_closes_old_chunks(self) -> None:
        # Создаём 6 chunks с gap > 5 мин (каждый по 1 сообщению).
        # lookback=2 → при открытии 3-го первый должен вытесниться в closed.
        msgs = [
            _msg(str(i), offset_sec=i * 600)
            for i in range(6)
        ]
        chunks = list(chunk_messages(msgs, lookback=2))
        assert len(chunks) == 6  # все chunks должны дожить до flush

    def test_old_chunk_unreachable_via_reply(self) -> None:
        """
        Сообщение, отвечающее в chunk вне lookback, не сможет вернуться
        в него — откроется новый chunk с fallback на time-gap.
        """
        msgs = [
            _msg("1", offset_sec=0),               # chunk 1
            _msg("2", offset_sec=600),             # chunk 2 (gap > 5 мин)
            _msg("3", offset_sec=1200),            # chunk 3
            _msg("4", offset_sec=1800),            # chunk 4
            # Reply на 1, но chunk 1 уже вытеснен lookback'ом.
            _msg("late", offset_sec=2400, reply="1"),
        ]
        chunks = list(chunk_messages(msgs, lookback=2))
        # "late" должен быть в отдельном chunk'е (time-gap > 5 мин).
        late_chunk = [c for c in chunks if "late" in c.message_ids]
        assert len(late_chunk) == 1
        assert "1" not in late_chunk[0].message_ids


# ---------------------------------------------------------------------------
# Chunk утилиты.
# ---------------------------------------------------------------------------

class TestChunkUtilities:
    def test_chunk_text_joins_with_newlines(self) -> None:
        chunk = Chunk(chat_id="x")
        chunk.append(_msg("1", text="первое"))
        chunk.append(_msg("2", text="второе"))
        assert chunk.text == "первое\nвторое"

    def test_chunk_start_end_timestamps(self) -> None:
        chunk = Chunk(chat_id="x")
        chunk.append(_msg("1", offset_sec=0))
        chunk.append(_msg("2", offset_sec=120))
        chunk.append(_msg("3", offset_sec=240))
        assert chunk.start_timestamp == BASE_TIME
        assert chunk.end_timestamp == BASE_TIME + timedelta(seconds=240)

    def test_empty_chunk(self) -> None:
        chunk = Chunk(chat_id="x")
        assert chunk.is_empty()
        assert chunk.start_timestamp is None
        assert chunk.end_timestamp is None
        assert chunk.char_len == 0
        assert chunk.text == ""

    def test_has_message(self) -> None:
        chunk = Chunk(chat_id="x")
        chunk.append(_msg("42"))
        assert chunk.has_message("42")
        assert not chunk.has_message("43")


# ---------------------------------------------------------------------------
# Builder lifecycle.
# ---------------------------------------------------------------------------

class TestBuilderLifecycle:
    def test_flush_empty_builder(self) -> None:
        builder = ChunkBuilder()
        assert builder.flush() == []

    def test_flush_resets_state(self) -> None:
        builder = ChunkBuilder()
        builder.add(_msg("1"))
        first = builder.flush()
        assert len(first) == 1

        # После flush — чистое состояние.
        builder.add(_msg("2"))
        second = builder.flush()
        assert len(second) == 1
        assert second[0].messages[0].message_id == "2"

    def test_chronological_order_preserved(self) -> None:
        """Сообщения внутри chunk'а должны идти в том порядке, в каком поданы."""
        msgs = [_msg(str(i), offset_sec=i * 10) for i in range(5)]
        chunks = list(chunk_messages(msgs))
        assert len(chunks) == 1
        ids = [m.message_id for m in chunks[0].messages]
        assert ids == ["0", "1", "2", "3", "4"]


# ---------------------------------------------------------------------------
# Real-world scenarios.
# ---------------------------------------------------------------------------

class TestRealWorldScenarios:
    def test_typical_dialog_with_replies(self) -> None:
        """
        Типичный диалог: 3 реплики подряд, пауза, потом reply на первую.
        Должно получиться: [1,2,3] в одном chunk'е, [late reply to 1]
        также в нём, потому что reply-to сработал.
        """
        msgs = [
            _msg("1", offset_sec=0, text="Привет!"),
            _msg("2", offset_sec=30, text="Привет", reply="1"),
            _msg("3", offset_sec=60, text="Как дела?"),
            # Пауза 10 минут — time-gap сработал бы, но reply держит связь.
            _msg(
                "4", offset_sec=600, text="кстати вчера...", reply="1"
            ),
        ]
        chunks = list(chunk_messages(msgs))
        assert len(chunks) == 1
        assert len(chunks[0].messages) == 4

    def test_two_parallel_topics_interleaved(self) -> None:
        """
        Два параллельных тредa: A-1 → A-2 (reply) и B-1 → B-2 (reply),
        перемешанные по времени. Reply-to должен удержать каждый топик
        в своей chunk-группе несмотря на переплетение.
        """
        msgs = [
            _msg("A1", offset_sec=0, text="Тема A"),
            _msg("B1", offset_sec=30, text="Тема B"),
            _msg("A2", offset_sec=60, text="про A", reply="A1"),
            _msg("B2", offset_sec=90, text="про B", reply="B1"),
        ]
        # Все сообщения уходят в ОДИН chunk, потому что gap < 5 мин
        # и reply'и цепляются к тому же текущему chunk'у.
        # Это корректное поведение для "плоского" разговора.
        chunks = list(chunk_messages(msgs))
        assert len(chunks) == 1
        # Более сложное разделение потребует LLM-based topic clustering,
        # что вне scope'а regex-chunker'а. Фиксируем поведение тестом.
        assert len(chunks[0].messages) == 4
