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
