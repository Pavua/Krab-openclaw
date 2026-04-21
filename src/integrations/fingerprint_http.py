"""Optional HTTP client with Chrome TLS fingerprint (JA3) via curl_cffi.

Fallback на httpx если curl_cffi не установлен. Используется в web_search
fallback когда Brave API ratelimited и нужно прокси через public URL.

Public:
- fetch(url, *, method="GET", headers=None, proxies=None, timeout=15) -> dict
  Returns: {"ok": bool, "status_code": int, "text": str, "headers": dict, "error": str | None}
"""

from __future__ import annotations

from typing import Any

try:
    from curl_cffi import requests as _cffi_requests  # type: ignore[import-not-found]

    _CURL_CFFI_AVAILABLE = True
except ImportError:
    _cffi_requests = None
    _CURL_CFFI_AVAILABLE = False


def is_available() -> bool:
    """Возвращает True если curl_cffi установлен и доступен."""
    return _CURL_CFFI_AVAILABLE


def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    proxies: dict[str, str] | None = None,
    timeout: int = 15,
    impersonate: str = "chrome120",
) -> dict[str, Any]:
    """Выполнить HTTP-запрос с Chrome TLS fingerprint (JA3).

    Использует curl_cffi если доступен, иначе httpx как fallback.

    Args:
        url: Целевой URL.
        method: HTTP-метод (GET, POST и т.д.).
        headers: Дополнительные заголовки.
        proxies: Прокси-словарь ({"http": "...", "https": "..."}).
        timeout: Таймаут в секундах.
        impersonate: Браузерный профиль для curl_cffi (только при curl_cffi).

    Returns:
        {"ok": bool, "status_code": int, "text": str, "headers": dict, "error": str | None}
    """
    if _CURL_CFFI_AVAILABLE:
        return _fetch_cffi(
            url,
            method=method,
            headers=headers,
            proxies=proxies,
            timeout=timeout,
            impersonate=impersonate,
        )
    return _fetch_httpx(url, method=method, headers=headers, proxies=proxies, timeout=timeout)


def _fetch_cffi(
    url: str,
    *,
    method: str,
    headers: dict[str, str] | None,
    proxies: dict[str, str] | None,
    timeout: int,
    impersonate: str,
) -> dict[str, Any]:
    """Fetch через curl_cffi с JA3 импersonation."""
    try:
        kwargs: dict[str, Any] = {
            "headers": headers or {},
            "timeout": timeout,
            "impersonate": impersonate,
        }
        if proxies:
            kwargs["proxies"] = proxies

        resp = _cffi_requests.request(method.upper(), url, **kwargs)
        return {
            "ok": resp.status_code < 400,
            "status_code": resp.status_code,
            "text": resp.text,
            "headers": dict(resp.headers),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status_code": 0,
            "text": "",
            "headers": {},
            "error": str(exc),
        }


def _fetch_httpx(
    url: str,
    *,
    method: str,
    headers: dict[str, str] | None,
    proxies: dict[str, str] | None,
    timeout: int,
) -> dict[str, Any]:
    """Fallback fetch через httpx (без JA3 impersonation)."""
    try:
        import httpx  # noqa: PLC0415

        proxy_url: str | None = None
        if proxies:
            proxy_url = proxies.get("https") or proxies.get("http")

        client_kwargs: dict[str, Any] = {}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url

        with httpx.Client(timeout=timeout, **client_kwargs) as client:
            resp = client.request(method.upper(), url, headers=headers or {})
            return {
                "ok": resp.status_code < 400,
                "status_code": resp.status_code,
                "text": resp.text,
                "headers": dict(resp.headers),
                "error": None,
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status_code": 0,
            "text": "",
            "headers": {},
            "error": str(exc),
        }
