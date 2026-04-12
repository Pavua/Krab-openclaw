# -*- coding: utf-8 -*-
"""
Тесты модуля message_scheduler.

Проверяем:
1) parse_schedule_spec — все поддерживаемые форматы (+Nm, +Nh, HH:MM)
2) split_schedule_input — разбивка на spec + текст
3) MessageSchedulerStore — add/list_pending/mark_cancelled/get
4) format_scheduled_list — форматирование пустого/непустого списка
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.message_scheduler import (
    MessageSchedulerStore,
    ScheduledMsgRecord,
    _MIN_SCHEDULE_SECONDS,
    _now_local,
    format_scheduled_list,
    parse_schedule_spec,
    split_schedule_input,
)


# ---------------------------------------------------------------------------
# parse_schedule_spec
# ---------------------------------------------------------------------------

class TestParseScheduleSpec:
    """Тесты парсинга time_spec."""

    def test_plus_minutes_30(self) -> None:
        """'+30m' должен дать offset +30 минут."""
        now = _now_local()
        due = parse_schedule_spec("+30m")
        delta = (due - now).total_seconds()
        assert 29 * 60 <= delta <= 31 * 60

    def test_plus_minutes_1(self) -> None:
        """'+1m' — минимальный допустимый."""
        due = parse_schedule_spec("+1m")
        assert (due - _now_local()).total_seconds() >= 50

    def test_plus_hours_2(self) -> None:
        """'+2h' даёт ~7200 секунд вперёд."""
        now = _now_local()
        due = parse_schedule_spec("+2h")
        delta = (due - now).total_seconds()
        assert 7100 <= delta <= 7300

    def test_plus_hours_uppercase(self) -> None:
        """'+3H' — регистронезависимо."""
        now = _now_local()
        due = parse_schedule_spec("+3H")
        delta = (due - now).total_seconds()
        assert 3 * 3600 - 10 <= delta <= 3 * 3600 + 10

    def test_hhmm_future_today(self) -> None:
        """HH:MM в будущем сегодня — должен вернуть сегодняшнюю дату."""
        now = _now_local().replace(hour=10, minute=0, second=0, microsecond=0)
        with patch("src.core.message_scheduler._now_local", return_value=now):
            due = parse_schedule_spec("11:30")
        assert due.hour == 11
        assert due.minute == 30
        assert due.date() == now.date()

    def test_hhmm_past_rolls_to_tomorrow(self) -> None:
        """HH:MM уже прошёл сегодня — планируется на завтра."""
        now = _now_local().replace(hour=20, minute=0, second=0, microsecond=0)
        with patch("src.core.message_scheduler._now_local", return_value=now):
            due = parse_schedule_spec("09:00")
        assert due.date() == (now + timedelta(days=1)).date()
        assert due.hour == 9

    def test_hhmm_midnight(self) -> None:
        """00:00 — полночь, должно быть завтра если сейчас не 00:00."""
        now = _now_local().replace(hour=12, minute=0, second=0, microsecond=0)
        with patch("src.core.message_scheduler._now_local", return_value=now):
            due = parse_schedule_spec("00:00")
        assert due.date() == (now + timedelta(days=1)).date()

    def test_invalid_spec_raises(self) -> None:
        """Нераспознанный формат — ValueError."""
        with pytest.raises(ValueError):
            parse_schedule_spec("завтра")

    def test_zero_minutes_raises(self) -> None:
        """+0m — недопустимо."""
        with pytest.raises(ValueError):
            parse_schedule_spec("+0m")

    def test_zero_hours_raises(self) -> None:
        """+0h — недопустимо."""
        with pytest.raises(ValueError):
            parse_schedule_spec("+0h")

    def test_hhmm_invalid_range_raises(self) -> None:
        """25:00 — за пределами допустимого диапазона."""
        with pytest.raises(ValueError):
            parse_schedule_spec("25:00")

    def test_hhmm_invalid_minutes_raises(self) -> None:
        """14:70 — недопустимые минуты."""
        with pytest.raises(ValueError):
            parse_schedule_spec("14:70")

    def test_empty_string_raises(self) -> None:
        """Пустая строка — ValueError."""
        with pytest.raises(ValueError):
            parse_schedule_spec("")

    def test_plus_minutes_large(self) -> None:
        """+1440m = 24 часа — допустимо."""
        now = _now_local()
        due = parse_schedule_spec("+1440m")
        delta = (due - now).total_seconds()
        assert abs(delta - 86400) < 10

    def test_plus_hours_returns_timezone_aware(self) -> None:
        """Результат всегда timezone-aware."""
        due = parse_schedule_spec("+1h")
        assert due.tzinfo is not None


# ---------------------------------------------------------------------------
# split_schedule_input
# ---------------------------------------------------------------------------

class TestSplitScheduleInput:
    """Тесты разбивки аргумента !schedule."""

    def test_plus_minutes_with_text(self) -> None:
        spec, text = split_schedule_input("+30m Напомни позвонить")
        assert spec == "+30m"
        assert text == "Напомни позвонить"

    def test_plus_hours_with_text(self) -> None:
        spec, text = split_schedule_input("+2h Отправить отчёт")
        assert spec == "+2h"
        assert text == "Отправить отчёт"

    def test_hhmm_with_text(self) -> None:
        spec, text = split_schedule_input("14:30 Встреча с командой")
        assert spec == "14:30"
        assert text == "Встреча с командой"

    def test_list_command(self) -> None:
        spec, text = split_schedule_input("list")
        assert spec == "list"
        assert text == ""

    def test_cancel_command_with_id(self) -> None:
        spec, text = split_schedule_input("cancel abc12345")
        assert spec == "cancel"
        assert text == "abc12345"

    def test_список_alias(self) -> None:
        """Русский псевдоним команды list."""
        spec, text = split_schedule_input("список")
        assert spec == "list"
        assert text == ""

    def test_отмена_alias(self) -> None:
        """Русский псевдоним команды cancel."""
        spec, text = split_schedule_input("отмена abc")
        assert spec == "cancel"
        assert text == "abc"

    def test_empty_string(self) -> None:
        spec, text = split_schedule_input("")
        assert spec == ""
        assert text == ""

    def test_text_with_spaces(self) -> None:
        """Текст с пробелами должен сохраняться целиком."""
        spec, text = split_schedule_input("+10m Купить молоко и хлеб")
        assert spec == "+10m"
        assert text == "Купить молоко и хлеб"


# ---------------------------------------------------------------------------
# MessageSchedulerStore
# ---------------------------------------------------------------------------

class TestMessageSchedulerStore:
    """Тесты хранилища отложенных сообщений."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> MessageSchedulerStore:
        return MessageSchedulerStore(storage_path=tmp_path / "scheduled.json")

    def test_add_returns_record_id(self, store: MessageSchedulerStore) -> None:
        """add() должен вернуть непустой record_id."""
        schedule_time = _now_local() + timedelta(hours=1)
        rid = store.add(
            chat_id="123",
            text="тест",
            schedule_time=schedule_time,
            tg_message_id=999,
        )
        assert rid
        assert len(rid) == 8

    def test_add_persists_to_file(self, store: MessageSchedulerStore, tmp_path: Path) -> None:
        """После add() файл должен существовать и содержать запись."""
        schedule_time = _now_local() + timedelta(minutes=30)
        store.add(chat_id="111", text="привет", schedule_time=schedule_time, tg_message_id=42)
        assert store.storage_path.exists()
        payload = json.loads(store.storage_path.read_text(encoding="utf-8"))
        assert len(payload["records"]) == 1
        assert payload["records"][0]["text"] == "привет"

    def test_list_pending_empty(self, store: MessageSchedulerStore) -> None:
        """Пустое хранилище — пустой список."""
        assert store.list_pending() == []

    def test_list_pending_returns_only_pending(self, store: MessageSchedulerStore) -> None:
        """list_pending() не возвращает cancelled записи."""
        schedule_time = _now_local() + timedelta(hours=1)
        rid = store.add(chat_id="1", text="a", schedule_time=schedule_time, tg_message_id=1)
        store.mark_cancelled(rid)
        assert store.list_pending() == []

    def test_list_pending_filters_by_chat_id(self, store: MessageSchedulerStore) -> None:
        """list_pending(chat_id=X) возвращает только записи чата X."""
        t = _now_local() + timedelta(hours=1)
        store.add(chat_id="111", text="один", schedule_time=t, tg_message_id=1)
        store.add(chat_id="222", text="два", schedule_time=t, tg_message_id=2)
        result = store.list_pending(chat_id="111")
        assert len(result) == 1
        assert result[0].chat_id == "111"

    def test_list_pending_sorted_by_time(self, store: MessageSchedulerStore) -> None:
        """Записи должны быть отсортированы по schedule_time_iso."""
        now = _now_local()
        store.add(chat_id="1", text="поздно", schedule_time=now + timedelta(hours=3), tg_message_id=1)
        store.add(chat_id="1", text="рано", schedule_time=now + timedelta(hours=1), tg_message_id=2)
        result = store.list_pending(chat_id="1")
        assert result[0].text == "рано"
        assert result[1].text == "поздно"

    def test_get_returns_record(self, store: MessageSchedulerStore) -> None:
        """get() возвращает запись по record_id."""
        t = _now_local() + timedelta(hours=1)
        rid = store.add(chat_id="1", text="тест", schedule_time=t, tg_message_id=77)
        rec = store.get(rid)
        assert rec is not None
        assert rec.record_id == rid
        assert rec.tg_message_id == 77
        assert rec.text == "тест"

    def test_get_nonexistent_returns_none(self, store: MessageSchedulerStore) -> None:
        """get() для несуществующего ID возвращает None."""
        assert store.get("nonexistent") is None

    def test_mark_cancelled_returns_true(self, store: MessageSchedulerStore) -> None:
        """mark_cancelled() возвращает True для существующей записи."""
        t = _now_local() + timedelta(hours=1)
        rid = store.add(chat_id="1", text="x", schedule_time=t, tg_message_id=1)
        assert store.mark_cancelled(rid) is True

    def test_mark_cancelled_nonexistent_returns_false(self, store: MessageSchedulerStore) -> None:
        """mark_cancelled() возвращает False для несуществующего ID."""
        assert store.mark_cancelled("nope") is False

    def test_mark_cancelled_changes_status(self, store: MessageSchedulerStore) -> None:
        """После mark_cancelled() статус записи становится 'cancelled'."""
        t = _now_local() + timedelta(hours=1)
        rid = store.add(chat_id="1", text="y", schedule_time=t, tg_message_id=5)
        store.mark_cancelled(rid)
        rec = store.get(rid)
        assert rec is not None
        assert rec.status == "cancelled"

    def test_multiple_adds_unique_ids(self, store: MessageSchedulerStore) -> None:
        """Несколько add() — все ID уникальны."""
        t = _now_local() + timedelta(hours=1)
        ids = [store.add(chat_id="1", text=f"msg{i}", schedule_time=t, tg_message_id=i) for i in range(10)]
        assert len(set(ids)) == 10

    def test_storage_survives_reload(self, store: MessageSchedulerStore, tmp_path: Path) -> None:
        """Записи сохраняются и загружаются из файла."""
        t = _now_local() + timedelta(hours=2)
        rid = store.add(chat_id="42", text="persist", schedule_time=t, tg_message_id=100)

        # Создаём новый store с тем же путём — должен подхватить данные
        store2 = MessageSchedulerStore(storage_path=store.storage_path)
        recs = store2.list_pending(chat_id="42")
        assert len(recs) == 1
        assert recs[0].record_id == rid
        assert recs[0].text == "persist"

    def test_corrupted_file_returns_empty(self, tmp_path: Path) -> None:
        """При повреждённом JSON файле возвращает пустой результат."""
        storage = tmp_path / "bad.json"
        storage.write_text("not-json{{{", encoding="utf-8")
        store = MessageSchedulerStore(storage_path=storage)
        assert store.list_pending() == []

    def test_missing_storage_dir_created(self, tmp_path: Path) -> None:
        """Директория создаётся автоматически при первом add()."""
        deep_path = tmp_path / "a" / "b" / "c" / "scheduled.json"
        store = MessageSchedulerStore(storage_path=deep_path)
        t = _now_local() + timedelta(hours=1)
        store.add(chat_id="1", text="x", schedule_time=t, tg_message_id=1)
        assert deep_path.exists()


# ---------------------------------------------------------------------------
# ScheduledMsgRecord
# ---------------------------------------------------------------------------

class TestScheduledMsgRecord:
    """Тесты dataclass записи."""

    def test_from_dict_roundtrip(self) -> None:
        """from_dict(to_dict()) должен восстанавливать исходные данные."""
        rec = ScheduledMsgRecord(
            record_id="abcd1234",
            chat_id="999",
            text="тестовый текст",
            schedule_time_iso="2026-04-12T14:30:00+03:00",
            tg_message_id=12345,
            created_at_iso="2026-04-12T10:00:00+03:00",
            status="pending",
        )
        restored = ScheduledMsgRecord.from_dict(rec.to_dict())
        assert restored.record_id == rec.record_id
        assert restored.chat_id == rec.chat_id
        assert restored.text == rec.text
        assert restored.tg_message_id == rec.tg_message_id
        assert restored.status == rec.status

    def test_from_dict_defaults(self) -> None:
        """from_dict() с минимальным набором полей — дефолтный статус pending."""
        rec = ScheduledMsgRecord.from_dict({
            "record_id": "test",
            "chat_id": "1",
            "text": "hello",
            "schedule_time_iso": "2026-04-12T14:30:00",
            "tg_message_id": 1,
            "created_at_iso": "2026-04-12T10:00:00",
        })
        assert rec.status == "pending"


# ---------------------------------------------------------------------------
# format_scheduled_list
# ---------------------------------------------------------------------------

class TestFormatScheduledList:
    """Тесты форматирования списка запланированных сообщений."""

    def test_empty_list(self) -> None:
        """Пустой список — дружелюбное сообщение."""
        result = format_scheduled_list([])
        assert "Нет запланированных" in result

    def test_single_record(self) -> None:
        """Одна запись — выводится ID и превью текста."""
        rec = ScheduledMsgRecord(
            record_id="abc123",
            chat_id="1",
            text="Позвонить маме",
            schedule_time_iso="2026-04-12T14:30:00+03:00",
            tg_message_id=5,
            created_at_iso="2026-04-12T10:00:00+03:00",
        )
        result = format_scheduled_list([rec])
        assert "abc123" in result
        assert "Позвонить маме" in result
        assert "14:30" in result

    def test_long_text_truncated(self) -> None:
        """Длинный текст обрезается с многоточием."""
        long_text = "А" * 100
        rec = ScheduledMsgRecord(
            record_id="xyz",
            chat_id="1",
            text=long_text,
            schedule_time_iso="2026-04-12T14:30:00+03:00",
            tg_message_id=1,
            created_at_iso="2026-04-12T10:00:00+03:00",
        )
        result = format_scheduled_list([rec])
        assert "…" in result

    def test_cancel_hint_present(self) -> None:
        """В выводе есть подсказка по отмене."""
        rec = ScheduledMsgRecord(
            record_id="zz",
            chat_id="1",
            text="x",
            schedule_time_iso="2026-04-12T15:00:00+03:00",
            tg_message_id=1,
            created_at_iso="2026-04-12T10:00:00+03:00",
        )
        result = format_scheduled_list([rec])
        assert "cancel" in result.lower() or "отмен" in result.lower()

    def test_multiple_records(self) -> None:
        """Несколько записей — все присутствуют в выводе."""
        records = [
            ScheduledMsgRecord(
                record_id=f"r{i}",
                chat_id="1",
                text=f"msg {i}",
                schedule_time_iso=f"2026-04-12T{10+i}:00:00+03:00",
                tg_message_id=i,
                created_at_iso="2026-04-12T09:00:00+03:00",
            )
            for i in range(3)
        ]
        result = format_scheduled_list(records)
        for i in range(3):
            assert f"r{i}" in result
