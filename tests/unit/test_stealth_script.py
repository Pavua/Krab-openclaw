# tests/unit/test_stealth_script.py
"""Проверяет stealth_init.js и интеграцию с mercadona.py."""

from pathlib import Path

import pytest

_STEALTH_JS = Path(__file__).parent.parent.parent / "src" / "integrations" / "stealth_init.js"


def test_stealth_js_file_exists():
    """Файл stealth_init.js должен существовать."""
    assert _STEALTH_JS.exists(), f"Файл не найден: {_STEALTH_JS}"


def test_stealth_js_not_empty():
    """Файл не должен быть пустым."""
    content = _STEALTH_JS.read_text(encoding="utf-8")
    assert len(content) > 100, "stealth_init.js слишком короткий"


def test_stealth_js_contains_webdriver_patch():
    """Должен патчить navigator.webdriver."""
    content = _STEALTH_JS.read_text(encoding="utf-8")
    assert "webdriver" in content


def test_stealth_js_contains_canvas_patch():
    """Должен содержать canvas fingerprint noise."""
    content = _STEALTH_JS.read_text(encoding="utf-8")
    assert "toDataURL" in content
    assert "HTMLCanvasElement" in content


def test_stealth_js_contains_webgl_patch():
    """Должен содержать WebGL vendor/renderer spoof."""
    content = _STEALTH_JS.read_text(encoding="utf-8")
    assert "37445" in content  # UNMASKED_VENDOR_WEBGL
    assert "37446" in content  # UNMASKED_RENDERER_WEBGL
    assert "Intel" in content


def test_stealth_js_contains_webrtc_patch():
    """Должен содержать WebRTC IP-leak block."""
    content = _STEALTH_JS.read_text(encoding="utf-8")
    assert "RTCPeerConnection" in content
    assert "createOffer" in content


def test_stealth_js_contains_permissions_patch():
    """Должен патчить permissions.query."""
    content = _STEALTH_JS.read_text(encoding="utf-8")
    assert "permissions" in content
    assert "notifications" in content


def test_stealth_js_under_120_lines():
    """JS-бандл должен оставаться компактным (<120 строк)."""
    lines = _STEALTH_JS.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 120, f"stealth_init.js слишком длинный: {len(lines)} строк"


def test_mercadona_loads_stealth_from_file():
    """mercadona._STEALTH_SCRIPT должен содержать canvas и WebGL патчи из JS-файла."""
    from src.skills.mercadona import _STEALTH_SCRIPT  # noqa: PLC0415

    assert "toDataURL" in _STEALTH_SCRIPT, "Canvas patch не загружен из stealth_init.js"
    assert "37445" in _STEALTH_SCRIPT, "WebGL patch не загружен из stealth_init.js"


def test_mercadona_stealth_script_is_not_empty():
    """_STEALTH_SCRIPT не должен быть пустым даже при fallback."""
    from src.skills.mercadona import _STEALTH_SCRIPT  # noqa: PLC0415

    assert len(_STEALTH_SCRIPT.strip()) > 20
