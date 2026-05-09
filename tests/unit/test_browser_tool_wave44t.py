"""Wave 44-T-browser-profile - tests for krab_browser.py bash tool."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "scripts" / "agent_tools"
SCRIPT_PATH = TOOLS_DIR / "krab_browser.py"
PYTHON = sys.executable

sys.path.insert(0, str(TOOLS_DIR))

import krab_browser  # noqa: E402  type: ignore


def _run(args: list[str], timeout: int = 30) -> tuple[int, dict | None, str]:
    proc = subprocess.run(
        [PYTHON, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        env={**os.environ},
        timeout=timeout,
    )
    parsed: dict | None = None
    out = proc.stdout.strip()
    if out:
        for line in reversed(out.splitlines()):
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    return proc.returncode, parsed, proc.stderr


# ---------- _classify_url ----------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://google.com", "ok"),
        ("https://github.com/user/repo", "ok"),
        ("https://example.com/page", "ok"),
        ("https://www.paypal.com/login", "financial"),
        ("https://paypal.com", "financial"),
        ("https://accounts.binance.com/login", "financial"),
        ("https://www.coinbase.com", "financial"),
        ("https://chase.com/banking", "financial"),
        ("https://www.irs.gov/forms", "government"),
        ("https://www.whitehouse.gov", "government"),
        ("https://agenciatributaria.es/x", "government"),
        ("not-a-url", "invalid"),
        ("", "invalid"),
    ],
)
def test_classify_url(url: str, expected: str) -> None:
    status, _ = krab_browser._classify_url(url)
    assert status == expected, f"{url} -> got {status}, want {expected}"


# ---------- owner token gate ----------


def test_verify_owner_token_no_file(tmp_path, monkeypatch):
    fake = tmp_path / "missing.token"
    monkeypatch.setattr(krab_browser, "OWNER_TOKEN_PATH", fake)
    assert krab_browser._verify_owner_token("anything") is False


def test_verify_owner_token_match(tmp_path, monkeypatch):
    token_file = tmp_path / "owner.token"
    token_file.write_text("secret-xyz\n")
    monkeypatch.setattr(krab_browser, "OWNER_TOKEN_PATH", token_file)
    assert krab_browser._verify_owner_token("secret-xyz") is True
    assert krab_browser._verify_owner_token("wrong") is False
    assert krab_browser._verify_owner_token(None) is False


def test_verify_owner_token_empty_file_rejects(tmp_path, monkeypatch):
    token_file = tmp_path / "owner.token"
    token_file.write_text("")
    monkeypatch.setattr(krab_browser, "OWNER_TOKEN_PATH", token_file)
    assert krab_browser._verify_owner_token("") is False
    assert krab_browser._verify_owner_token("anything") is False


# ---------- _check_safety ----------


def test_check_safety_ok():
    assert (
        krab_browser._check_safety("https://google.com", allow_financial=False, owner_token=None)
        is None
    )


def test_check_safety_financial_blocked():
    result = krab_browser._check_safety(
        "https://paypal.com", allow_financial=False, owner_token=None
    )
    assert result is not None
    assert result["ok"] is False
    assert result["error"] == "financial_blocked"


def test_check_safety_financial_allow_without_token_still_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(krab_browser, "OWNER_TOKEN_PATH", tmp_path / "missing")
    result = krab_browser._check_safety("https://paypal.com", allow_financial=True, owner_token="x")
    assert result is not None
    assert result["error"] == "financial_blocked"


def test_check_safety_financial_with_valid_token_allows(tmp_path, monkeypatch):
    token_file = tmp_path / "owner.token"
    token_file.write_text("realtoken")
    monkeypatch.setattr(krab_browser, "OWNER_TOKEN_PATH", token_file)
    result = krab_browser._check_safety(
        "https://paypal.com", allow_financial=True, owner_token="realtoken"
    )
    assert result is None


def test_check_safety_invalid_url():
    result = krab_browser._check_safety("garbage", allow_financial=False, owner_token=None)
    assert result is not None
    assert result["error"] == "invalid_url"


# ---------- subprocess: blocked navigation ----------


def test_subprocess_paypal_blocked_no_override():
    rc, parsed, _stderr = _run(["open", "--url", "https://paypal.com"])
    assert rc == 1
    assert parsed is not None
    assert parsed["ok"] is False
    assert parsed["error"] == "financial_blocked"


def test_subprocess_irs_blocked():
    rc, parsed, _stderr = _run(["open", "--url", "https://www.irs.gov/"])
    assert rc == 1
    assert parsed is not None
    assert parsed["ok"] is False
    assert parsed["error"] == "government_blocked"


def test_subprocess_invalid_url_short_circuits():
    rc, parsed, _stderr = _run(["open", "--url", "not-a-url"])
    assert rc == 1
    assert parsed is not None
    assert parsed["error"] == "invalid_url"


def test_subprocess_js_run_requires_token():
    """JS execution without token must hard-block."""
    rc, parsed, _stderr = _run(["js_run", "--url", "https://example.com", "--js", "1+1"])
    assert rc == 1
    assert parsed is not None
    assert parsed["error"] == "js_run_requires_owner_token"


# ---------- CLI parser ----------


def test_parser_subcommands():
    parser = krab_browser._build_parser()
    args = parser.parse_args(["open", "--url", "https://x.com"])
    assert args.cmd == "open"
    assert args.url == "https://x.com"
    assert args.no_cdp is False

    args2 = parser.parse_args(
        [
            "screenshot",
            "--url",
            "https://x.com",
            "--output",
            "/tmp/x.png",
            "--full-page",
        ]
    )
    assert args2.cmd == "screenshot"
    assert args2.full_page is True

    args3 = parser.parse_args(
        ["type", "--url", "https://x.com", "--selector", "input", "--text", "hi", "--submit"]
    )
    assert args3.submit is True
    assert args3.text == "hi"


# ---------- screenshot validator integration ----------


def test_screenshot_validation_invokes_image_validator(tmp_path):
    """Synthesize blank png; ensure validator marks it failed."""
    blank_path = tmp_path / "blank.png"
    blank_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)  # tiny garbage
    try:
        from src.core.image_validator import is_blank_image

        is_blank, reason = is_blank_image(blank_path)
        assert is_blank is True
        assert reason != "ok"
    except ImportError:
        pytest.skip("image_validator not available")


# ---------- live network test (skipped by default) ----------


@pytest.mark.skipif(
    os.environ.get("KRAB_BROWSER_LIVE_TEST") != "1",
    reason="live network test (set KRAB_BROWSER_LIVE_TEST=1 to run)",
)
def test_subprocess_open_google_live():
    rc, parsed, _stderr = _run(["open", "--url", "https://www.google.com"], timeout=60)
    assert rc == 0
    assert parsed is not None
    assert parsed["ok"] is True
    assert "Google" in parsed.get("title", "")
