# -*- coding: utf-8 -*-
"""
Тесты расчёта stream-таймаутов в userbot_bridge.

Проверяем:
1) безопасные значения по умолчанию для тяжёлых локальных моделей;
2) корректное применение явных override через config;
3) нижние границы при слишком маленьких значениях.
"""

from __future__ import annotations

import src.userbot_bridge as userbot_bridge_module


def test_resolve_stream_timeouts_defaults_text() -> None:
    first, chunk = userbot_bridge_module._resolve_openclaw_stream_timeouts(has_photo=False)
    assert first >= 420.0
    assert chunk >= 15.0
    assert first >= chunk


def test_resolve_stream_timeouts_defaults_photo() -> None:
    first, chunk = userbot_bridge_module._resolve_openclaw_stream_timeouts(has_photo=True)
    assert first >= 540.0
    assert chunk >= 15.0
    assert first >= chunk


def test_resolve_stream_timeouts_respects_overrides(monkeypatch) -> None:
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "OPENCLAW_CHUNK_TIMEOUT_SEC",
        240.0,
        raising=False,
    )
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "OPENCLAW_FIRST_CHUNK_TIMEOUT_SEC",
        480.0,
        raising=False,
    )
    first, chunk = userbot_bridge_module._resolve_openclaw_stream_timeouts(has_photo=False)
    assert first == 480.0
    assert chunk == 240.0


def test_resolve_stream_timeouts_respects_photo_first_chunk_override(monkeypatch) -> None:
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "OPENCLAW_CHUNK_TIMEOUT_SEC",
        200.0,
        raising=False,
    )
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC",
        600.0,
        raising=False,
    )
    first, chunk = userbot_bridge_module._resolve_openclaw_stream_timeouts(has_photo=True)
    assert first == 600.0
    assert chunk == 200.0


def test_resolve_stream_timeouts_applies_min_bounds(monkeypatch) -> None:
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "OPENCLAW_CHUNK_TIMEOUT_SEC",
        1.0,
        raising=False,
    )
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "OPENCLAW_FIRST_CHUNK_TIMEOUT_SEC",
        5.0,
        raising=False,
    )
    first, chunk = userbot_bridge_module._resolve_openclaw_stream_timeouts(has_photo=False)
    assert chunk == 15.0
    assert first >= 30.0
    assert first >= chunk


def test_resolve_buffered_response_timeout_defaults_text() -> None:
    total = userbot_bridge_module._resolve_openclaw_buffered_response_timeout(
        has_photo=False,
        first_chunk_timeout_sec=420.0,
    )
    assert total >= 660.0
    assert total > 420.0


def test_resolve_buffered_response_timeout_defaults_photo() -> None:
    total = userbot_bridge_module._resolve_openclaw_buffered_response_timeout(
        has_photo=True,
        first_chunk_timeout_sec=540.0,
    )
    assert total >= 780.0
    assert total > 540.0


def test_resolve_progress_notice_schedule_defaults_text() -> None:
    initial, repeat = userbot_bridge_module._resolve_openclaw_progress_notice_schedule(
        has_photo=False,
        first_chunk_timeout_sec=420.0,
    )
    assert initial >= 5.0
    assert initial <= 420.0
    assert repeat >= 15.0


def test_resolve_progress_notice_schedule_respects_overrides(monkeypatch) -> None:
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "OPENCLAW_PROGRESS_NOTICE_INITIAL_SEC",
        12.0,
        raising=False,
    )
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "OPENCLAW_PROGRESS_NOTICE_REPEAT_SEC",
        33.0,
        raising=False,
    )
    initial, repeat = userbot_bridge_module._resolve_openclaw_progress_notice_schedule(
        has_photo=False,
        first_chunk_timeout_sec=420.0,
    )
    assert initial == 12.0
    assert repeat == 33.0


def test_build_openclaw_progress_wait_notice_reflects_current_attempt() -> None:
    notice = userbot_bridge_module._build_openclaw_progress_wait_notice(
        route_model="google-gemini-cli/gemini-3-flash-preview",
        attempt=2,
        elapsed_sec=335.0,
        notice_index=3,
    )
    assert "Текущий маршрут" in notice
    assert "google-gemini-cli/gemini-3-flash-preview" in notice
    assert "попытка `2`" in notice
    assert "fallback активен" in notice
    assert "Стартовый маршрут" not in notice


def test_build_openclaw_progress_wait_notice_mentions_running_tool() -> None:
    notice = userbot_bridge_module._build_openclaw_progress_wait_notice(
        route_model="openai-codex/gpt-5.4",
        attempt=1,
        elapsed_sec=18.0,
        notice_index=1,
        tool_calls_summary="🔧 Выполняется: browser\nИнструментов: 0/1",
    )
    assert "Использую инструмент" in notice
    assert "Выполняется: browser" in notice


def test_build_openclaw_progress_wait_notice_mentions_tool_wrap_up() -> None:
    notice = userbot_bridge_module._build_openclaw_progress_wait_notice(
        route_model="openai-codex/gpt-5.4",
        attempt=1,
        elapsed_sec=22.0,
        notice_index=2,
        tool_calls_summary="✅ Готово: browser\nИнструментов: 1/1",
    )
    assert "собираю итоговый ответ" in notice
    assert "Готово: browser" in notice


def test_build_openclaw_slow_wait_notice_uses_route_line() -> None:
    notice = userbot_bridge_module._build_openclaw_slow_wait_notice(
        route_model="codex-cli/gpt-5.4",
        attempt=2,
    )
    assert "Текущий маршрут" in notice
    assert "codex-cli/gpt-5.4" in notice
    assert "попытка `2`" in notice
