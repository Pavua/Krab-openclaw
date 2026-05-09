"""Wave 44-Y-stealth-browser — stealth fingerprint masking for Playwright.

Wraps `playwright-stealth` (>=2.0) and adds:
- User-Agent rotation (recent macOS Chrome strings).
- Accept-Language matching profile (en-US,en + es-ES fallback).
- sec-ch-ua matching Chrome version.

Usage:
    from _browser_stealth import apply_stealth
    await apply_stealth(context)
"""

from __future__ import annotations

import logging
import random
from typing import Any

logger = logging.getLogger(__name__)

# Recent Chrome on macOS (Sequoia 15.x / Sonoma 14.x), updated 2026-05.
UA_POOL: tuple[str, ...] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
)

DEFAULT_ACCEPT_LANGUAGE = "en-US,en;q=0.9,es-ES;q=0.8,es;q=0.7,ru;q=0.6"


def pick_user_agent() -> str:
    """Pick a random recent Chrome UA string."""
    return random.choice(UA_POOL)


def _sec_ch_ua_for(ua: str) -> str:
    """Compose sec-ch-ua header consistent with Chrome major version in UA."""
    # extract chrome major
    try:
        chrome_part = ua.split("Chrome/")[1]
        major = chrome_part.split(".")[0]
    except (IndexError, ValueError):
        major = "131"
    return f'"Chromium";v="{major}", "Google Chrome";v="{major}", "Not?A_Brand";v="24"'


async def apply_stealth(context: Any, *, user_agent: str | None = None) -> dict[str, Any]:
    """Apply playwright-stealth patches + UA/headers tuning.

    Returns dict with applied settings (for logging).
    """
    from playwright_stealth import Stealth  # noqa: PLC0415

    ua = user_agent or pick_user_agent()
    sec_ch_ua = _sec_ch_ua_for(ua)

    # apply_stealth_async patches the context — navigator.webdriver, plugins,
    # languages, codecs, WebGL, Canvas etc.
    try:
        stealth = Stealth(
            navigator_languages_override=("en-US", "en"),
            navigator_user_agent_override=ua,
        )
        await stealth.apply_stealth_async(context)
    except Exception as exc:  # noqa: BLE001
        logger.warning("apply_stealth: playwright_stealth failed: %s", exc)

    # Extra HTTP headers
    try:
        await context.set_extra_http_headers(
            {
                "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
                "sec-ch-ua": sec_ch_ua,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"macOS"',
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("apply_stealth: set_extra_http_headers failed: %s", exc)

    return {
        "user_agent": ua,
        "sec_ch_ua": sec_ch_ua,
        "accept_language": DEFAULT_ACCEPT_LANGUAGE,
    }
