# -*- coding: utf-8 -*-
"""Тесты для PatternDetector (Idea 32 — Proactive Suggestions)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.proactive_suggestions import (
    ACTION_CALC_QUERY,
    ACTION_NEWS_FORWARD,
    ACTION_SCREENSHOT_UPLOAD,
    ACTION_TIMEZONE_QUERY,
    PatternDetector,
)


def _make_detector(tmp_path: Path, clock_holder: list[datetime]) -> PatternDetector:
    storage = tmp_path / "proactive_actions.json"
    return PatternDetector(
        storage_path=storage,
        now_fn=lambda: clock_holder[0],
    )


def test_pattern_detected_at_threshold(tmp_path: Path) -> None:
    """3 timezone-запроса в одну TZ → выдаём Suggestion."""
    clock = [datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)]
    detector = _make_detector(tmp_path, clock)
    for _ in range(3):
        detector.record_action(
            ACTION_TIMEZONE_QUERY,
            chat_id=100,
            owner_id="owner",
            metadata={"tz": "Europe/Madrid"},
        )
        clock[0] += timedelta(minutes=10)
    suggestions = detector.detect_patterns(min_count=3, window_hours=24)
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.action_type == "setup_timezone_widget"
    assert s.evidence["tz"] == "Europe/Madrid"
    assert s.evidence["count"] == 3
    assert s.confidence >= 0.5


def test_below_threshold_not_detected(tmp_path: Path) -> None:
    """2 запроса при min_count=3 → пусто."""
    clock = [datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)]
    detector = _make_detector(tmp_path, clock)
    for _ in range(2):
        detector.record_action(
            ACTION_TIMEZONE_QUERY,
            chat_id=100,
            owner_id="owner",
            metadata={"tz": "Europe/Madrid"},
        )
    assert detector.detect_patterns(min_count=3, window_hours=24) == []


def test_sliding_window_expiry(tmp_path: Path) -> None:
    """Действия старше окна не учитываются и вычищаются."""
    clock = [datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)]
    detector = _make_detector(tmp_path, clock)
    for _ in range(3):
        detector.record_action(
            ACTION_SCREENSHOT_UPLOAD,
            chat_id=100,
            owner_id="owner",
        )
    # Перематываем часы на 25 часов вперёд — окно 24h.
    clock[0] += timedelta(hours=25)
    suggestions = detector.detect_patterns(min_count=3, window_hours=24)
    assert suggestions == []
    # И in-memory store должен быть очищен, чтобы не таскать мусор.
    assert detector.list_actions() == []


def test_persistent_across_reload(tmp_path: Path) -> None:
    """Действия сохраняются на диск и подхватываются новым detector'ом."""
    clock_a = [datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)]
    detector_a = _make_detector(tmp_path, clock_a)
    for _ in range(3):
        detector_a.record_action(
            ACTION_SCREENSHOT_UPLOAD,
            chat_id=100,
            owner_id="owner",
        )

    # Новый инстанс с тем же путём — должен подхватить записи.
    clock_b = [datetime(2026, 4, 28, 12, 30, tzinfo=timezone.utc)]
    detector_b = _make_detector(tmp_path, clock_b)
    suggestions = detector_b.detect_patterns(min_count=3, window_hours=24)
    assert len(suggestions) == 1
    assert suggestions[0].action_type == "enable_screenshot_ocr"
    assert suggestions[0].evidence["count"] == 3


def test_multi_owner_isolation(tmp_path: Path) -> None:
    """Действия разных owner'ов не суммируются в один Suggestion."""
    clock = [datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)]
    detector = _make_detector(tmp_path, clock)
    # 2 от owner_a и 2 от owner_b — ни у кого не достаём порога 3.
    for _ in range(2):
        detector.record_action(
            ACTION_TIMEZONE_QUERY,
            chat_id=100,
            owner_id="owner_a",
            metadata={"tz": "Europe/Madrid"},
        )
        detector.record_action(
            ACTION_TIMEZONE_QUERY,
            chat_id=200,
            owner_id="owner_b",
            metadata={"tz": "Europe/Madrid"},
        )
    assert detector.detect_patterns(min_count=3, window_hours=24) == []

    # Добавляем третий owner_a — у него теперь паттерн, у b всё ещё нет.
    detector.record_action(
        ACTION_TIMEZONE_QUERY,
        chat_id=100,
        owner_id="owner_a",
        metadata={"tz": "Europe/Madrid"},
    )
    suggestions = detector.detect_patterns(min_count=3, window_hours=24)
    assert len(suggestions) == 1
    assert suggestions[0].evidence["owner_id"] == "owner_a"
    assert suggestions[0].evidence["count"] == 3


def test_news_forward_higher_threshold(tmp_path: Path) -> None:
    """News-форварды требуют 5+, не 3."""
    clock = [datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)]
    detector = _make_detector(tmp_path, clock)
    for _ in range(4):
        detector.record_action(ACTION_NEWS_FORWARD, chat_id=100, owner_id="owner")
    # Даже с min_count=3 модуль навязывает встроенный порог 5 для news.
    assert detector.detect_patterns(min_count=3, window_hours=24) == []
    detector.record_action(ACTION_NEWS_FORWARD, chat_id=100, owner_id="owner")
    suggestions = detector.detect_patterns(min_count=3, window_hours=24)
    assert len(suggestions) == 1
    assert suggestions[0].action_type == "enable_news_autosummary"


def test_calc_query_grouped_by_expression(tmp_path: Path) -> None:
    """Повтор одного и того же calc-выражения → Suggestion с этим expression."""
    clock = [datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)]
    detector = _make_detector(tmp_path, clock)
    for _ in range(3):
        detector.record_action(
            ACTION_CALC_QUERY,
            chat_id=100,
            owner_id="owner",
            metadata={"expression": "100 USD to EUR"},
        )
    # Одиночный отличный запрос не должен срабатывать.
    detector.record_action(
        ACTION_CALC_QUERY,
        chat_id=100,
        owner_id="owner",
        metadata={"expression": "5 + 5"},
    )
    suggestions = detector.detect_patterns(min_count=3, window_hours=24)
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.action_type == "pin_calc_shortcut"
    assert s.evidence["expression"] == "100 USD to EUR"


@pytest.mark.parametrize("bad_action", ["", "   "])
def test_record_ignores_empty_action_type(tmp_path: Path, bad_action: str) -> None:
    clock = [datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)]
    detector = _make_detector(tmp_path, clock)
    detector.record_action(bad_action, chat_id=1, owner_id="owner")
    assert detector.list_actions() == []
