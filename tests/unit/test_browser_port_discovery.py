# -*- coding: utf-8 -*-
"""
Тесты обнаружения CDP-порта в BrowserBridge.

Покрывает:
1. CDP_URL property — читает KRAB_CDP_PORT, дефолт 9222.
2. _read_ws_from_json_version_async — сканирование кандидатов или только env-порт.
3. _read_devtools_ws_endpoint — парсинг файла DevToolsActivePort.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from src.integrations.browser_bridge import BrowserBridge


# ---------------------------------------------------------------------------
# 1. CDP_URL property
# ---------------------------------------------------------------------------


def test_cdp_url_default_9222(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без KRAB_CDP_PORT → порт 9222."""
    monkeypatch.delenv("KRAB_CDP_PORT", raising=False)
    bridge = BrowserBridge()
    assert bridge.CDP_URL == "http://127.0.0.1:9222"


def test_cdp_url_respects_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_CDP_PORT=9223 → порт 9223."""
    monkeypatch.setenv("KRAB_CDP_PORT", "9223")
    bridge = BrowserBridge()
    assert bridge.CDP_URL == "http://127.0.0.1:9223"


def test_cdp_url_env_var_9224(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_CDP_PORT=9224 → порт 9224."""
    monkeypatch.setenv("KRAB_CDP_PORT", "9224")
    bridge = BrowserBridge()
    assert bridge.CDP_URL == "http://127.0.0.1:9224"


# ---------------------------------------------------------------------------
# 2. _read_ws_from_json_version_async — порядок кандидатов
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_ws_only_env_port_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Когда KRAB_CDP_PORT задан, HTTP-запрос делается только к этому порту.
    Порты 9222 и 9224 не должны быть опрошены.
    """
    monkeypatch.setenv("KRAB_CDP_PORT", "9223")
    bridge = BrowserBridge()

    probed: list[str] = []

    def mock_fetch(cdp_http: str) -> str | None:
        probed.append(cdp_http)
        return None  # Chrome не запущен — всегда None

    with patch.object(BrowserBridge, "_fetch_ws_from_json_version_sync", staticmethod(mock_fetch)):
        result = await bridge._read_ws_from_json_version_async()

    assert result is None
    assert probed == ["http://127.0.0.1:9223/json/version"], (
        f"Ожидали опрос только порта 9223, получили: {probed}"
    )


@pytest.mark.asyncio
async def test_read_ws_scans_all_candidates_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Без KRAB_CDP_PORT сканируются все три кандидата: 9222, 9223, 9224.
    Дефолтный порт (9222) идёт первым.
    """
    monkeypatch.delenv("KRAB_CDP_PORT", raising=False)
    bridge = BrowserBridge()

    probed: list[str] = []

    def mock_fetch(cdp_http: str) -> str | None:
        probed.append(cdp_http)
        return None

    with patch.object(BrowserBridge, "_fetch_ws_from_json_version_sync", staticmethod(mock_fetch)):
        result = await bridge._read_ws_from_json_version_async()

    assert result is None
    # Порт 9222 должен быть первым; все три кандидата должны быть опрошены
    assert probed[0] == "http://127.0.0.1:9222/json/version"
    assert "http://127.0.0.1:9223/json/version" in probed
    assert "http://127.0.0.1:9224/json/version" in probed


@pytest.mark.asyncio
async def test_read_ws_returns_first_successful_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Как только один порт возвращает ws://, поиск останавливается.
    Порты после успешного не опрашиваются.
    """
    monkeypatch.delenv("KRAB_CDP_PORT", raising=False)
    bridge = BrowserBridge()

    probed: list[str] = []

    def mock_fetch(cdp_http: str) -> str | None:
        probed.append(cdp_http)
        if ":9223/" in cdp_http:
            return "ws://127.0.0.1:9223/devtools/browser/abc123"
        return None

    with patch.object(BrowserBridge, "_fetch_ws_from_json_version_sync", staticmethod(mock_fetch)):
        result = await bridge._read_ws_from_json_version_async()

    assert result == "ws://127.0.0.1:9223/devtools/browser/abc123"
    # 9222 пробовался и вернул None → 9223 успех → 9224 не должен быть опрошен
    assert "http://127.0.0.1:9224/json/version" not in probed


# ---------------------------------------------------------------------------
# 3. _read_devtools_ws_endpoint — парсинг файла DevToolsActivePort
# ---------------------------------------------------------------------------


def test_read_devtools_ws_parses_valid_file(tmp_path: Path) -> None:
    """
    Файл DevToolsActivePort с корректным содержимым возвращает ws:// endpoint.

    Формат файла Chrome:
    <порт>
    /devtools/browser/<uuid>
    """
    port_file = tmp_path / "DevToolsActivePort"
    port_file.write_text("9223\n/devtools/browser/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n")

    bridge = BrowserBridge()

    # Подменяем список кандидатов файла на наш временный файл
    with patch.object(bridge, "_devtools_active_port_candidates", return_value=[port_file]):
        ws = bridge._read_devtools_ws_endpoint()

    assert ws == "ws://127.0.0.1:9223/devtools/browser/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_read_devtools_ws_skips_missing_file(tmp_path: Path) -> None:
    """Файл не существует → возвращает None, без исключений."""
    missing = tmp_path / "DevToolsActivePort"
    bridge = BrowserBridge()

    with patch.object(bridge, "_devtools_active_port_candidates", return_value=[missing]):
        ws = bridge._read_devtools_ws_endpoint()

    assert ws is None


def test_read_devtools_ws_skips_malformed_file(tmp_path: Path) -> None:
    """Неправильный формат файла → None, без исключений."""
    bad_file = tmp_path / "DevToolsActivePort"
    bad_file.write_text("not-a-port\nsome-random-text\n")

    bridge = BrowserBridge()
    with patch.object(bridge, "_devtools_active_port_candidates", return_value=[bad_file]):
        ws = bridge._read_devtools_ws_endpoint()

    assert ws is None


def test_read_devtools_ws_skips_incomplete_file(tmp_path: Path) -> None:
    """Файл с одной строкой (только порт, без пути) → None."""
    incomplete = tmp_path / "DevToolsActivePort"
    incomplete.write_text("9222\n")

    bridge = BrowserBridge()
    with patch.object(bridge, "_devtools_active_port_candidates", return_value=[incomplete]):
        ws = bridge._read_devtools_ws_endpoint()

    assert ws is None


def test_read_devtools_ws_prefers_first_valid_candidate(tmp_path: Path) -> None:
    """
    При нескольких кандидатах использует первый валидный файл.
    Второй кандидат не читается, если первый дал ws://.
    """
    file_a = tmp_path / "a" / "DevToolsActivePort"
    file_b = tmp_path / "b" / "DevToolsActivePort"
    file_a.parent.mkdir()
    file_b.parent.mkdir()
    file_a.write_text("9222\n/devtools/browser/port-a-uuid\n")
    file_b.write_text("9223\n/devtools/browser/port-b-uuid\n")

    bridge = BrowserBridge()
    with patch.object(bridge, "_devtools_active_port_candidates", return_value=[file_a, file_b]):
        ws = bridge._read_devtools_ws_endpoint()

    assert ws == "ws://127.0.0.1:9222/devtools/browser/port-a-uuid"
