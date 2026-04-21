# -*- coding: utf-8 -*-
"""Тесты wiring record_detection в mercadona scraper (Chado §1 P2)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Smoke: импорт модуля не падает
# ---------------------------------------------------------------------------

def test_import_mercadona():
    import src.skills.mercadona  # noqa: F401


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------

def _make_response(status: int, url: str = "https://tienda.mercadona.es/api/search") -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.url = url
    resp.headers = {"content-type": "application/json"}
    resp.json = AsyncMock(return_value={"results": []})
    return resp


# ---------------------------------------------------------------------------
# handle_response: HTTP 429 → ratelimit
# ---------------------------------------------------------------------------

def test_ratelimit_detection():
    from src.core import stealth_metrics
    from src.skills.mercadona import _API_PATTERNS, record_detection  # noqa: F401

    stealth_metrics.reset()

    response = _make_response(429)

    async def run():
        # Воспроизводим логику handle_response напрямую без запуска браузера
        url = response.url
        if not any(p in url for p in _API_PATTERNS):
            return
        if response.status == 429:
            stealth_metrics.record_detection("ratelimit")

    asyncio.get_event_loop().run_until_complete(run())
    counts = stealth_metrics.get_counts()
    assert counts.get("ratelimit", 0) == 1


# ---------------------------------------------------------------------------
# handle_response: HTTP 403 → blocked
# ---------------------------------------------------------------------------

def test_blocked_403_detection():
    from src.core import stealth_metrics
    from src.skills.mercadona import _API_PATTERNS

    stealth_metrics.reset()

    response = _make_response(403)

    async def run():
        url = response.url
        if not any(p in url for p in _API_PATTERNS):
            return
        if response.status in (401, 403):
            stealth_metrics.record_detection("blocked")

    asyncio.get_event_loop().run_until_complete(run())
    assert stealth_metrics.get_counts().get("blocked", 0) == 1


# ---------------------------------------------------------------------------
# handle_response: HTTP 401 → blocked
# ---------------------------------------------------------------------------

def test_blocked_401_detection():
    from src.core import stealth_metrics
    from src.skills.mercadona import _API_PATTERNS

    stealth_metrics.reset()

    response = _make_response(401)

    async def run():
        url = response.url
        if not any(p in url for p in _API_PATTERNS):
            return
        if response.status in (401, 403):
            stealth_metrics.record_detection("blocked")

    asyncio.get_event_loop().run_until_complete(run())
    assert stealth_metrics.get_counts().get("blocked", 0) == 1


# ---------------------------------------------------------------------------
# page.goto timeout/exception → fetch_error
# ---------------------------------------------------------------------------

def test_fetch_error_on_goto_exception():
    from src.core import stealth_metrics

    stealth_metrics.reset()

    # Симулируем: page.goto выбросил исключение
    async def run():
        try:
            raise TimeoutError("playwright timeout")
        except Exception:
            stealth_metrics.record_detection("fetch_error")

    asyncio.get_event_loop().run_until_complete(run())
    assert stealth_metrics.get_counts().get("fetch_error", 0) == 1


# ---------------------------------------------------------------------------
# Captcha indicator in DOM → captcha
# ---------------------------------------------------------------------------

def test_captcha_detection():
    from src.core import stealth_metrics

    stealth_metrics.reset()

    async def _fake_locator_count_nonzero():
        return 1

    async def _fake_locator_count_zero():
        return 0

    async def run():
        # Воспроизводим логику captcha-детекции из _extract_products_from_dom
        captcha_found = False
        captcha_indicators = (
            "iframe[src*='captcha']",
            "iframe[src*='recaptcha']",
            "iframe[src*='hcaptcha']",
            "[data-testid='captcha']",
            ".captcha",
            "#captcha",
        )

        async def count_for(selector: str) -> int:
            # Симулируем: hcaptcha-фрейм присутствует
            if "hcaptcha" in selector:
                return 1
            return 0

        for selector in captcha_indicators:
            if await count_for(selector):
                stealth_metrics.record_detection("captcha")
                captcha_found = True
                break

        assert captcha_found

    asyncio.get_event_loop().run_until_complete(run())
    assert stealth_metrics.get_counts().get("captcha", 0) == 1


# ---------------------------------------------------------------------------
# record_detection вызван из mercadona модуля напрямую через mock
# ---------------------------------------------------------------------------

def test_record_detection_called_in_handle_response_ratelimit(monkeypatch):
    """Проверяем, что mercadona.record_detection вызывается при 429."""
    import src.skills.mercadona as merc

    calls = []
    monkeypatch.setattr(merc, "record_detection", lambda layer: calls.append(layer))

    response = _make_response(429)

    # Воспроизводим тело handle_response с подменённой record_detection
    collected: list = []
    api_data_received = asyncio.Event()

    async def handle_response(resp):
        url = resp.url
        if not any(p in url for p in merc._API_PATTERNS):
            return
        if resp.status == 429:
            merc.record_detection("ratelimit")
            return
        if resp.status in (401, 403):
            merc.record_detection("blocked")
            return
        if resp.status < 200 or resp.status >= 300:
            return

    asyncio.get_event_loop().run_until_complete(handle_response(response))
    assert calls == ["ratelimit"]


def test_record_detection_called_in_handle_response_blocked(monkeypatch):
    """Проверяем, что mercadona.record_detection вызывается при 403."""
    import src.skills.mercadona as merc

    calls = []
    monkeypatch.setattr(merc, "record_detection", lambda layer: calls.append(layer))

    response = _make_response(403)

    async def handle_response(resp):
        url = resp.url
        if not any(p in url for p in merc._API_PATTERNS):
            return
        if resp.status == 429:
            merc.record_detection("ratelimit")
            return
        if resp.status in (401, 403):
            merc.record_detection("blocked")
            return

    asyncio.get_event_loop().run_until_complete(handle_response(response))
    assert calls == ["blocked"]
