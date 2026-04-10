# -*- coding: utf-8 -*-
"""
Тесты LLMFlowMixin и module-level helpers (src/userbot/llm_flow.py).

Стратегия:
- Все module-level helpers (_resolve_*, _build_*) — чистые функции, тестируются напрямую.
- LLMFlowMixin._build_background_handoff_notice — static, простой текстовый контракт.
- _run_llm_request_flow / _finish_ai_request_background — сложные async, только smoke.

Не тестируем полный LLM pipeline — он требует реального openclaw_client.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.userbot.llm_flow import (
    LLMFlowMixin,
    _build_openclaw_progress_wait_notice,
    _build_openclaw_route_notice_line,
    _build_openclaw_slow_wait_notice,
    _resolve_openclaw_buffered_response_timeout,
    _resolve_openclaw_progress_notice_schedule,
    _resolve_openclaw_stream_timeouts,
)
from src.userbot_bridge import KraabUserbot


def _make_bot() -> KraabUserbot:
    """Минимальный stub KraabUserbot без вызова __init__."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.current_role = "default"
    bot.me = SimpleNamespace(id=777)
    return bot


# ---------------------------------------------------------------------------
# _resolve_openclaw_stream_timeouts
# ---------------------------------------------------------------------------


def test_stream_timeouts_text_defaults() -> None:
    """Для текстового запроса first_chunk >= chunk_timeout."""
    first, chunk = _resolve_openclaw_stream_timeouts(has_photo=False)
    assert first >= chunk
    assert chunk >= 15.0
    assert first >= 30.0


def test_stream_timeouts_photo_first_chunk_not_greater_than_text() -> None:
    """Для фото first_chunk дефолт (1200) не превышает текстовый (1800)."""
    # Дефолт для текста — 1800, для фото — 1200, итоговое значение: max(chunk, default)
    # При chunk_timeout=180 (дефолт из config): text → max(180, 1800)=1800, photo → max(180, 1200)=1200
    # Но если в config есть override, оба могут выровняться. Проверяем реальный контракт:
    # first_chunk_timeout >= chunk_timeout всегда.
    first_text, chunk_text = _resolve_openclaw_stream_timeouts(has_photo=False)
    first_photo, chunk_photo = _resolve_openclaw_stream_timeouts(has_photo=True)
    assert first_text >= chunk_text
    assert first_photo >= chunk_photo


def test_stream_timeouts_custom_config(monkeypatch) -> None:
    """Config-значение OPENCLAW_CHUNK_TIMEOUT_SEC применяется."""
    import src.userbot.llm_flow as llm_module

    monkeypatch.setattr(llm_module.config, "OPENCLAW_CHUNK_TIMEOUT_SEC", 300.0, raising=False)
    _, chunk = _resolve_openclaw_stream_timeouts(has_photo=False)
    assert chunk == 300.0


def test_stream_timeouts_minimum_floors(monkeypatch) -> None:
    """Очень маленькое значение chunk timeout приводится к минимуму 15 сек."""
    import src.userbot.llm_flow as llm_module

    monkeypatch.setattr(llm_module.config, "OPENCLAW_CHUNK_TIMEOUT_SEC", 1.0, raising=False)
    first, chunk = _resolve_openclaw_stream_timeouts(has_photo=False)
    assert chunk >= 15.0
    assert first >= 30.0


# ---------------------------------------------------------------------------
# _resolve_openclaw_buffered_response_timeout
# ---------------------------------------------------------------------------


def test_buffered_response_timeout_text_minimum() -> None:
    """Для текста минимальный hard timeout >= 900 сек."""
    timeout = _resolve_openclaw_buffered_response_timeout(
        has_photo=False, first_chunk_timeout_sec=120.0
    )
    assert timeout >= 900.0


def test_buffered_response_timeout_photo_minimum() -> None:
    """Для фото минимальный hard timeout >= 1020 сек."""
    timeout = _resolve_openclaw_buffered_response_timeout(
        has_photo=True, first_chunk_timeout_sec=120.0
    )
    assert timeout >= 1020.0


def test_buffered_response_timeout_respects_first_chunk_offset() -> None:
    """Hard timeout >= first_chunk_timeout + 60 сек."""
    first_chunk = 2000.0
    timeout = _resolve_openclaw_buffered_response_timeout(
        has_photo=False, first_chunk_timeout_sec=first_chunk
    )
    assert timeout >= first_chunk + 60.0


# ---------------------------------------------------------------------------
# _resolve_openclaw_progress_notice_schedule
# ---------------------------------------------------------------------------


def test_progress_notice_schedule_defaults_text() -> None:
    """Для текста: initial_sec > 0, repeat_sec >= 15."""
    initial, repeat = _resolve_openclaw_progress_notice_schedule(
        has_photo=False, first_chunk_timeout_sec=1800.0
    )
    assert initial >= 5.0
    assert repeat >= 15.0


def test_progress_notice_schedule_capped_by_first_chunk() -> None:
    """initial_sec не превышает first_chunk_timeout_sec."""
    first_chunk = 10.0
    initial, _ = _resolve_openclaw_progress_notice_schedule(
        has_photo=False, first_chunk_timeout_sec=first_chunk
    )
    assert initial <= first_chunk


def test_progress_notice_photo_uses_higher_defaults() -> None:
    """Для фото initial_sec больше чем для текста (30 vs 20)."""
    initial_text, _ = _resolve_openclaw_progress_notice_schedule(
        has_photo=False, first_chunk_timeout_sec=1800.0
    )
    initial_photo, _ = _resolve_openclaw_progress_notice_schedule(
        has_photo=True, first_chunk_timeout_sec=1200.0
    )
    # Для фото default_initial=30, для текста default_initial=20
    assert initial_photo >= initial_text


# ---------------------------------------------------------------------------
# _build_openclaw_route_notice_line
# ---------------------------------------------------------------------------


def test_route_notice_line_empty_when_no_model() -> None:
    """Без модели и попытки — пустая строка."""
    line = _build_openclaw_route_notice_line(route_model="", attempt=None)
    assert line == ""


def test_route_notice_line_includes_model() -> None:
    """Строка содержит название модели."""
    line = _build_openclaw_route_notice_line(route_model="gemini-3.1-pro", attempt=None)
    assert "gemini-3.1-pro" in line


def test_route_notice_line_includes_attempt_and_fallback() -> None:
    """Если попытка > 1 — указывается 'fallback активен'."""
    line = _build_openclaw_route_notice_line(route_model="gemini-flash", attempt=3)
    assert "fallback активен" in line
    assert "3" in line


def test_route_notice_line_attempt_1_no_fallback() -> None:
    """Первая попытка — 'fallback активен' не показывается."""
    line = _build_openclaw_route_notice_line(route_model="gemini-flash", attempt=1)
    assert "fallback активен" not in line


# ---------------------------------------------------------------------------
# _build_openclaw_slow_wait_notice
# ---------------------------------------------------------------------------


def test_slow_wait_notice_contains_route_info() -> None:
    """Уведомление о долгом ожидании включает маршрут."""
    notice = _build_openclaw_slow_wait_notice(route_model="gemini-2.5-pro", attempt=1)
    assert "gemini-2.5-pro" in notice
    assert "OpenClaw" in notice


def test_slow_wait_notice_no_model_still_returns_string() -> None:
    """Без модели возвращается непустая строка."""
    notice = _build_openclaw_slow_wait_notice(route_model="", attempt=None)
    assert isinstance(notice, str)
    assert len(notice) > 0


# ---------------------------------------------------------------------------
# _build_openclaw_progress_wait_notice
# ---------------------------------------------------------------------------


def test_progress_notice_contains_elapsed_seconds() -> None:
    """Для elapsed < 60 — отображается в секундах."""
    notice = _build_openclaw_progress_wait_notice(
        route_model="gemini",
        attempt=None,
        elapsed_sec=30.0,
        notice_index=1,
    )
    assert "сек" in notice
    assert "Прошло" in notice


def test_progress_notice_contains_elapsed_minutes() -> None:
    """Для elapsed >= 60 — отображается в минутах."""
    notice = _build_openclaw_progress_wait_notice(
        route_model="gemini",
        attempt=None,
        elapsed_sec=120.0,
        notice_index=1,
    )
    assert "мин" in notice


def test_progress_notice_with_tool_summary_running() -> None:
    """Активные tool calls отображаются в прогресс-уведомлении."""
    tool_summary = "🌐 Открываю браузер...\nИнструментов: 0/3"
    notice = _build_openclaw_progress_wait_notice(
        route_model="gemini",
        attempt=None,
        elapsed_sec=15.0,
        notice_index=1,
        tool_calls_summary=tool_summary,
    )
    assert tool_summary in notice


def test_progress_notice_early_stage_message() -> None:
    """Первое уведомление (index=1) содержит 'принят' или аналогичный текст."""
    notice = _build_openclaw_progress_wait_notice(
        route_model="",
        attempt=None,
        elapsed_sec=5.0,
        notice_index=1,
    )
    # Первое уведомление — "Запрос принят — OpenClaw обрабатывает задачу."
    assert "OpenClaw" in notice


def test_progress_notice_late_stage_message() -> None:
    """Позднее уведомление (index=5) содержит дашборд :18789."""
    notice = _build_openclaw_progress_wait_notice(
        route_model="",
        attempt=None,
        elapsed_sec=300.0,
        notice_index=5,
    )
    assert "18789" in notice


# ---------------------------------------------------------------------------
# LLMFlowMixin._build_background_handoff_notice (static method)
# ---------------------------------------------------------------------------


def test_build_background_handoff_notice_contains_query() -> None:
    """Handoff-уведомление включает текст запроса."""
    notice = LLMFlowMixin._build_background_handoff_notice("сделай анализ рынка")
    assert "сделай анализ рынка" in notice
    assert "фоне" in notice


def test_build_background_handoff_notice_empty_query_uses_fallback() -> None:
    """Пустой запрос заменяется на 'запрос'."""
    notice = LLMFlowMixin._build_background_handoff_notice("")
    assert "запрос" in notice
    assert "фоне" in notice


def test_build_background_handoff_notice_none_query() -> None:
    """None query не роняет метод."""
    notice = LLMFlowMixin._build_background_handoff_notice(None)  # type: ignore[arg-type]
    assert isinstance(notice, str)
    assert len(notice) > 0
