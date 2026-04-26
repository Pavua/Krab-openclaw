# -*- coding: utf-8 -*-
"""
Unit-тесты Session 24 для health_deep_collector helper-функций.

Покрывает 4 новые секции /api/health/deep:
- _collect_sentry — Sentry SDK initialization status
- _collect_mcp_servers — параллельный TCP probe MCP серверов
- _collect_cf_tunnel — Cloudflare quick tunnel state
- _collect_error_rate — sliding window count из error_handler ring buffer

Все функции defensive: provoke exception → возвращают error-info dict вместо
exception. Это контракт — health endpoint никогда не падает целиком из-за
одной сломанной секции.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ── _collect_sentry ─────────────────────────────────────────────────────────


def test_sentry_dsn_set_and_initialized(monkeypatch):
    """SENTRY_DSN задан + sentry_sdk.is_initialized()=True → initialized=True."""
    from src.core.health_deep_collector import _collect_sentry

    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/123")
    with patch("sentry_sdk.is_initialized", return_value=True, create=True):
        result = _collect_sentry()
    assert result["initialized"] is True
    assert result["dsn_configured"] is True
    assert "error" not in result


def test_sentry_dsn_unset_returns_false(monkeypatch):
    """SENTRY_DSN не задан → dsn_configured=False, initialized всё равно проверяется."""
    from src.core.health_deep_collector import _collect_sentry

    monkeypatch.delenv("SENTRY_DSN", raising=False)
    result = _collect_sentry()
    assert result["dsn_configured"] is False
    assert "initialized" in result


def test_sentry_is_initialized_false_returns_false(monkeypatch):
    """sentry_sdk.is_initialized()=False → initialized=False (даже при заданном DSN)."""
    from src.core.health_deep_collector import _collect_sentry

    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/123")
    with patch("sentry_sdk.is_initialized", return_value=False, create=True):
        result = _collect_sentry()
    assert result["initialized"] is False
    assert result["dsn_configured"] is True


def test_sentry_returns_keys_even_on_internal_error(monkeypatch):
    """is_initialized() throws unexpected → defensive: error key + initialized=False."""
    from src.core.health_deep_collector import _collect_sentry

    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/123")
    # Patch ОБА attribute lookups так чтобы оба пути упали на RuntimeError
    with patch("sentry_sdk.is_initialized", side_effect=RuntimeError("boom"), create=True):
        result = _collect_sentry()
    # Defensive contract: error key есть, initialized=False
    assert "initialized" in result
    assert result["initialized"] is False
    assert "error" in result


# ── _probe_tcp_port + _collect_mcp_servers ──────────────────────────────────


@pytest.mark.asyncio
async def test_probe_tcp_port_unreachable_returns_ok_false():
    """Закрытый порт → ok=False с понятной ошибкой."""
    from src.core.health_deep_collector import _probe_tcp_port

    # 1 — это reserved port, 99.99% не слушается на macOS
    result = await _probe_tcp_port(port=1, timeout=0.5)
    assert result["port"] == 1
    assert result["ok"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_collect_mcp_servers_returns_three_keys():
    """Всегда возвращает yung-nagato + p0lrd + hammerspoon ключи."""
    from src.core.health_deep_collector import _collect_mcp_servers

    result = await _collect_mcp_servers()
    assert set(result.keys()) == {"yung-nagato", "p0lrd", "hammerspoon"}
    for name, data in result.items():
        assert "port" in data
        assert "ok" in data
        assert isinstance(data["ok"], bool)


@pytest.mark.asyncio
async def test_collect_mcp_servers_ports_correct():
    """Порты соответствуют Krab infrastructure: 8011/8012/8013."""
    from src.core.health_deep_collector import _collect_mcp_servers

    result = await _collect_mcp_servers()
    assert result["yung-nagato"]["port"] == 8011
    assert result["p0lrd"]["port"] == 8012
    assert result["hammerspoon"]["port"] == 8013


# ── _collect_cf_tunnel ──────────────────────────────────────────────────────


def test_cf_tunnel_reads_state_files(tmp_path, monkeypatch):
    """Читает /tmp/krab_cf_tunnel/{last_url,fail_count}."""
    from src.core import health_deep_collector

    # Создаём fake state dir и подменяем Path в module
    state = tmp_path / "krab_cf_tunnel"
    state.mkdir()
    (state / "last_url").write_text("https://test.trycloudflare.com\n")
    (state / "fail_count").write_text("3\n")

    # Подменяем Path("/tmp/krab_cf_tunnel") внутри функции через patch
    original_path = Path

    def _fake_path(arg):
        if str(arg) == "/tmp/krab_cf_tunnel":
            return state
        return original_path(arg)

    with patch("src.core.health_deep_collector.Path", side_effect=_fake_path):
        # subprocess.run для launchctl — мокаем как successful (loaded=True)
        with patch("src.core.health_deep_collector.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = health_deep_collector._collect_cf_tunnel()

    assert result["label"] == "ai.krab.cloudflared-tunnel"
    assert result["loaded"] is True
    assert result["last_url"] == "https://test.trycloudflare.com"
    assert result["fail_count"] == 3


def test_cf_tunnel_missing_state_files_graceful(tmp_path):
    """Отсутствие state файлов → last_url=None, fail_count=None, не падает."""
    from src.core import health_deep_collector

    empty_state = tmp_path / "empty"
    empty_state.mkdir()
    original_path = Path

    def _fake_path(arg):
        if str(arg) == "/tmp/krab_cf_tunnel":
            return empty_state
        return original_path(arg)

    with patch("src.core.health_deep_collector.Path", side_effect=_fake_path):
        with patch("src.core.health_deep_collector.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1  # not loaded
            result = health_deep_collector._collect_cf_tunnel()

    assert result["loaded"] is False
    assert result["last_url"] is None
    assert result["fail_count"] is None


def test_cf_tunnel_invalid_fail_count_returns_minus_one(tmp_path):
    """Невалидное fail_count (нечисло) → fail_count=-1."""
    from src.core import health_deep_collector

    state = tmp_path / "krab_cf_tunnel"
    state.mkdir()
    (state / "fail_count").write_text("not-a-number")

    original_path = Path

    def _fake_path(arg):
        if str(arg) == "/tmp/krab_cf_tunnel":
            return state
        return original_path(arg)

    with patch("src.core.health_deep_collector.Path", side_effect=_fake_path):
        with patch("src.core.health_deep_collector.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = health_deep_collector._collect_cf_tunnel()

    assert result["fail_count"] == -1


# ── _collect_error_rate + recent_error_count ───────────────────────────────


def test_error_rate_empty_buffer():
    """Пустой ring buffer → errors_5m=0."""
    from src.core import error_handler
    from src.core.health_deep_collector import _collect_error_rate

    error_handler._RECENT_ERROR_TS.clear()
    result = _collect_error_rate(window_sec=300)
    assert result["errors_5m"] == 0
    assert result["window_sec"] == 300


def test_error_rate_counts_only_inside_window():
    """Только timestamps внутри окна считаются."""
    from src.core import error_handler
    from src.core.health_deep_collector import _collect_error_rate

    error_handler._RECENT_ERROR_TS.clear()
    now = time.time()
    # 3 в окне (последние 300 sec) + 2 старых
    error_handler._RECENT_ERROR_TS.append(now - 100)
    error_handler._RECENT_ERROR_TS.append(now - 200)
    error_handler._RECENT_ERROR_TS.append(now - 50)
    error_handler._RECENT_ERROR_TS.append(now - 400)  # вне окна
    error_handler._RECENT_ERROR_TS.append(now - 500)  # вне окна

    result = _collect_error_rate(window_sec=300)
    assert result["errors_5m"] == 3


def test_recent_error_count_window_zero_returns_zero():
    """window_sec=0 — degenerate case, безопасно возвращает 0."""
    from src.core import error_handler
    from src.core.error_handler import recent_error_count

    error_handler._RECENT_ERROR_TS.clear()
    error_handler._RECENT_ERROR_TS.append(time.time())
    assert recent_error_count(window_sec=0) == 0
    assert recent_error_count(window_sec=-5) == 0


def test_recent_error_count_custom_window():
    """Кастомное окно (например 60 sec) считает корректно."""
    from src.core import error_handler
    from src.core.error_handler import recent_error_count

    error_handler._RECENT_ERROR_TS.clear()
    now = time.time()
    error_handler._RECENT_ERROR_TS.append(now - 30)  # внутри 60s
    error_handler._RECENT_ERROR_TS.append(now - 90)  # вне 60s
    error_handler._RECENT_ERROR_TS.append(now - 200)  # вне 60s

    assert recent_error_count(window_sec=60) == 1
    assert recent_error_count(window_sec=120) == 2
    assert recent_error_count(window_sec=300) == 3
