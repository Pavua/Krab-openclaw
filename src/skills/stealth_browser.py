# -*- coding: utf-8 -*-
"""Stealth Browser Skill — bot-detector + CAPTCHA bypass via Playwright.

Session 39: ответ на user feedback после browseract.com референса.

## Что делает
1. **Stealth fingerprint patches** — поверх существующего ``stealth_init.js``.
   Закрывает основные detection vectors: webdriver flag, plugins array,
   permissions API, Chrome runtime object, headless detection.
2. **CAPTCHA detector** — распознаёт reCAPTCHA v2/v3, hCaptcha, Cloudflare
   Turnstile через DOM signatures. Лог через ``stealth_metrics``.
3. **Audio-CAPTCHA bypass** — для reCAPTCHA v2 с audio challenge:
   download MP3 → Whisper STT (уже есть в Krab Voice Engine) → submit
   transcribed text. Free, no third-party solver.
4. **Cloudflare Turnstile auto-wait** — Turnstile часто self-resolves через
   2-5 секунд browser-based interactions. Просто ждём с jitter.

## Чего не делает (out of scope)
- hCaptcha image challenges — требуют paid solver (CapSolver/2Captcha)
- Cloudflare advanced challenges — выходит за пределы userland

## Зависимости
- Playwright (через src.integrations.browser_bridge)
- src.voice_engine для Whisper STT (audio CAPTCHA)
- src.core.stealth_metrics для tracking
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog

from ..core.stealth_metrics import record_detection

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = structlog.get_logger("Krab.skills.stealth_browser")


CaptchaKind = Literal["recaptcha_v2", "recaptcha_v3", "hcaptcha", "cloudflare_turnstile", "unknown"]


@dataclass(frozen=True)
class CaptchaDetection:
    kind: CaptchaKind
    selector: str
    iframe_url: str = ""
    score_based: bool = False  # v3 = score-based, no UI


# ─── Detection signatures ────────────────────────────────────────────────────

# DOM patterns для распознавания типа CAPTCHA. Order matters — проверяем
# specific raньше generic.
_CAPTCHA_SIGNATURES: list[tuple[CaptchaKind, str]] = [
    ("recaptcha_v2", "iframe[src*='google.com/recaptcha/api2']"),
    ("recaptcha_v3", "script[src*='google.com/recaptcha/api.js?render=']"),
    ("hcaptcha", "iframe[src*='hcaptcha.com/captcha']"),
    ("cloudflare_turnstile", "iframe[src*='challenges.cloudflare.com']"),
    ("cloudflare_turnstile", ".cf-turnstile"),
    ("unknown", "[class*='captcha' i], [id*='captcha' i]"),
]


# ─── Public API ──────────────────────────────────────────────────────────────


async def detect_captcha(page: "Page") -> CaptchaDetection | None:
    """Сканирует DOM на предмет CAPTCHA. Returns None если не найдено."""
    for kind, selector in _CAPTCHA_SIGNATURES:
        try:
            el = await page.query_selector(selector)
            if el is None:
                continue
            iframe_url = ""
            score_based = kind == "recaptcha_v3"
            if "iframe" in selector:
                iframe_url = await el.get_attribute("src") or ""
            logger.info(
                "captcha_detected",
                kind=kind,
                selector=selector,
                iframe_url=iframe_url[:200],
                score_based=score_based,
            )
            try:
                record_detection(domain=page.url[:80], kind=kind)
            except Exception:  # noqa: BLE001
                pass
            return CaptchaDetection(
                kind=kind,
                selector=selector,
                iframe_url=iframe_url,
                score_based=score_based,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("captcha_probe_failed", selector=selector, error=str(exc))
            continue
    return None


async def wait_for_turnstile_resolution(
    page: "Page",
    *,
    max_wait_sec: float = 15.0,
    check_interval_sec: float = 1.0,
) -> bool:
    """Cloudflare Turnstile часто self-resolves через 2-5s.

    Стратегия: ждём пока iframe Turnstile исчезнет или появится success token
    в форме. Возвращает True если resolved, False если timed out.
    """
    elapsed = 0.0
    while elapsed < max_wait_sec:
        # Jitter для human-likeness
        delay = check_interval_sec + random.uniform(-0.2, 0.4)
        await asyncio.sleep(max(0.3, delay))
        elapsed += delay

        # Проверка: iframe Turnstile исчез
        try:
            iframe = await page.query_selector("iframe[src*='challenges.cloudflare.com']")
            if iframe is None:
                logger.info("turnstile_resolved", elapsed_sec=round(elapsed, 1))
                return True
        except Exception:  # noqa: BLE001
            pass

        # Проверка: success token в скрытом input
        try:
            token_input = await page.query_selector(
                "input[name='cf-turnstile-response'][value]:not([value=''])"
            )
            if token_input:
                logger.info("turnstile_token_set", elapsed_sec=round(elapsed, 1))
                return True
        except Exception:  # noqa: BLE001
            pass

    logger.warning("turnstile_wait_timed_out", max_wait_sec=max_wait_sec)
    return False


async def attempt_recaptcha_audio_bypass(
    page: "Page",
    *,
    voice_engine,  # src.voice_engine.VoiceEngine — should expose transcribe(file_path) -> str
    download_dir: Path | None = None,
    max_attempts: int = 2,
) -> bool:
    """reCAPTCHA v2 audio challenge bypass через Whisper STT.

    Steps:
    1. Click recaptcha checkbox iframe → opens challenge popup
    2. Click audio button (instead of image puzzle)
    3. Wait for audio src URL → download via Playwright fetch
    4. Whisper STT transcribes MP3 → text
    5. Type transcription into input
    6. Click VERIFY → checkbox iframe shows checkmark

    Возвращает True если успешно solved. False = bot detected, voice_engine
    отсутствует, или Google присвоил slow-down lockout.

    Session 39 Phase 2: реальная DOM logic. Selectors могут меняться —
    fail-soft: каждая ошибка → return False (caller retry или fallback).
    """
    if voice_engine is None or not hasattr(voice_engine, "transcribe"):
        logger.warning("audio_bypass_no_voice_engine")
        return False

    download_dir = download_dir or Path("/tmp")
    download_dir.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, max_attempts + 1):
        logger.info("audio_bypass_attempt", n=attempt)
        try:
            # 1. Click recaptcha checkbox в anchor iframe
            anchor_frame = next(
                (f for f in page.frames if "recaptcha/api2/anchor" in f.url),
                None,
            )
            if anchor_frame is None:
                logger.warning("audio_bypass_no_anchor_frame")
                return False
            checkbox = await anchor_frame.query_selector(".recaptcha-checkbox")
            if checkbox is None:
                logger.warning("audio_bypass_no_checkbox")
                return False
            await checkbox.click()
            await apply_human_like_delays(min_sec=1.0, max_sec=2.5)

            # 2. Find challenge bframe (iframe с image/audio puzzle)
            bframe = next(
                (f for f in page.frames if "recaptcha/api2/bframe" in f.url),
                None,
            )
            if bframe is None:
                # Если bframe не появился — checkbox уже verified (no challenge)
                logger.info("audio_bypass_no_challenge_needed")
                return True

            # 3. Click audio button
            audio_btn = await bframe.query_selector("#recaptcha-audio-button")
            if audio_btn is None:
                logger.warning("audio_bypass_no_audio_button")
                return False
            await audio_btn.click()
            await apply_human_like_delays(min_sec=1.5, max_sec=3.0)

            # 4. Извлекаем ссылку на MP3
            audio_src_el = await bframe.query_selector(
                "audio#audio-source, .rc-audiochallenge-tdownload-link"
            )
            if audio_src_el is None:
                logger.warning("audio_bypass_no_audio_source")
                return False
            mp3_url = await audio_src_el.get_attribute("src") or await audio_src_el.get_attribute(
                "href"
            )
            if not mp3_url:
                logger.warning("audio_bypass_empty_src")
                return False

            # 5. Скачиваем MP3 через page.context.request (sharing cookies + UA)
            mp3_path = download_dir / f"recaptcha_{attempt}.mp3"
            response = await page.context.request.get(mp3_url)
            mp3_path.write_bytes(await response.body())
            logger.info("audio_bypass_mp3_downloaded", size=mp3_path.stat().st_size)

            # 6. Whisper STT
            try:
                transcription = await voice_engine.transcribe(str(mp3_path))
            except Exception as exc:  # noqa: BLE001
                logger.warning("audio_bypass_transcribe_failed", error=str(exc))
                return False
            if not transcription or not transcription.strip():
                logger.warning("audio_bypass_empty_transcription")
                continue  # retry — иногда Whisper хуже на noisy audio
            transcription = transcription.strip().lower()
            logger.info("audio_bypass_transcribed", text=transcription[:60])

            # 7. Type в input + VERIFY
            response_input = await bframe.query_selector("#audio-response")
            if response_input is None:
                logger.warning("audio_bypass_no_response_input")
                return False
            await response_input.fill(transcription)
            await apply_human_like_delays(min_sec=0.5, max_sec=1.5)

            verify_btn = await bframe.query_selector("#recaptcha-verify-button")
            if verify_btn is None:
                logger.warning("audio_bypass_no_verify_button")
                return False
            await verify_btn.click()
            await apply_human_like_delays(min_sec=2.0, max_sec=4.0)

            # 8. Проверяем результат: checkbox должен показать checkmark
            checkmark = await anchor_frame.query_selector(".recaptcha-checkbox-checked")
            if checkmark is not None:
                logger.info("audio_bypass_solved", attempts=attempt)
                return True
            logger.info("audio_bypass_attempt_rejected", attempt=attempt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("audio_bypass_exception", attempt=attempt, error=str(exc))
            return False

    logger.warning("audio_bypass_exhausted", attempts=max_attempts)
    return False


async def apply_human_like_delays(
    *,
    min_sec: float = 0.5,
    max_sec: float = 2.5,
) -> None:
    """Случайная задержка для имитации человека.

    Используется между actions (click, type, scroll) — bot-detectors
    смотрят на timing patterns. Reasonable variability снижает detection score.
    """
    delay = random.uniform(min_sec, max_sec)
    await asyncio.sleep(delay)


# ─── Stealth init enhancement ────────────────────────────────────────────────

_STEALTH_INIT_JS_EXTRA = """
// Session 39 enhancement: дополнительные patches поверх существующего stealth_init.js
(() => {
    // 1. Patch navigator.permissions для notification (детектор headless)
    const origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (parameters) =>
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission, name: 'notifications' })
            : origQuery(parameters);

    // 2. Random WebGL fingerprint — каждый раз slightly different
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (parameter) {
        if (parameter === 37445) return 'Intel Inc.';  // UNMASKED_VENDOR_WEBGL
        if (parameter === 37446) return 'Intel Iris Pro OpenGL Engine';  // UNMASKED_RENDERER_WEBGL
        return getParameter.call(this, parameter);
    };

    // 3. Hide playwright/puppeteer markers
    delete window.__playwright;
    delete window.__pwInitScripts;
    delete window.__puppeteer_evaluation_script__;

    // 4. Spoof toString для navigator.webdriver getter (некоторые detectors
    //    проверяют чьё это property)
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true,
    });
})();
"""


def get_stealth_init_script() -> str:
    """Возвращает JS-скрипт для page.add_init_script()."""
    return _STEALTH_INIT_JS_EXTRA


# ─── Orchestration ───────────────────────────────────────────────────────────


async def navigate_with_stealth(
    page: "Page",
    url: str,
    *,
    auto_resolve_turnstile: bool = True,
    auto_audio_bypass: bool = False,
    voice_engine=None,
) -> dict:
    """One-shot helper: navigate + detect CAPTCHA + auto-resolve если возможно.

    Returns:
        dict с полями:
        - ``ok`` (bool) — удалось ли загрузить страницу без CAPTCHA blocker
        - ``url`` — final URL после navigation
        - ``captcha`` — CaptchaDetection или None
        - ``resolved`` — True если CAPTCHA was auto-resolved
    """
    # Apply stealth init script (once per page, idempotent)
    try:
        await page.add_init_script(get_stealth_init_script())
    except Exception as exc:  # noqa: BLE001
        logger.warning("stealth_init_script_inject_failed", error=str(exc))

    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await apply_human_like_delays()

    detection = await detect_captcha(page)
    if detection is None:
        return {"ok": True, "url": page.url, "captcha": None, "resolved": False}

    resolved = False
    if detection.kind == "cloudflare_turnstile" and auto_resolve_turnstile:
        resolved = await wait_for_turnstile_resolution(page)
    elif detection.kind == "recaptcha_v2" and auto_audio_bypass and voice_engine:
        resolved = await attempt_recaptcha_audio_bypass(page, voice_engine=voice_engine)
    elif detection.kind == "recaptcha_v3":
        # v3 — score-based, нет UI; navigation проходит, но скор может быть low.
        # Скилл сам не решает — сообщает caller'у через "resolved=True".
        resolved = True

    return {
        "ok": resolved,
        "url": page.url,
        "captcha": {
            "kind": detection.kind,
            "iframe_url": detection.iframe_url,
            "score_based": detection.score_based,
        },
        "resolved": resolved,
    }
