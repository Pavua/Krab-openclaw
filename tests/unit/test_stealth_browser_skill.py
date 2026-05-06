# -*- coding: utf-8 -*-
"""Tests for stealth_browser skill (Session 39)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_skill_importable():
    from src.skills import stealth_browser

    assert stealth_browser.__name__.endswith("stealth_browser")


def test_captcha_detection_dataclass_frozen():
    from src.skills.stealth_browser import CaptchaDetection

    d = CaptchaDetection(
        kind="recaptcha_v2", selector="iframe", iframe_url="http://x", score_based=False
    )
    with pytest.raises(Exception):
        d.kind = "hcaptcha"  # frozen — нельзя менять


def test_signatures_cover_all_kinds():
    """Все CaptchaKind вариант должны быть в _CAPTCHA_SIGNATURES (или 'unknown' fallback)."""
    from src.skills.stealth_browser import _CAPTCHA_SIGNATURES

    kinds_in_sigs = {s[0] for s in _CAPTCHA_SIGNATURES}
    expected = {"recaptcha_v2", "recaptcha_v3", "hcaptcha", "cloudflare_turnstile", "unknown"}
    assert kinds_in_sigs == expected


def test_stealth_init_script_contains_key_patches():
    from src.skills.stealth_browser import get_stealth_init_script

    js = get_stealth_init_script()
    # Все 4 patch'а должны присутствовать
    assert "navigator.permissions" in js, "permissions patch missing"
    assert "getParameter" in js, "WebGL patch missing"
    assert "__playwright" in js, "playwright marker hide missing"
    assert "navigator.webdriver" in js or "webdriver" in js, "webdriver patch missing"


@pytest.mark.asyncio
async def test_detect_captcha_returns_none_when_no_signatures_match():
    from src.skills.stealth_browser import detect_captcha

    page = MagicMock()
    page.url = "https://example.com/"
    page.query_selector = AsyncMock(return_value=None)

    result = await detect_captcha(page)
    assert result is None


@pytest.mark.asyncio
async def test_detect_captcha_recognizes_recaptcha_v2():
    from src.skills.stealth_browser import detect_captcha

    iframe = MagicMock()
    iframe.get_attribute = AsyncMock(return_value="https://google.com/recaptcha/api2/anchor?k=abc")

    async def fake_query(selector: str):
        # Возвращаем iframe только для recaptcha_v2 selector
        if "recaptcha/api2" in selector:
            return iframe
        return None

    page = MagicMock()
    page.url = "https://test.com/"
    page.query_selector = AsyncMock(side_effect=fake_query)

    result = await detect_captcha(page)
    assert result is not None
    assert result.kind == "recaptcha_v2"
    assert "recaptcha" in result.iframe_url


@pytest.mark.asyncio
async def test_apply_human_like_delays_jitter_in_range():
    """Задержка должна быть в [min_sec, max_sec]."""
    import time

    from src.skills.stealth_browser import apply_human_like_delays

    t0 = time.monotonic()
    await apply_human_like_delays(min_sec=0.05, max_sec=0.15)
    elapsed = time.monotonic() - t0
    assert 0.05 <= elapsed <= 0.30, f"elapsed {elapsed}"


@pytest.mark.asyncio
async def test_audio_bypass_returns_false_phase_1_stub():
    from src.skills.stealth_browser import attempt_recaptcha_audio_bypass

    page = MagicMock()
    voice_engine = MagicMock()
    result = await attempt_recaptcha_audio_bypass(page, voice_engine=voice_engine)
    assert result is False  # Phase 1 stub
