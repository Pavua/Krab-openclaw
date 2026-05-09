#!/usr/bin/env python3
"""Wave 44-T-browser-profile - bash-callable Chrome browser tool with user's profile.

Krab agent (codex-cli) needs to interact with web pages using pavua's logged-in
accounts. Connects to running Chrome via DevTools port 9222 (preferred) or
launches a persistent isolated profile copy as fallback.

Subcommands:
    open       - navigate, return JSON
    screenshot - capture page
    extract    - return text content
    click      - click element by CSS selector
    type       - fill input, optionally submit
    js_run     - execute JS in DOM (RESTRICTED, requires owner_token)

Safety:
    - URL blocklist (banks, crypto exchanges, gov/tax) - HARD BLOCK
    - --allow-financial requires owner_token (~/.openclaw/krab_runtime_state/owner_confirm.token)
    - js_run requires owner_token (XSS risk)

Returns JSON {"ok": bool, ...}, logs to /tmp/krab_agent_tools.log.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import os  # noqa: E402

from _browser_humanize import human_click, human_type  # noqa: E402
from _browser_stealth import apply_stealth  # noqa: E402
from _captcha_solver import solve_if_captcha  # noqa: E402
from _common import emit_error, emit_json  # noqa: E402

SCRIPT = "krab_browser.py"

HUMANIZE_DEFAULT = os.environ.get("KRAB_BROWSER_HUMANIZE_DEFAULT", "1") not in ("0", "false", "")
STEALTH_DEFAULT = os.environ.get("KRAB_BROWSER_STEALTH_DEFAULT", "1") not in ("0", "false", "")

USER_CHROME_PROFILE = Path("/Users/pablito/Library/Application Support/Google/Chrome/Default")
USER_CHROME_USER_DATA_DIR = USER_CHROME_PROFILE.parent

CDP_URL = "http://127.0.0.1:9222"

OWNER_TOKEN_PATH = Path("~/.openclaw/krab_runtime_state/owner_confirm.token").expanduser()

FINANCIAL_BLOCKLIST = (
    "bank.com",
    "chase.com",
    "bankofamerica.com",
    "wellsfargo.com",
    "citibank.com",
    "hsbc.com",
    "santander.com",
    "bbva.com",
    "caixabank.es",
    "sabadell.com",
    "ing.com",
    "ing.es",
    "paypal.com",
    "venmo.com",
    "revolut.com",
    "wise.com",
    "stripe.com",
    "n26.com",
    "binance.com",
    "coinbase.com",
    "kraken.com",
    "bybit.com",
    "okx.com",
    "kucoin.com",
    "bitfinex.com",
    "gemini.com",
)

GOVERNMENT_BLOCKLIST_SUFFIXES = (".gov", ".gob.es")
GOVERNMENT_BLOCKLIST_KEYWORDS = ("irs.gov", "agenciatributaria", "tax.")

DEFAULT_TIMEOUT_MS = 20_000
DEFAULT_VIEWPORT = {"width": 1280, "height": 800}


def _classify_url(url: str) -> tuple[str, str]:
    """Returns (status, reason). status in {'ok', 'financial', 'government', 'invalid'}."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return "invalid", "url parse failed"
    if not host:
        return "invalid", "no hostname"
    for suffix in FINANCIAL_BLOCKLIST:
        if host == suffix or host.endswith("." + suffix):
            return "financial", f"matched financial: {suffix}"
    for kw in GOVERNMENT_BLOCKLIST_KEYWORDS:
        if kw in host:
            return "government", f"matched gov keyword: {kw}"
    for suf in GOVERNMENT_BLOCKLIST_SUFFIXES:
        if host.endswith(suf):
            return "government", f"matched gov suffix: {suf}"
    return "ok", ""


def _verify_owner_token(provided: str | None) -> bool:
    """Compares provided token with stored. Empty stored token = always reject."""
    if not provided:
        return False
    if not OWNER_TOKEN_PATH.is_file():
        return False
    try:
        stored = OWNER_TOKEN_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not stored:
        return False
    return provided.strip() == stored


def _check_safety(
    url: str, *, allow_financial: bool, owner_token: str | None
) -> dict[str, Any] | None:
    """Returns error dict if blocked, else None."""
    status, reason = _classify_url(url)
    if status == "ok":
        return None
    if status == "invalid":
        return {"ok": False, "error": "invalid_url", "reason": reason}
    if status in ("financial", "government"):
        if allow_financial and _verify_owner_token(owner_token):
            return None
        return {
            "ok": False,
            "error": f"{status}_blocked",
            "reason": reason,
            "hint": "use --allow-financial with valid --owner-token",
        }
    return None


def _is_cdp_alive() -> bool:
    """Checks if Chrome is running with --remote-debugging-port=9222."""
    try:
        import urllib.request  # noqa: PLC0415

        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=1.5) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


async def _get_context(
    playwright_obj: Any, *, prefer_cdp: bool, stealth: bool = True
) -> tuple[Any, Any, bool]:
    """Returns (holder, page, used_cdp). holder is browser (CDP) or context (launched)."""
    if prefer_cdp and _is_cdp_alive():
        browser = await playwright_obj.chromium.connect_over_cdp(CDP_URL)
        contexts = browser.contexts
        if contexts:
            context = contexts[0]
        else:
            context = await browser.new_context()
        if stealth:
            try:
                await apply_stealth(context)
            except Exception:  # noqa: BLE001
                pass
        page = await context.new_page()
        return browser, page, True

    isolated_dir = Path("/tmp/krab_chrome_profile_isolated")
    isolated_dir.mkdir(parents=True, exist_ok=True)
    context = await playwright_obj.chromium.launch_persistent_context(
        user_data_dir=str(isolated_dir),
        headless=True,
        viewport=DEFAULT_VIEWPORT,
        args=["--no-first-run", "--no-default-browser-check"],
    )
    if stealth:
        try:
            await apply_stealth(context)
        except Exception:  # noqa: BLE001
            pass
    page = await context.new_page()
    return context, page, False


async def _close_context(holder: Any, used_cdp: bool) -> None:
    try:
        await holder.close()
    except Exception:  # noqa: BLE001
        pass


async def _op_open(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import async_playwright  # noqa: PLC0415

    started = time.time()
    async with async_playwright() as p:
        holder, page, used_cdp = await _get_context(
            p, prefer_cdp=not args.no_cdp, stealth=not getattr(args, "no_stealth", False)
        )
        try:
            await page.goto(args.url, timeout=DEFAULT_TIMEOUT_MS, wait_until="domcontentloaded")
            title = await page.title()
            captcha_result: dict[str, Any] | None = None
            if not getattr(args, "skip_captcha", False):
                try:
                    captcha_result = await solve_if_captcha(page)
                except Exception as exc:  # noqa: BLE001
                    captcha_result = {"ok": False, "error": f"captcha_detect_fail: {exc}"}
            return {
                "ok": True,
                "url": args.url,
                "final_url": page.url,
                "title": title,
                "used_cdp": used_cdp,
                "captcha": captcha_result,
                "elapsed_sec": round(time.time() - started, 2),
            }
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
            await _close_context(holder, used_cdp)


async def _op_screenshot(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import async_playwright  # noqa: PLC0415

    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else Path(f"/tmp/krab_browser_{int(time.time())}.png")
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    started = time.time()
    async with async_playwright() as p:
        holder, page, used_cdp = await _get_context(
            p, prefer_cdp=not args.no_cdp, stealth=not getattr(args, "no_stealth", False)
        )
        try:
            await page.goto(args.url, timeout=DEFAULT_TIMEOUT_MS, wait_until="domcontentloaded")
            await page.screenshot(path=str(output), full_page=bool(args.full_page))
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
            await _close_context(holder, used_cdp)

    valid = True
    reason = "ok"
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from src.core.image_validator import is_blank_image  # noqa: PLC0415

        is_blank, reason = is_blank_image(output)
        valid = not is_blank
    except Exception:  # noqa: BLE001
        if not output.is_file() or output.stat().st_size < 20_000:
            valid = False
            reason = "size_too_small"

    if not valid:
        return {
            "ok": False,
            "error": "screenshot_validation_failed",
            "reason": reason,
            "screenshot_path": str(output),
            "size_bytes": output.stat().st_size if output.is_file() else 0,
        }

    return {
        "ok": True,
        "url": args.url,
        "screenshot_path": str(output),
        "size_bytes": output.stat().st_size,
        "used_cdp": used_cdp,
        "elapsed_sec": round(time.time() - started, 2),
    }


async def _op_extract(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import async_playwright  # noqa: PLC0415

    started = time.time()
    async with async_playwright() as p:
        holder, page, used_cdp = await _get_context(
            p, prefer_cdp=not args.no_cdp, stealth=not getattr(args, "no_stealth", False)
        )
        try:
            await page.goto(args.url, timeout=DEFAULT_TIMEOUT_MS, wait_until="domcontentloaded")
            if args.selector:
                el = await page.query_selector(args.selector)
                if el is None:
                    return {
                        "ok": False,
                        "error": "selector_not_found",
                        "selector": args.selector,
                    }
                text = (await el.inner_text()).strip()
            else:
                text = (await page.inner_text("body")).strip()
            limit = int(args.limit or 5000)
            truncated = text[:limit]
            return {
                "ok": True,
                "url": args.url,
                "selector": args.selector,
                "extracted_text": truncated,
                "truncated": len(text) > limit,
                "used_cdp": used_cdp,
                "elapsed_sec": round(time.time() - started, 2),
            }
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
            await _close_context(holder, used_cdp)


async def _op_click(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import async_playwright  # noqa: PLC0415

    started = time.time()
    async with async_playwright() as p:
        holder, page, used_cdp = await _get_context(
            p, prefer_cdp=not args.no_cdp, stealth=not getattr(args, "no_stealth", False)
        )
        try:
            await page.goto(args.url, timeout=DEFAULT_TIMEOUT_MS, wait_until="domcontentloaded")
            humanize = HUMANIZE_DEFAULT and not getattr(args, "no_humanize", False)
            click_meta: dict[str, Any]
            if humanize:
                click_meta = await human_click(page, args.selector, timeout_ms=DEFAULT_TIMEOUT_MS)
            else:
                await page.click(args.selector, timeout=DEFAULT_TIMEOUT_MS)
                click_meta = {"ok": True, "selector": args.selector, "humanized": False}
            await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
            return {
                "ok": True,
                "url": args.url,
                "final_url": page.url,
                "title": await page.title(),
                "selector": args.selector,
                "used_cdp": used_cdp,
                "humanized": humanize,
                "click_meta": click_meta,
                "elapsed_sec": round(time.time() - started, 2),
            }
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
            await _close_context(holder, used_cdp)


async def _op_type(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import async_playwright  # noqa: PLC0415

    started = time.time()
    async with async_playwright() as p:
        holder, page, used_cdp = await _get_context(
            p, prefer_cdp=not args.no_cdp, stealth=not getattr(args, "no_stealth", False)
        )
        try:
            await page.goto(args.url, timeout=DEFAULT_TIMEOUT_MS, wait_until="domcontentloaded")
            humanize = HUMANIZE_DEFAULT and not getattr(args, "no_humanize", False)
            type_meta: dict[str, Any]
            if humanize:
                type_meta = await human_type(
                    page, args.selector, args.text, timeout_ms=DEFAULT_TIMEOUT_MS
                )
            else:
                await page.fill(args.selector, args.text, timeout=DEFAULT_TIMEOUT_MS)
                type_meta = {"ok": True, "selector": args.selector, "humanized": False}
            if args.submit:
                await page.press(args.selector, "Enter")
                await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
            return {
                "ok": True,
                "url": args.url,
                "final_url": page.url,
                "selector": args.selector,
                "submitted": bool(args.submit),
                "used_cdp": used_cdp,
                "humanized": humanize,
                "type_meta": type_meta,
                "elapsed_sec": round(time.time() - started, 2),
            }
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
            await _close_context(holder, used_cdp)


async def _op_js_run(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import async_playwright  # noqa: PLC0415

    started = time.time()
    async with async_playwright() as p:
        holder, page, used_cdp = await _get_context(
            p, prefer_cdp=not args.no_cdp, stealth=not getattr(args, "no_stealth", False)
        )
        try:
            await page.goto(args.url, timeout=DEFAULT_TIMEOUT_MS, wait_until="domcontentloaded")
            result = await page.evaluate(args.js)
            try:
                import json  # noqa: PLC0415

                json.dumps(result)
                serialized = result
            except (TypeError, ValueError):
                serialized = repr(result)
            return {
                "ok": True,
                "url": args.url,
                "result": serialized,
                "used_cdp": used_cdp,
                "elapsed_sec": round(time.time() - started, 2),
            }
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
            await _close_context(holder, used_cdp)


async def _op_solve_captcha(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import async_playwright  # noqa: PLC0415

    started = time.time()
    async with async_playwright() as p:
        holder, page, used_cdp = await _get_context(
            p, prefer_cdp=not args.no_cdp, stealth=not getattr(args, "no_stealth", False)
        )
        try:
            await page.goto(args.url, timeout=DEFAULT_TIMEOUT_MS, wait_until="domcontentloaded")
            result = await solve_if_captcha(page)
            if result is None:
                return {
                    "ok": True,
                    "url": args.url,
                    "captcha_detected": False,
                    "used_cdp": used_cdp,
                    "elapsed_sec": round(time.time() - started, 2),
                }
            return {
                "ok": bool(result.get("ok")),
                "url": args.url,
                "captcha_detected": True,
                "captcha": result,
                "used_cdp": used_cdp,
                "elapsed_sec": round(time.time() - started, 2),
            }
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
            await _close_context(holder, used_cdp)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Krab browser tool (Wave 44-T)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", required=True)
    common.add_argument(
        "--no-cdp",
        action="store_true",
        help="skip CDP, use isolated profile copy (no logins)",
    )
    common.add_argument(
        "--allow-financial",
        action="store_true",
        help="override financial/gov blocklist (requires --owner-token)",
    )
    common.add_argument("--owner-token", default=None)
    common.add_argument(
        "--no-stealth",
        action="store_true",
        help="disable stealth fingerprint patches (debug only)",
    )
    common.add_argument(
        "--no-humanize",
        action="store_true",
        help="disable human-like delays/bezier mouse (faster, more bot-like)",
    )

    p_open = sub.add_parser("open", parents=[common])
    p_open.add_argument(
        "--skip-captcha", action="store_true", help="skip captcha auto-detect on open"
    )
    p_open.set_defaults(func=_op_open)

    p_solve = sub.add_parser("solve_captcha", parents=[common])
    p_solve.set_defaults(func=_op_solve_captcha)

    p_shot = sub.add_parser("screenshot", parents=[common])
    p_shot.add_argument("--output", default=None)
    p_shot.add_argument("--full-page", action="store_true")
    p_shot.set_defaults(func=_op_screenshot)

    p_ext = sub.add_parser("extract", parents=[common])
    p_ext.add_argument("--selector", default=None)
    p_ext.add_argument("--limit", type=int, default=5000)
    p_ext.set_defaults(func=_op_extract)

    p_click = sub.add_parser("click", parents=[common])
    p_click.add_argument("--selector", required=True)
    p_click.set_defaults(func=_op_click)

    p_type = sub.add_parser("type", parents=[common])
    p_type.add_argument("--selector", required=True)
    p_type.add_argument("--text", required=True)
    p_type.add_argument("--submit", action="store_true")
    p_type.set_defaults(func=_op_type)

    p_js = sub.add_parser("js_run", parents=[common])
    p_js.add_argument("--js", required=True)
    p_js.set_defaults(func=_op_js_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    blocked = _check_safety(
        args.url, allow_financial=args.allow_financial, owner_token=args.owner_token
    )
    if blocked is not None:
        emit_json(blocked, SCRIPT, sys.argv[1:])
        return 1

    if args.cmd == "js_run" and not _verify_owner_token(args.owner_token):
        emit_json(
            {
                "ok": False,
                "error": "js_run_requires_owner_token",
                "hint": "JS execution is XSS risk; --owner-token required",
            },
            SCRIPT,
            sys.argv[1:],
        )
        return 1

    try:
        result = asyncio.run(args.func(args))
    except KeyboardInterrupt:
        return emit_error("interrupted", SCRIPT, sys.argv[1:])
    except Exception as exc:  # noqa: BLE001
        return emit_error(f"{type(exc).__name__}: {exc}", SCRIPT, sys.argv[1:])

    emit_json(result, SCRIPT, sys.argv[1:])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
