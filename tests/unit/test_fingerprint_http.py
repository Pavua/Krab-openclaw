"""Tests for src/integrations/fingerprint_http.py."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

import src.integrations.fingerprint_http as fh

# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------

def test_is_available_returns_bool():
    result = fh.is_available()
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# fetch via curl_cffi (monkeypatched)
# ---------------------------------------------------------------------------

def _make_cffi_response(status_code: int = 200, text: str = "ok", headers: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = headers or {"content-type": "text/plain"}
    return resp


def test_fetch_uses_cffi_when_available(monkeypatch):
    """fetch() delegирует в curl_cffi.requests.request при _CURL_CFFI_AVAILABLE=True."""
    mock_requests = MagicMock()
    mock_requests.request.return_value = _make_cffi_response(200, "hello")

    monkeypatch.setattr(fh, "_CURL_CFFI_AVAILABLE", True)
    monkeypatch.setattr(fh, "_cffi_requests", mock_requests)

    result = fh.fetch("https://example.com", headers={"X-Test": "1"})

    mock_requests.request.assert_called_once()
    call_kwargs = mock_requests.request.call_args
    assert call_kwargs.args[0] == "GET"
    assert call_kwargs.args[1] == "https://example.com"
    assert call_kwargs.kwargs.get("impersonate") == "chrome120"

    assert result["ok"] is True
    assert result["status_code"] == 200
    assert result["text"] == "hello"
    assert result["error"] is None
    assert isinstance(result["headers"], dict)


def test_fetch_cffi_4xx_returns_ok_false(monkeypatch):
    mock_requests = MagicMock()
    mock_requests.request.return_value = _make_cffi_response(403, "forbidden")

    monkeypatch.setattr(fh, "_CURL_CFFI_AVAILABLE", True)
    monkeypatch.setattr(fh, "_cffi_requests", mock_requests)

    result = fh.fetch("https://example.com/blocked")
    assert result["ok"] is False
    assert result["status_code"] == 403


def test_fetch_cffi_custom_impersonate(monkeypatch):
    mock_requests = MagicMock()
    mock_requests.request.return_value = _make_cffi_response()

    monkeypatch.setattr(fh, "_CURL_CFFI_AVAILABLE", True)
    monkeypatch.setattr(fh, "_cffi_requests", mock_requests)

    fh.fetch("https://example.com", impersonate="chrome110")
    assert mock_requests.request.call_args.kwargs["impersonate"] == "chrome110"


def test_fetch_cffi_passes_proxies(monkeypatch):
    mock_requests = MagicMock()
    mock_requests.request.return_value = _make_cffi_response()

    monkeypatch.setattr(fh, "_CURL_CFFI_AVAILABLE", True)
    monkeypatch.setattr(fh, "_cffi_requests", mock_requests)

    fh.fetch("https://example.com", proxies={"https": "socks5://127.0.0.1:9050"})
    assert mock_requests.request.call_args.kwargs["proxies"] == {"https": "socks5://127.0.0.1:9050"}


def test_fetch_cffi_error_handling(monkeypatch):
    mock_requests = MagicMock()
    mock_requests.request.side_effect = ConnectionError("network unreachable")

    monkeypatch.setattr(fh, "_CURL_CFFI_AVAILABLE", True)
    monkeypatch.setattr(fh, "_cffi_requests", mock_requests)

    result = fh.fetch("https://example.com")
    assert result["ok"] is False
    assert result["status_code"] == 0
    assert "network unreachable" in result["error"]
    assert result["text"] == ""


# ---------------------------------------------------------------------------
# fetch fallback via httpx
# ---------------------------------------------------------------------------

def test_fetch_falls_back_to_httpx_when_cffi_missing(monkeypatch):
    """Когда curl_cffi недоступен, fetch() использует httpx."""
    monkeypatch.setattr(fh, "_CURL_CFFI_AVAILABLE", False)
    monkeypatch.setattr(fh, "_cffi_requests", None)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "httpx-response"
    mock_resp.headers = {"server": "nginx"}

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.request.return_value = mock_resp

    mock_httpx = MagicMock()
    mock_httpx.Client.return_value = mock_client

    with patch.dict(sys.modules, {"httpx": mock_httpx}):
        result = fh.fetch("https://example.com")

    assert result["ok"] is True
    assert result["status_code"] == 200
    assert result["text"] == "httpx-response"
    assert result["error"] is None


def test_fetch_httpx_4xx_ok_false(monkeypatch):
    monkeypatch.setattr(fh, "_CURL_CFFI_AVAILABLE", False)
    monkeypatch.setattr(fh, "_cffi_requests", None)

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "rate limited"
    mock_resp.headers = {}

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.request.return_value = mock_resp

    mock_httpx = MagicMock()
    mock_httpx.Client.return_value = mock_client

    with patch.dict(sys.modules, {"httpx": mock_httpx}):
        result = fh.fetch("https://example.com")

    assert result["ok"] is False
    assert result["status_code"] == 429


def test_fetch_httpx_error_handling(monkeypatch):
    monkeypatch.setattr(fh, "_CURL_CFFI_AVAILABLE", False)
    monkeypatch.setattr(fh, "_cffi_requests", None)

    mock_httpx = MagicMock()
    mock_httpx.Client.side_effect = RuntimeError("httpx init failed")

    with patch.dict(sys.modules, {"httpx": mock_httpx}):
        result = fh.fetch("https://example.com")

    assert result["ok"] is False
    assert result["status_code"] == 0
    assert "httpx init failed" in result["error"]


def test_fetch_httpx_proxy_selection(monkeypatch):
    """httpx fallback выбирает https прокси из словаря."""
    monkeypatch.setattr(fh, "_CURL_CFFI_AVAILABLE", False)
    monkeypatch.setattr(fh, "_cffi_requests", None)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ""
    mock_resp.headers = {}

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.request.return_value = mock_resp

    mock_httpx = MagicMock()
    mock_httpx.Client.return_value = mock_client

    with patch.dict(sys.modules, {"httpx": mock_httpx}):
        fh.fetch("https://example.com", proxies={"https": "http://proxy:8080"})

    mock_httpx.Client.assert_called_once()
    client_kwargs = mock_httpx.Client.call_args.kwargs
    assert client_kwargs.get("proxy") == "http://proxy:8080"


# ---------------------------------------------------------------------------
# Return shape invariants
# ---------------------------------------------------------------------------

def test_return_shape_cffi(monkeypatch):
    mock_requests = MagicMock()
    mock_requests.request.return_value = _make_cffi_response()

    monkeypatch.setattr(fh, "_CURL_CFFI_AVAILABLE", True)
    monkeypatch.setattr(fh, "_cffi_requests", mock_requests)

    result = fh.fetch("https://example.com")
    assert set(result.keys()) == {"ok", "status_code", "text", "headers", "error"}


def test_return_shape_httpx(monkeypatch):
    monkeypatch.setattr(fh, "_CURL_CFFI_AVAILABLE", False)
    monkeypatch.setattr(fh, "_cffi_requests", None)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ""
    mock_resp.headers = {}

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.request.return_value = mock_resp

    mock_httpx = MagicMock()
    mock_httpx.Client.return_value = mock_client

    with patch.dict(sys.modules, {"httpx": mock_httpx}):
        result = fh.fetch("https://example.com")

    assert set(result.keys()) == {"ok", "status_code", "text", "headers", "error"}
