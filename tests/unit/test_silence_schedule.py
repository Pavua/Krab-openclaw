# -*- coding: utf-8 -*-
"""
Тесты для SilenceScheduleManager (src/core/silence_schedule.py).

Покрывает:
- парсинг времени (_parse_time)
- логику попадания в диапазон (_in_range): обычный и ночной
- set_schedule / disable_schedule
- is_schedule_active с мокованием времени
- формат статуса
- персистентность (save/load state)
- фоновый loop (apply/remove mute callbacks)
"""

from __future__ import annotations

import asyncio
import json
from datetime import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Патчим путь к state-файлу до импорта модуля
_FAKE_STATE_PATH = Path("/tmp/test_silence_schedule_state.json")


@pytest.fixture(autouse=True)
def patch_state_path(tmp_path):
    """Перенаправляем _STATE_PATH на временный файл."""
    fake = tmp_path / "silence_schedule.json"
    with patch("src.core.silence_schedule._STATE_PATH", fake):
        yield fake


@pytest.fixture
def manager():
    """Свежий SilenceScheduleManager для каждого теста."""
    from src.core.silence_schedule import SilenceScheduleManager
    return SilenceScheduleManager()


# ── Парсинг времени ────────────────────────────────────────────────────────

class TestParseTime:
    def test_valid_hhmm(self):
        from src.core.silence_schedule import _parse_time
        t = _parse_time("23:00")
        assert t == time(23, 0)

    def test_valid_zero(self):
        from src.core.silence_schedule import _parse_time
        t = _parse_time("00:00")
        assert t == time(0, 0)

    def test_valid_with_spaces(self):
        from src.core.silence_schedule import _parse_time
        t = _parse_time(" 08:30 ")
        assert t == time(8, 30)

    def test_invalid_no_colon(self):
        from src.core.silence_schedule import _parse_time
        with pytest.raises(ValueError, match="Неверный формат"):
            _parse_time("2300")

    def test_invalid_empty(self):
        from src.core.silence_schedule import _parse_time
        with pytest.raises(ValueError):
            _parse_time("")

    def test_invalid_hour(self):
        from src.core.silence_schedule import _parse_time
        with pytest.raises(ValueError):
            _parse_time("25:00")

    def test_invalid_minute(self):
        from src.core.silence_schedule import _parse_time
        with pytest.raises(ValueError):
            _parse_time("10:70")


# ── Логика диапазона ───────────────────────────────────────────────────────

class TestInRange:
    def test_normal_range_inside(self):
        from src.core.silence_schedule import _in_range
        assert _in_range(time(10, 0), time(9, 0), time(17, 0)) is True

    def test_normal_range_outside(self):
        from src.core.silence_schedule import _in_range
        assert _in_range(time(18, 0), time(9, 0), time(17, 0)) is False

    def test_normal_range_on_start(self):
        from src.core.silence_schedule import _in_range
        assert _in_range(time(9, 0), time(9, 0), time(17, 0)) is True

    def test_normal_range_on_end_exclusive(self):
        from src.core.silence_schedule import _in_range
        # end не включается
        assert _in_range(time(17, 0), time(9, 0), time(17, 0)) is False

    def test_night_range_after_midnight(self):
        """23:00-08:00 — 02:00 попадает."""
        from src.core.silence_schedule import _in_range
        assert _in_range(time(2, 0), time(23, 0), time(8, 0)) is True

    def test_night_range_before_midnight(self):
        """23:00-08:00 — 23:30 попадает."""
        from src.core.silence_schedule import _in_range
        assert _in_range(time(23, 30), time(23, 0), time(8, 0)) is True

    def test_night_range_outside(self):
        """23:00-08:00 — 12:00 не попадает."""
        from src.core.silence_schedule import _in_range
        assert _in_range(time(12, 0), time(23, 0), time(8, 0)) is False

    def test_night_range_on_end_exclusive(self):
        """23:00-08:00 — 08:00 не попадает (exclusive)."""
        from src.core.silence_schedule import _in_range
        assert _in_range(time(8, 0), time(23, 0), time(8, 0)) is False


# ── SilenceScheduleManager ────────────────────────────────────────────────

class TestSilenceScheduleManager:
    def test_disabled_by_default(self, manager):
        assert manager._enabled is False
        assert manager.is_schedule_active() is False

    def test_set_schedule_enables(self, manager):
        manager.set_schedule("23:00", "08:00")
        assert manager._enabled is True
        assert manager._start_str == "23:00"
        assert manager._end_str == "08:00"

    def test_set_schedule_invalid_format_raises(self, manager):
        with pytest.raises(ValueError):
            manager.set_schedule("25:00", "08:00")

    def test_disable_schedule(self, manager):
        manager.set_schedule("23:00", "08:00")
        manager.disable_schedule()
        assert manager._enabled is False
        assert manager.is_schedule_active() is False

    def test_is_schedule_active_true(self, manager):
        """Мокаем текущее время внутри диапазона."""
        manager.set_schedule("22:00", "08:00")
        fake_now = MagicMock()
        fake_now.time.return_value = time(23, 30)
        with patch("src.core.silence_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            assert manager.is_schedule_active() is True

    def test_is_schedule_active_false_outside(self, manager):
        """Мокаем текущее время вне диапазона."""
        manager.set_schedule("22:00", "08:00")
        fake_now = MagicMock()
        fake_now.time.return_value = time(12, 0)
        with patch("src.core.silence_schedule.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            assert manager.is_schedule_active() is False

    def test_is_schedule_active_disabled(self, manager):
        """Выключенное расписание всегда возвращает False."""
        manager._enabled = False
        manager._start_str = "22:00"
        manager._end_str = "08:00"
        assert manager.is_schedule_active() is False

    def test_get_status_disabled(self, manager):
        st = manager.get_status()
        assert st["enabled"] is False
        assert st["active_now"] is False

    def test_get_status_enabled(self, manager):
        manager.set_schedule("23:00", "08:00")
        st = manager.get_status()
        assert st["enabled"] is True
        assert st["start"] == "23:00"
        assert st["end"] == "08:00"

    def test_format_status_disabled(self, manager):
        text = manager.format_status()
        assert "выключено" in text

    def test_format_status_enabled(self, manager):
        manager.set_schedule("23:00", "08:00")
        text = manager.format_status()
        assert "23:00" in text
        assert "08:00" in text


# ── Персистентность ────────────────────────────────────────────────────────

class TestPersistence:
    def test_set_schedule_saves_to_file(self, manager, patch_state_path):
        manager.set_schedule("22:00", "07:00")
        assert patch_state_path.exists()
        data = json.loads(patch_state_path.read_text())
        assert data["enabled"] is True
        assert data["start"] == "22:00"
        assert data["end"] == "07:00"

    def test_disable_saves_to_file(self, manager, patch_state_path):
        manager.set_schedule("22:00", "07:00")
        manager.disable_schedule()
        data = json.loads(patch_state_path.read_text())
        assert data["enabled"] is False

    def test_load_state_on_init(self, patch_state_path):
        """Новый менеджер загружает ранее сохранённое состояние."""
        patch_state_path.write_text(
            json.dumps({"enabled": True, "start": "23:00", "end": "06:00"}),
            encoding="utf-8",
        )
        from src.core.silence_schedule import SilenceScheduleManager
        m2 = SilenceScheduleManager()
        assert m2._enabled is True
        assert m2._start_str == "23:00"
        assert m2._end_str == "06:00"

    def test_load_state_missing_file(self, patch_state_path):
        """Отсутствующий файл — defaults."""
        assert not patch_state_path.exists()
        from src.core.silence_schedule import SilenceScheduleManager
        m = SilenceScheduleManager()
        assert m._enabled is False

    def test_load_state_corrupt_file(self, patch_state_path):
        """Corrupt JSON — graceful fallback."""
        patch_state_path.write_text("not json", encoding="utf-8")
        from src.core.silence_schedule import SilenceScheduleManager
        m = SilenceScheduleManager()
        assert m._enabled is False


# ── Фоновый loop ─────────────────────────────────────────────────────────

class TestRunLoop:
    @pytest.mark.asyncio
    async def test_loop_applies_mute_when_active(self, manager):
        """Loop вызывает apply_mute когда расписание активно."""
        manager.set_schedule("00:00", "23:59")  # всегда активно (кроме 23:59)
        apply_called = []
        remove_called = []

        def apply_fn():
            apply_called.append(1)

        def remove_fn():
            remove_called.append(1)

        # Мокаем sleep чтобы не ждать 60 сек
        call_count = 0

        async def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError

        with patch("src.core.silence_schedule.asyncio.sleep", side_effect=fake_sleep):
            with patch("src.core.silence_schedule._CHECK_INTERVAL_SEC", 0):
                task = asyncio.create_task(manager.run_loop(apply_fn, remove_fn))
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        assert len(apply_called) >= 1
        assert len(remove_called) == 0

    @pytest.mark.asyncio
    async def test_loop_removes_mute_when_inactive(self, manager):
        """Loop вызывает remove_mute когда выходит из расписания."""
        manager.set_schedule("23:00", "08:00")
        manager._mute_applied = True  # эмулируем ранее применённый mute

        remove_called = []

        def apply_fn():
            pass

        def remove_fn():
            remove_called.append(1)

        call_count = 0

        async def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError

        # Текущее время вне расписания
        fake_now = MagicMock()
        fake_now.time.return_value = time(12, 0)

        with patch("src.core.silence_schedule.asyncio.sleep", side_effect=fake_sleep):
            with patch("src.core.silence_schedule.datetime") as mock_dt:
                mock_dt.now.return_value = fake_now
                task = asyncio.create_task(manager.run_loop(apply_fn, remove_fn))
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        assert len(remove_called) >= 1

    @pytest.mark.asyncio
    async def test_loop_handles_cancelled_error(self, manager):
        """Loop корректно завершается при CancelledError (не пробрасывает)."""
        async def instant_cancel(n):
            raise asyncio.CancelledError

        with patch("src.core.silence_schedule.asyncio.sleep", side_effect=instant_cancel):
            task = asyncio.create_task(manager.run_loop(lambda: None, lambda: None))
            # loop должен завершиться без исключения — CancelledError перехватывается внутри
            result = await asyncio.wait_for(task, timeout=1.0)
            assert result is None

    @pytest.mark.asyncio
    async def test_loop_handles_async_callbacks(self, manager):
        """Loop поддерживает async apply/remove callbacks."""
        manager.set_schedule("00:00", "23:59")
        apply_called = []

        async def async_apply():
            apply_called.append(1)

        call_count = 0

        async def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError

        with patch("src.core.silence_schedule.asyncio.sleep", side_effect=fake_sleep):
            task = asyncio.create_task(manager.run_loop(async_apply, lambda: None))
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        assert len(apply_called) >= 1
