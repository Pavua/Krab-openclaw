"""Wave 44-Y-stealth-browser tests — stealth, captcha, humanize."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_TOOLS = REPO_ROOT / "scripts" / "agent_tools"
sys.path.insert(0, str(AGENT_TOOLS))

import _browser_humanize as humanize_mod  # noqa: E402
import _browser_stealth as stealth_mod  # noqa: E402
import _captcha_solver as captcha_mod  # noqa: E402

# -------- stealth --------


def test_pick_user_agent_returns_chrome_string():
    ua = stealth_mod.pick_user_agent()
    assert "Chrome/" in ua
    assert "Macintosh" in ua


def test_sec_ch_ua_extracts_major_version():
    ua = "Mozilla/5.0 ... Chrome/131.0.0.0 Safari/..."
    sec = stealth_mod._sec_ch_ua_for(ua)
    assert 'v="131"' in sec


def test_sec_ch_ua_fallback_on_bad_ua():
    sec = stealth_mod._sec_ch_ua_for("garbage")
    assert "Chrome" in sec  # default fallback


@pytest.mark.asyncio
async def test_apply_stealth_calls_set_extra_http_headers():
    fake_context = MagicMock()
    fake_context.set_extra_http_headers = AsyncMock()

    with patch("playwright_stealth.Stealth") as MockStealth:
        instance = MagicMock()
        instance.apply_stealth_async = AsyncMock()
        MockStealth.return_value = instance
        result = await stealth_mod.apply_stealth(fake_context)

    assert "user_agent" in result
    fake_context.set_extra_http_headers.assert_called_once()
    headers = fake_context.set_extra_http_headers.call_args[0][0]
    assert "Accept-Language" in headers
    assert "sec-ch-ua" in headers


# -------- humanize --------


def test_bezier_point_at_endpoints():
    p0 = (0.0, 0.0)
    p1 = (50.0, 100.0)
    p2 = (100.0, 0.0)
    assert humanize_mod._bezier_point(p0, p1, p2, 0.0) == p0
    assert humanize_mod._bezier_point(p0, p1, p2, 1.0) == p2


@pytest.mark.asyncio
async def test_move_bezier_emits_multiple_moves():
    page = MagicMock()
    page.mouse = MagicMock()
    page.mouse.move = AsyncMock()
    moves = await humanize_mod._move_bezier(page, (0.0, 0.0), (200.0, 200.0), steps=10)
    assert moves == 10
    assert page.mouse.move.await_count == 10


@pytest.mark.asyncio
async def test_human_click_uses_bezier_path():
    page = MagicMock()
    page.viewport_size = {"width": 1280, "height": 800}
    el = MagicMock()
    el.bounding_box = AsyncMock(return_value={"x": 100, "y": 100, "width": 50, "height": 20})
    page.wait_for_selector = AsyncMock(return_value=el)
    page.mouse = MagicMock()
    page.mouse.move = AsyncMock()
    page.mouse.click = AsyncMock()

    res = await humanize_mod.human_click(page, "#btn")
    assert res["ok"] is True
    assert res["moves"] >= 1
    page.mouse.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_human_type_variable_delays(monkeypatch):
    page = MagicMock()
    el = MagicMock()
    el.click = AsyncMock()
    page.wait_for_selector = AsyncMock(return_value=el)
    page.keyboard = MagicMock()
    page.keyboard.type = AsyncMock()
    page.keyboard.press = AsyncMock()

    sleeps: list[float] = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(humanize_mod.asyncio, "sleep", fake_sleep)
    res = await humanize_mod.human_type(page, "#in", "hello")
    assert res["ok"] is True
    assert res["chars"] == 5
    # Variable delays — at least 5 char-delay sleeps + initial click delay.
    assert len(sleeps) >= 5
    # not all equal
    assert len(set(sleeps)) > 1


# -------- captcha --------


@pytest.mark.asyncio
async def test_detect_captcha_recaptcha_v2():
    page = MagicMock()
    page.url = "https://example.com/login"
    iframe = MagicMock()

    async def query(sel):
        if "recaptcha/api2" in sel:
            return iframe
        return None

    page.query_selector = AsyncMock(side_effect=query)
    page.evaluate = AsyncMock(return_value="6Ld_test_sitekey_xyz")

    out = await captcha_mod.detect_captcha(page)
    assert out is not None
    assert out["type"] == "recaptcha_v2"
    assert out["sitekey"] == "6Ld_test_sitekey_xyz"


@pytest.mark.asyncio
async def test_detect_captcha_returns_none_when_clean():
    page = MagicMock()
    page.url = "https://example.com/"
    page.query_selector = AsyncMock(return_value=None)
    page.evaluate = AsyncMock(return_value=None)
    out = await captcha_mod.detect_captcha(page)
    assert out is None


@pytest.mark.asyncio
async def test_solve_if_captcha_no_service_returns_manual(monkeypatch):
    captcha_mod.reset_solve_counter()
    monkeypatch.delenv("KRAB_CAPTCHA_SERVICE", raising=False)
    monkeypatch.delenv("KRAB_CAPTCHA_API_KEY", raising=False)

    page = MagicMock()
    page.url = "https://x.com/"
    iframe = MagicMock()

    async def query(sel):
        return iframe if "recaptcha/api2" in sel else None

    page.query_selector = AsyncMock(side_effect=query)
    page.evaluate = AsyncMock(return_value="key")
    out = await captcha_mod.solve_if_captcha(page)
    assert out is not None
    assert out.get("requires_manual") is True


@pytest.mark.asyncio
async def test_solve_2captcha_mocked(monkeypatch):
    """Mock 2captcha API: in.php returns task id, res.php returns ready+token."""
    captcha_mod.reset_solve_counter()
    monkeypatch.setenv("KRAB_CAPTCHA_SERVICE", "2captcha")
    monkeypatch.setenv("KRAB_CAPTCHA_API_KEY", "fake_key")
    monkeypatch.setattr(captcha_mod, "POLL_INTERVAL_SEC", 0.01)

    submit_resp = MagicMock()
    submit_resp.json = MagicMock(return_value={"status": 1, "request": "task123"})
    poll_resp = MagicMock()
    poll_resp.json = MagicMock(return_value={"status": 1, "request": "TOKEN_ABC"})

    class FakeClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **kw):
            return submit_resp

        async def get(self, *a, **kw):
            return poll_resp

    fake_httpx = MagicMock(AsyncClient=FakeClient)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    page = MagicMock()
    page.url = "https://t.com/"
    iframe = MagicMock()

    async def query(sel):
        return iframe if "recaptcha/api2" in sel else None

    page.query_selector = AsyncMock(side_effect=query)
    page.evaluate = AsyncMock(return_value="sk_xyz")

    out = await captcha_mod.solve_if_captcha(page, timeout=5.0)
    assert out is not None
    assert out["ok"] is True
    assert "TOKEN_ABC"[:20] in out["solution"] or out["solution"].startswith("TOKEN_ABC")


@pytest.mark.asyncio
async def test_inject_solution_recaptcha_v2(monkeypatch):
    page = MagicMock()
    page.evaluate = AsyncMock()
    captcha = {"type": "recaptcha_v2", "sitekey": "x", "url": "https://y.com"}
    ok = await captcha_mod._inject_solution(page, captcha, "TOKEN_XYZ")
    assert ok is True
    page.evaluate.assert_awaited_once()
    assert "g-recaptcha-response" in page.evaluate.call_args[0][0]


@pytest.mark.asyncio
async def test_max_solves_guard(monkeypatch):
    """After MAX_SOLVES_PER_RUN, further solves should be refused."""
    captcha_mod.reset_solve_counter()
    monkeypatch.setattr(captcha_mod, "MAX_SOLVES_PER_RUN", 1)
    monkeypatch.setenv("KRAB_CAPTCHA_SERVICE", "2captcha")
    monkeypatch.setenv("KRAB_CAPTCHA_API_KEY", "fake")
    captcha_mod._SOLVE_COUNT = 1  # at limit

    page = MagicMock()
    page.url = "https://z.com/"
    iframe = MagicMock()

    async def query(sel):
        return iframe if "recaptcha/api2" in sel else None

    page.query_selector = AsyncMock(side_effect=query)
    page.evaluate = AsyncMock(return_value="sk")
    out = await captcha_mod.solve_if_captcha(page)
    assert out is not None
    assert out.get("error") == "max_solves_exceeded"


# -------- CLI flags --------


def test_no_stealth_no_humanize_flags_present():
    sys.path.insert(0, str(AGENT_TOOLS))
    import importlib

    # ensure fresh import
    if "krab_browser" in sys.modules:
        importlib.reload(sys.modules["krab_browser"])
    import krab_browser  # noqa: PLC0415

    parser = krab_browser._build_parser()
    args = parser.parse_args(
        ["open", "--url", "https://example.com", "--no-stealth", "--no-humanize"]
    )
    assert args.no_stealth is True
    assert args.no_humanize is True


def test_solve_captcha_subcommand_exists():
    import importlib

    if "krab_browser" in sys.modules:
        importlib.reload(sys.modules["krab_browser"])
    import krab_browser  # noqa: PLC0415

    parser = krab_browser._build_parser()
    args = parser.parse_args(["solve_captcha", "--url", "https://example.com"])
    assert args.cmd == "solve_captcha"
