# -*- coding: utf-8 -*-
"""
Тесты для src/integrations/tor_bridge.py и src/integrations/hammerspoon_bridge.py.

Все HTTP-вызовы мокаются — реальные демоны не нужны.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.integrations.hammerspoon_bridge import (
    HammerspoonBridge,
    HammerspoonBridgeError,
)
from src.integrations.tor_bridge import (
    build_tor_client,
    get_tor_proxy_url,
    health_check,
    is_tor_available,
    tor_fetch,
)

# ══════════════════════════════════════════════════════════════
# TorBridge — конфигурация proxy URL
# ══════════════════════════════════════════════════════════════


def test_get_tor_proxy_url_default():
    """Дефолтный порт 9050 формирует корректный socks5 URL."""
    url = get_tor_proxy_url()
    assert url == "socks5://127.0.0.1:9050"


def test_get_tor_proxy_url_custom_port():
    """Кастомный порт отражается в URL."""
    url = get_tor_proxy_url(socks_port=9150)
    assert url == "socks5://127.0.0.1:9150"


def test_build_tor_client_uses_socks5_proxy():
    """build_tor_client передаёт корректный socks5 proxy URL в AsyncClient."""
    with patch("src.integrations.tor_bridge.httpx.AsyncClient") as mock_cls:
        build_tor_client(socks_port=9050)
    call_kwargs = mock_cls.call_args.kwargs
    assert call_kwargs["proxy"] == "socks5://127.0.0.1:9050"
    assert call_kwargs["follow_redirects"] is True


# ══════════════════════════════════════════════════════════════
# TorBridge — is_tor_available
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_is_tor_available_true():
    """Tor доступен: open_connection успешно открывает соединение."""
    mock_writer = MagicMock()
    mock_writer.wait_closed = AsyncMock()

    with patch(
        "src.integrations.tor_bridge.asyncio.open_connection",
        new_callable=AsyncMock,
        return_value=(MagicMock(), mock_writer),
    ):
        result = await is_tor_available()
    assert result is True


@pytest.mark.asyncio
async def test_is_tor_available_false_oserror():
    """Tor недоступен: open_connection бросает OSError."""
    with patch(
        "src.integrations.tor_bridge.asyncio.open_connection",
        side_effect=OSError("Connection refused"),
    ):
        result = await is_tor_available()
    assert result is False


# ══════════════════════════════════════════════════════════════
# TorBridge — tor_fetch
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_tor_fetch_when_tor_not_running():
    """tor_fetch возвращает ошибку, если Tor не запущен."""
    with patch(
        "src.integrations.tor_bridge.is_tor_available",
        new_callable=AsyncMock,
        return_value=False,
    ):
        result = await tor_fetch("https://example.com")

    assert result["ok"] is False
    assert result["error"] == "tor_not_running"


@pytest.mark.asyncio
async def test_tor_fetch_success():
    """tor_fetch возвращает ok=True со статусом и текстом при успехе."""
    # Мок ответа httpx
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = "hello"
    mock_resp.url = "https://example.com"

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.request = AsyncMock(return_value=mock_resp)

    with (
        patch(
            "src.integrations.tor_bridge.is_tor_available",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "src.integrations.tor_bridge.build_tor_client",
            return_value=mock_client,
        ),
    ):
        result = await tor_fetch("https://example.com")

    assert result["ok"] is True
    assert result["status"] == 200
    assert result["text"] == "hello"


@pytest.mark.asyncio
async def test_tor_fetch_http_exception():
    """tor_fetch перехватывает Exception и возвращает ok=False."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.request = AsyncMock(side_effect=Exception("network error"))

    with (
        patch(
            "src.integrations.tor_bridge.is_tor_available",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "src.integrations.tor_bridge.build_tor_client",
            return_value=mock_client,
        ),
    ):
        result = await tor_fetch("https://example.com")

    assert result["ok"] is False
    assert "network error" in result["error"]


# ══════════════════════════════════════════════════════════════
# TorBridge — health_check
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_health_check_tor_not_running():
    """health_check возвращает ok=False если Tor не запущен."""
    with patch(
        "src.integrations.tor_bridge.is_tor_available",
        new_callable=AsyncMock,
        return_value=False,
    ):
        result = await health_check()

    assert result["ok"] is False
    assert result["error"] == "tor_daemon_not_running"


@pytest.mark.asyncio
async def test_health_check_tor_running_with_ip():
    """health_check возвращает ok=True и IP если Tor работает нормально."""
    with (
        patch(
            "src.integrations.tor_bridge.is_tor_available",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "src.integrations.tor_bridge.get_tor_ip",
            new_callable=AsyncMock,
            return_value="1.2.3.4",
        ),
    ):
        result = await health_check()

    assert result["ok"] is True
    assert result["ip"] == "1.2.3.4"
    assert result["error"] == ""


@pytest.mark.asyncio
async def test_health_check_tor_running_no_ip():
    """health_check возвращает ok=False если exit IP не получить."""
    with (
        patch(
            "src.integrations.tor_bridge.is_tor_available",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "src.integrations.tor_bridge.get_tor_ip",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await health_check()

    assert result["ok"] is False
    assert "no_exit_ip" in result["error"]


# ══════════════════════════════════════════════════════════════
# HammerspoonBridge — конфигурация
# ══════════════════════════════════════════════════════════════


def test_hammerspoon_bridge_default_url():
    """Дефолтный base_url — localhost:10101."""
    bridge = HammerspoonBridge()
    assert bridge.base_url == "http://localhost:10101"


def test_hammerspoon_bridge_custom_url_strip_slash():
    """Trailing slash в base_url обрезается."""
    bridge = HammerspoonBridge(base_url="http://localhost:10101/")
    assert bridge.base_url == "http://localhost:10101"


def test_hammerspoon_bridge_pass_key_header():
    """pass_key попадает в заголовок X-Krab-Pass."""
    bridge = HammerspoonBridge(pass_key="secret123")
    assert bridge._headers.get("X-Krab-Pass") == "secret123"


def test_hammerspoon_bridge_no_pass_key_no_header():
    """Без pass_key заголовок X-Krab-Pass не добавляется."""
    bridge = HammerspoonBridge()
    assert "X-Krab-Pass" not in bridge._headers


# ══════════════════════════════════════════════════════════════
# HammerspoonBridge — _parse_response
# ══════════════════════════════════════════════════════════════


def test_parse_response_ok():
    """_parse_response возвращает dict при валидном JSON и 2xx статусе."""
    bridge = HammerspoonBridge()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True}

    result = bridge._parse_response(mock_resp)
    assert result == {"ok": True}


def test_parse_response_http_error_raises():
    """_parse_response бросает HammerspoonBridgeError при статусе >= 400."""
    bridge = HammerspoonBridge()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 404
    mock_resp.json.return_value = {"error": "not_found"}

    with pytest.raises(HammerspoonBridgeError, match="not_found"):
        bridge._parse_response(mock_resp)


def test_parse_response_invalid_json_raises():
    """_parse_response бросает HammerspoonBridgeError при невалидном JSON."""
    bridge = HammerspoonBridge()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.side_effect = ValueError("bad json")
    mock_resp.text = "not json"

    with pytest.raises(HammerspoonBridgeError, match="invalid_json"):
        bridge._parse_response(mock_resp)


# ══════════════════════════════════════════════════════════════
# HammerspoonBridge — публичные методы (мок HTTP)
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_status_returns_dict():
    """status() делает GET /status и возвращает ответ."""
    bridge = HammerspoonBridge()
    with patch.object(
        bridge, "_get", new_callable=AsyncMock, return_value={"version": "1.0", "screens": 2}
    ):
        result = await bridge.status()
    assert result["version"] == "1.0"


@pytest.mark.asyncio
async def test_list_windows_extracts_list():
    """list_windows() возвращает список из поля 'windows'."""
    bridge = HammerspoonBridge()
    windows_data = [{"id": 1, "title": "Terminal", "app": "iTerm2"}]
    with patch.object(
        bridge, "_get", new_callable=AsyncMock, return_value={"windows": windows_data}
    ):
        result = await bridge.list_windows()
    assert result == windows_data


@pytest.mark.asyncio
async def test_focus_sends_correct_payload():
    """focus() передаёт action=focus и имя приложения."""
    bridge = HammerspoonBridge()
    with patch.object(
        bridge, "_post", new_callable=AsyncMock, return_value={"ok": True}
    ) as mock_post:
        await bridge.focus("iTerm2")
    mock_post.assert_called_once_with("/window", {"action": "focus", "app": "iTerm2"})


@pytest.mark.asyncio
async def test_tile_sends_correct_preset():
    """tile() передаёт preset и имя приложения в /window."""
    bridge = HammerspoonBridge()
    with patch.object(
        bridge, "_post", new_callable=AsyncMock, return_value={"ok": True}
    ) as mock_post:
        await bridge.tile(preset="left", app="Safari")
    mock_post.assert_called_once_with(
        "/window", {"action": "tile", "preset": "left", "app": "Safari"}
    )


@pytest.mark.asyncio
async def test_get_transport_error_raises_bridge_error():
    """_get пробрасывает httpx.TransportError как HammerspoonBridgeError."""
    bridge = HammerspoonBridge()

    async def fake_get_context(*args, **kwargs):
        raise httpx.ConnectError("refused")

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with patch("src.integrations.hammerspoon_bridge.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HammerspoonBridgeError, match="connection_failed"):
            await bridge._get("/status")
