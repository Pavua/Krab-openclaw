# -*- coding: utf-8 -*-
"""
silence_schedule.py — расписание ночного режима (silence) для Краба.

Позволяет задать временной диапазон HH:MM-HH:MM, в котором
Краб автоматически включает глобальную тишину.

Расписание хранится в JSON: ~/.openclaw/krab_runtime_state/silence_schedule.json
Проверка активности — через is_schedule_active() из фонового asyncio-таска.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, time
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

_STATE_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "silence_schedule.json"

# Как часто (в секундах) проверяем расписание в фоновом loop
_CHECK_INTERVAL_SEC = 60


def _load_state() -> dict[str, Any]:
    """Загружает состояние из JSON. Возвращает {} при ошибке."""
    try:
        if _STATE_PATH.exists():
            return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("silence_schedule_load_error", error=str(exc))
    return {}


def _save_state(state: dict[str, Any]) -> None:
    """Сохраняет состояние в JSON (атомарно через tmp)."""
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_STATE_PATH)
    except Exception as exc:
        logger.error("silence_schedule_save_error", error=str(exc))


def _parse_time(s: str) -> time:
    """Парсит строку HH:MM → datetime.time. Кидает ValueError при ошибке."""
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Неверный формат времени: {s!r}. Ожидается HH:MM")
    hh, mm = int(parts[0]), int(parts[1])
    return time(hh, mm)


def _in_range(now_t: time, start: time, end: time) -> bool:
    """Проверяет, попадает ли now_t в диапазон [start, end).

    Поддерживает ночные диапазоны (start > end), например 23:00-08:00.
    """
    if start <= end:
        # Обычный диапазон в пределах одних суток
        return start <= now_t < end
    else:
        # Ночной диапазон, переходящий через полночь: 23:00-08:00
        return now_t >= start or now_t < end


class SilenceScheduleManager:
    """Управление расписанием ночного (scheduled) silence-режима."""

    def __init__(self) -> None:
        state = _load_state()
        # enabled — расписание активировано (не то же самое, что сейчас в тишине)
        self._enabled: bool = state.get("enabled", False)
        # start/end хранятся как строки "HH:MM"
        self._start_str: str | None = state.get("start")
        self._end_str: str | None = state.get("end")
        # Флаг: применили ли мы глобальный mute в текущем интервале (чтобы не спамить)
        self._mute_applied: bool = False

    # ── Public API ───────────────────────────────────────────────────────────

    def set_schedule(self, start_str: str, end_str: str) -> None:
        """Сохраняет расписание. Кидает ValueError при неверном формате."""
        # Валидируем перед сохранением
        _parse_time(start_str)
        _parse_time(end_str)
        self._start_str = start_str
        self._end_str = end_str
        self._enabled = True
        self._mute_applied = False
        self._persist()
        logger.info("silence_schedule_set", start=start_str, end=end_str)

    def disable_schedule(self) -> None:
        """Отключает расписание."""
        self._enabled = False
        self._mute_applied = False
        self._persist()
        logger.info("silence_schedule_disabled")

    def is_schedule_active(self) -> bool:
        """Возвращает True если сейчас попадаем в запланированный тихий час."""
        if not self._enabled or not self._start_str or not self._end_str:
            return False
        try:
            start = _parse_time(self._start_str)
            end = _parse_time(self._end_str)
            now_t = datetime.now().time().replace(second=0, microsecond=0)
            return _in_range(now_t, start, end)
        except ValueError as exc:
            logger.warning("silence_schedule_check_error", error=str(exc))
            return False

    def get_status(self) -> dict[str, Any]:
        """Возвращает словарь с текущим состоянием расписания."""
        return {
            "enabled": self._enabled,
            "start": self._start_str,
            "end": self._end_str,
            "active_now": self.is_schedule_active(),
        }

    def format_status(self) -> str:
        """Форматированный статус для Telegram."""
        st = self.get_status()
        if not st["enabled"] or not st["start"]:
            return "🌙 Расписание тишины: **выключено**"
        active_marker = " ✅ (сейчас активно)" if st["active_now"] else ""
        return (
            f"🌙 Расписание тишины: **{st['start']}–{st['end']}**{active_marker}\n"
            f"Для отключения: `!тишина расписание выкл`"
        )

    # ── Фоновый loop ─────────────────────────────────────────────────────────

    async def run_loop(self, apply_mute_fn: Any, remove_mute_fn: Any) -> None:
        """Фоновый asyncio-таск: проверяет расписание каждые 60 сек.

        apply_mute_fn()  — вызывается при входе в тихий час (активирует глобальный mute)
        remove_mute_fn() — вызывается при выходе из тихого часа
        """
        logger.info("silence_schedule_loop_started")
        while True:
            try:
                await asyncio.sleep(_CHECK_INTERVAL_SEC)
                active = self.is_schedule_active()
                if active and not self._mute_applied:
                    logger.info("silence_schedule_applying_mute")
                    await _maybe_await(apply_mute_fn)
                    self._mute_applied = True
                elif not active and self._mute_applied:
                    logger.info("silence_schedule_removing_mute")
                    await _maybe_await(remove_mute_fn)
                    self._mute_applied = False
            except asyncio.CancelledError:
                logger.info("silence_schedule_loop_stopped")
                break
            except Exception as exc:
                logger.error("silence_schedule_loop_error", error=str(exc))

    # ── Internal ─────────────────────────────────────────────────────────────

    def _persist(self) -> None:
        _save_state(
            {
                "enabled": self._enabled,
                "start": self._start_str,
                "end": self._end_str,
            }
        )


async def _maybe_await(fn: Any) -> None:
    """Вызывает fn(), ждёт если корутина."""
    result = fn()
    if asyncio.iscoroutine(result):
        await result


# Singleton
silence_schedule_manager = SilenceScheduleManager()
