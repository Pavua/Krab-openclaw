"""Wave 44-Y-stealth-browser — captcha detection & solving.

Supports services:
- 2captcha (`KRAB_CAPTCHA_SERVICE=2captcha`)
- CapSolver (`KRAB_CAPTCHA_SERVICE=capsolver`)
- anti-captcha (`KRAB_CAPTCHA_SERVICE=anti-captcha`)
- none / unset → no auto-solve, return {requires_manual: True}

Captcha types detected:
- reCAPTCHA v2  (iframe[src*="recaptcha/api2"])
- reCAPTCHA v3  (script[src*="recaptcha"] + grecaptcha.execute)
- hCaptcha      (iframe[src*="hcaptcha"])
- Cloudflare Turnstile (iframe[src*="challenges.cloudflare.com"])
- Image captcha (img[id*=captcha], img[class*=captcha])

Cost guard: per-process MAX_SOLVES_PER_RUN limits total spend per CLI invocation.

Latency: 15-60s typical. Caller responsible for surfacing progress.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 5.0
DEFAULT_TIMEOUT_SEC = 120.0
MAX_SOLVES_PER_RUN = int(os.environ.get("KRAB_CAPTCHA_MAX_SOLVES", "5"))

_SOLVE_COUNT = 0


def _service() -> str:
    return (os.environ.get("KRAB_CAPTCHA_SERVICE") or "none").strip().lower()


def _api_key() -> str:
    return (os.environ.get("KRAB_CAPTCHA_API_KEY") or "").strip()


async def detect_captcha(page: Any) -> dict[str, Any] | None:
    """Detect captcha on page. Returns dict with type+sitekey or None."""
    try:
        # reCAPTCHA v2
        v2 = await page.query_selector('iframe[src*="recaptcha/api2"]')
        if v2:
            sitekey = await page.evaluate(
                """() => {
                    const el = document.querySelector('[data-sitekey]');
                    return el ? el.getAttribute('data-sitekey') : null;
                }"""
            )
            return {"type": "recaptcha_v2", "sitekey": sitekey, "url": page.url}

        # hCaptcha
        hc = await page.query_selector('iframe[src*="hcaptcha"]')
        if hc:
            sitekey = await page.evaluate(
                """() => {
                    const el = document.querySelector('[data-sitekey]');
                    return el ? el.getAttribute('data-sitekey') : null;
                }"""
            )
            return {"type": "hcaptcha", "sitekey": sitekey, "url": page.url}

        # Cloudflare Turnstile
        turnstile = await page.query_selector('iframe[src*="challenges.cloudflare.com"]')
        if turnstile:
            sitekey = await page.evaluate(
                """() => {
                    const el = document.querySelector('[data-sitekey], .cf-turnstile');
                    return el ? (el.getAttribute('data-sitekey') || el.dataset.sitekey) : null;
                }"""
            )
            return {"type": "turnstile", "sitekey": sitekey, "url": page.url}

        # reCAPTCHA v3 (script-only, no iframe)
        v3 = await page.evaluate(
            """() => {
                const scripts = Array.from(document.querySelectorAll('script[src*="recaptcha"]'));
                if (!scripts.length) return null;
                // try to extract sitekey from scripts/global
                const m = document.body.innerHTML.match(/sitekey['"]?\\s*[:=]\\s*['"]([\\w-]{20,})/);
                return m ? m[1] : null;
            }"""
        )
        if v3:
            return {"type": "recaptcha_v3", "sitekey": v3, "url": page.url}

        # Image captcha
        img = await page.query_selector(
            'img[id*=captcha], img[class*=captcha], img[src*="captcha"]'
        )
        if img:
            return {"type": "image_captcha", "url": page.url}
    except Exception as exc:  # noqa: BLE001
        logger.debug("detect_captcha failed: %s", exc)
        return None
    return None


async def _solve_2captcha(
    captcha: dict[str, Any], api_key: str, timeout: float = DEFAULT_TIMEOUT_SEC
) -> str | None:
    """2captcha in.php → res.php polling. Returns token or None."""
    import httpx  # noqa: PLC0415

    ctype = captcha["type"]
    payload: dict[str, Any] = {"key": api_key, "json": 1}
    if ctype == "recaptcha_v2":
        payload["method"] = "userrecaptcha"
        payload["googlekey"] = captcha.get("sitekey") or ""
        payload["pageurl"] = captcha["url"]
    elif ctype == "recaptcha_v3":
        payload["method"] = "userrecaptcha"
        payload["version"] = "v3"
        payload["googlekey"] = captcha.get("sitekey") or ""
        payload["pageurl"] = captcha["url"]
        payload["min_score"] = 0.7
    elif ctype == "hcaptcha":
        payload["method"] = "hcaptcha"
        payload["sitekey"] = captcha.get("sitekey") or ""
        payload["pageurl"] = captcha["url"]
    elif ctype == "turnstile":
        payload["method"] = "turnstile"
        payload["sitekey"] = captcha.get("sitekey") or ""
        payload["pageurl"] = captcha["url"]
    else:
        return None

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post("https://2captcha.com/in.php", data=payload)
        data = r.json()
        if data.get("status") != 1:
            logger.warning("2captcha submit failed: %s", data)
            return None
        task_id = data["request"]

        deadline = time.time() + timeout
        while time.time() < deadline:
            await asyncio.sleep(POLL_INTERVAL_SEC)
            rr = await client.get(
                "https://2captcha.com/res.php",
                params={"key": api_key, "action": "get", "id": task_id, "json": 1},
            )
            rdata = rr.json()
            if rdata.get("status") == 1:
                return rdata["request"]
            if rdata.get("request") != "CAPCHA_NOT_READY":
                logger.warning("2captcha poll error: %s", rdata)
                return None
    return None


async def _solve_capsolver(
    captcha: dict[str, Any], api_key: str, timeout: float = DEFAULT_TIMEOUT_SEC
) -> str | None:
    """CapSolver createTask → getTaskResult."""
    import httpx  # noqa: PLC0415

    ctype = captcha["type"]
    type_map = {
        "recaptcha_v2": "ReCaptchaV2TaskProxyless",
        "recaptcha_v3": "ReCaptchaV3TaskProxyless",
        "hcaptcha": "HCaptchaTaskProxyless",
        "turnstile": "AntiTurnstileTaskProxyless",
    }
    task_type = type_map.get(ctype)
    if not task_type:
        return None
    task = {
        "type": task_type,
        "websiteURL": captcha["url"],
        "websiteKey": captcha.get("sitekey") or "",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            "https://api.capsolver.com/createTask",
            json={"clientKey": api_key, "task": task},
        )
        data = r.json()
        if data.get("errorId"):
            logger.warning("capsolver createTask failed: %s", data)
            return None
        task_id = data.get("taskId")
        deadline = time.time() + timeout
        while time.time() < deadline:
            await asyncio.sleep(POLL_INTERVAL_SEC)
            rr = await client.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
            )
            rd = rr.json()
            if rd.get("status") == "ready":
                sol = rd.get("solution") or {}
                return (
                    sol.get("gRecaptchaResponse") or sol.get("token") or sol.get("captchaResponse")
                )
    return None


async def _inject_solution(page: Any, captcha: dict[str, Any], token: str) -> bool:
    """Inject solution back into page DOM."""
    ctype = captcha["type"]
    try:
        if ctype in ("recaptcha_v2", "recaptcha_v3"):
            await page.evaluate(
                f"""() => {{
                    const el = document.getElementById('g-recaptcha-response');
                    if (el) {{
                        el.style.display = 'block';
                        el.value = {token!r};
                    }}
                    if (window.___grecaptcha_cfg) {{
                        try {{
                            const clients = window.___grecaptcha_cfg.clients || {{}};
                            for (const k of Object.keys(clients)) {{
                                const c = clients[k];
                                for (const p of Object.values(c)) {{
                                    for (const cb of Object.values(p || {{}})) {{
                                        if (cb && typeof cb.callback === 'function') {{
                                            cb.callback({token!r});
                                        }}
                                    }}
                                }}
                            }}
                        }} catch (_) {{}}
                    }}
                }}"""
            )
            return True
        if ctype == "hcaptcha":
            await page.evaluate(
                f"""() => {{
                    const el = document.querySelector('[name="h-captcha-response"]');
                    if (el) el.value = {token!r};
                    const el2 = document.querySelector('[name="g-recaptcha-response"]');
                    if (el2) el2.value = {token!r};
                }}"""
            )
            return True
        if ctype == "turnstile":
            await page.evaluate(
                f"""() => {{
                    const el = document.querySelector('[name="cf-turnstile-response"]');
                    if (el) el.value = {token!r};
                }}"""
            )
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("inject_solution failed: %s", exc)
    return False


async def solve_if_captcha(
    page: Any, *, timeout: float = DEFAULT_TIMEOUT_SEC, progress_cb: Any | None = None
) -> dict[str, Any] | None:
    """Detect + solve captcha. Returns:
    - None when no captcha detected
    - dict {ok, type, time_taken, ...} on success
    - dict {ok: False, requires_manual: True, ...} when no API or solve failed.
    """
    global _SOLVE_COUNT

    captcha = await detect_captcha(page)
    if not captcha:
        return None

    svc = _service()
    api_key = _api_key()

    if svc == "none" or not api_key:
        return {
            "ok": False,
            "type": captcha["type"],
            "requires_manual": True,
            "reason": "no_captcha_service_configured",
            "hint": "set KRAB_CAPTCHA_SERVICE and KRAB_CAPTCHA_API_KEY",
        }

    if _SOLVE_COUNT >= MAX_SOLVES_PER_RUN:
        return {
            "ok": False,
            "type": captcha["type"],
            "error": "max_solves_exceeded",
            "limit": MAX_SOLVES_PER_RUN,
        }

    started = time.time()

    # progress emitter (every 10s) for stagnation detector
    async def _progress_loop() -> None:
        while True:
            await asyncio.sleep(10)
            elapsed = time.time() - started
            if progress_cb:
                try:
                    progress_cb(f"captcha solving ({captcha['type']}) {elapsed:.0f}s elapsed")
                except Exception:  # noqa: BLE001
                    pass

    progress_task = asyncio.create_task(_progress_loop())
    try:
        if svc == "2captcha":
            token = await _solve_2captcha(captcha, api_key, timeout=timeout)
        elif svc == "capsolver":
            token = await _solve_capsolver(captcha, api_key, timeout=timeout)
        elif svc == "anti-captcha":
            # anti-captcha API is similar to capsolver — best-effort fallthrough
            token = await _solve_capsolver(captcha, api_key, timeout=timeout)
        else:
            token = None
    finally:
        progress_task.cancel()
        try:
            await progress_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    elapsed = time.time() - started
    if not token:
        return {
            "ok": False,
            "type": captcha["type"],
            "error": "solve_failed",
            "service": svc,
            "time_taken": round(elapsed, 1),
        }

    injected = await _inject_solution(page, captcha, token)
    _SOLVE_COUNT += 1
    return {
        "ok": True,
        "type": captcha["type"],
        "service": svc,
        "solution": token[:20] + "..." if len(token) > 20 else token,
        "injected": injected,
        "time_taken": round(elapsed, 1),
        "solves_used": _SOLVE_COUNT,
    }


def reset_solve_counter() -> None:
    """For tests."""
    global _SOLVE_COUNT
    _SOLVE_COUNT = 0
