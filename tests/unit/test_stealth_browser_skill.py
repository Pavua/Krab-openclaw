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
async def test_audio_bypass_returns_false_when_voice_engine_missing():
    """voice_engine=None → ранний exit, False. Session 39 Phase 2 contract."""
    from src.skills.stealth_browser import attempt_recaptcha_audio_bypass

    page = MagicMock()
    result = await attempt_recaptcha_audio_bypass(page, voice_engine=None)
    assert result is False


@pytest.mark.asyncio
async def test_audio_bypass_returns_false_when_voice_engine_lacks_transcribe():
    """voice_engine без .transcribe attr → False (defensive contract)."""
    from src.skills.stealth_browser import attempt_recaptcha_audio_bypass

    page = MagicMock()
    voice_engine = object()  # plain object, no transcribe
    result = await attempt_recaptcha_audio_bypass(page, voice_engine=voice_engine)
    assert result is False


@pytest.mark.asyncio
async def test_audio_bypass_returns_false_when_no_anchor_frame():
    """Если на странице нет anchor frame с recaptcha — False."""
    from src.skills.stealth_browser import attempt_recaptcha_audio_bypass

    voice_engine = MagicMock()
    voice_engine.transcribe = AsyncMock(return_value="hello")

    page = MagicMock()
    page.frames = []  # Нет ни одного frame с recaptcha api2
    result = await attempt_recaptcha_audio_bypass(page, voice_engine=voice_engine)
    assert result is False


@pytest.mark.asyncio
async def test_audio_bypass_returns_true_when_no_challenge_needed(tmp_path):
    """Если checkbox click сразу solved (нет bframe challenge) → True."""
    from src.skills.stealth_browser import attempt_recaptcha_audio_bypass

    voice_engine = MagicMock()
    voice_engine.transcribe = AsyncMock(return_value="hello")

    # Anchor frame с checkbox
    checkbox = MagicMock()
    checkbox.click = AsyncMock()
    anchor_frame = MagicMock()
    anchor_frame.url = "https://google.com/recaptcha/api2/anchor?k=abc"
    anchor_frame.query_selector = AsyncMock(return_value=checkbox)

    page = MagicMock()
    # Только anchor frame, bframe не появился — challenge skipped
    page.frames = [anchor_frame]

    result = await attempt_recaptcha_audio_bypass(
        page, voice_engine=voice_engine, download_dir=tmp_path
    )
    assert result is True
    checkbox.click.assert_awaited()


@pytest.mark.asyncio
async def test_audio_bypass_full_flow_solves(tmp_path):
    """End-to-end happy path: checkbox → audio button → MP3 → STT → verify."""
    from src.skills.stealth_browser import attempt_recaptcha_audio_bypass

    voice_engine = MagicMock()
    voice_engine.transcribe = AsyncMock(return_value="three two one")

    # Anchor frame после verify показывает .recaptcha-checkbox-checked
    checkbox = MagicMock()
    checkbox.click = AsyncMock()
    checkmark = MagicMock()  # после verify
    anchor_frame = MagicMock()
    anchor_frame.url = "https://google.com/recaptcha/api2/anchor?k=abc"
    # Сначала возвращаем checkbox, потом checkmark
    anchor_frame.query_selector = AsyncMock(side_effect=[checkbox, checkmark])

    # Bframe с audio challenge
    audio_btn = MagicMock()
    audio_btn.click = AsyncMock()
    audio_src_el = MagicMock()
    audio_src_el.get_attribute = AsyncMock(return_value="https://google.com/audio.mp3")
    response_input = MagicMock()
    response_input.fill = AsyncMock()
    verify_btn = MagicMock()
    verify_btn.click = AsyncMock()
    bframe = MagicMock()
    bframe.url = "https://google.com/recaptcha/api2/bframe?k=abc"
    bframe.query_selector = AsyncMock(
        side_effect=[audio_btn, audio_src_el, response_input, verify_btn]
    )

    page = MagicMock()
    page.frames = [anchor_frame, bframe]
    # request.get для скачивания MP3
    mock_response = MagicMock()
    mock_response.body = AsyncMock(return_value=b"\xff\xfb\x00\x00fake_mp3")
    page.context.request.get = AsyncMock(return_value=mock_response)

    result = await attempt_recaptcha_audio_bypass(
        page, voice_engine=voice_engine, download_dir=tmp_path
    )
    assert result is True
    voice_engine.transcribe.assert_awaited()
    response_input.fill.assert_awaited_with("three two one")
    verify_btn.click.assert_awaited()
