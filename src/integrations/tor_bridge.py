# -*- coding: utf-8 -*-
"""
Tor Bridge — SOCKS5 proxy через локальный Tor daemon.

Предоставляет:
- проверку доступности Tor;
- httpx-совместимый proxy URL для анонимных HTTP-запросов;
- tor_fetch() — одноразовый GET через Tor;
- health_check() — для capability_registry.

Требования:
- Tor daemon запущен локально (`brew install tor && brew services start tor`)
- Порт SOCKS5 на 127.0.0.1:9050 (по умолчанию)

Ограничения:
- Медленно (5-30с на запрос)
- .onion сайты нестабильны
- DNS resolution через Tor (не локально) — для этого нужен socks5h://
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = 30.0
_HEALTH_CHECK_URL = "https://check.torproject.org/api/ip"


def get_tor_proxy_url(socks_port: int = 9050) -> str:
    """Возвращает SOCKS5 proxy URL для httpx (с DNS через Tor)."""
    return f"socks5://127.0.0.1:{socks_port}"


def build_tor_client(
    *,
    socks_port: int = 9050,
    timeout: float = _DEFAULT_TIMEOUT,
) -> httpx.AsyncClient:
    """Создаёт httpx.AsyncClient через Tor SOCKS5 proxy."""
    proxy_url = get_tor_proxy_url(socks_port)
    return httpx.AsyncClient(
        proxy=proxy_url,
        timeout=httpx.Timeout(connect=15.0, read=timeout, write=15.0, pool=15.0),
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0"
        },
    )


async def is_tor_available(socks_port: int = 9050) -> bool:
    """Проверяет, работает ли Tor daemon (пробует подключиться к SOCKS порту)."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", socks_port),
            timeout=3.0,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def tor_fetch(
    url: str,
    *,
    socks_port: int = 9050,
    timeout: float = _DEFAULT_TIMEOUT,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Выполняет HTTP запрос через Tor и возвращает результат.

    Returns:
        {"ok": True, "status": int, "text": str, "url": str, "tor_ip": str}
        или {"ok": False, "error": str}
    """
    if not await is_tor_available(socks_port):
        return {"ok": False, "error": "tor_not_running"}

    try:
        async with build_tor_client(socks_port=socks_port, timeout=timeout) as client:
            resp = await client.request(method, url, headers=headers)
            return {
                "ok": True,
                "status": resp.status_code,
                "text": resp.text[:50_000],
                "url": str(resp.url),
            }
    except Exception as exc:
        logger.warning("tor_fetch_failed", url=url, error=repr(exc))
        return {"ok": False, "error": str(exc)}


async def get_tor_ip(socks_port: int = 9050) -> str | None:
    """Возвращает текущий exit-IP Tor или None если не работает."""
    result = await tor_fetch(_HEALTH_CHECK_URL, socks_port=socks_port, timeout=10.0)
    if result.get("ok") and result.get("status") == 200:
        import json

        try:
            data = json.loads(result["text"])
            return str(data.get("IP", ""))
        except (json.JSONDecodeError, KeyError):
            pass
    return None


async def health_check(socks_port: int = 9050) -> dict[str, Any]:
    """
    Health check для capability_registry._probe_status().

    Returns {"ok": True/False, "ip": str, "error": str}
    """
    if not await is_tor_available(socks_port):
        return {"ok": False, "error": "tor_daemon_not_running"}

    ip = await get_tor_ip(socks_port)
    if ip:
        return {"ok": True, "ip": ip, "error": ""}
    return {"ok": False, "error": "tor_running_but_no_exit_ip"}
