# -*- coding: utf-8 -*-
"""
Tests for Wave 44-T-money-safety: bash_guard money patterns + browser_url_guard.

Layered with Wave 44-S — these tests focus on financial / payment / banking
JAIL BAR enforcement.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASH_GUARD = REPO / "scripts" / "agent_tools" / "bash_guard.sh"
BROWSER_GUARD = REPO / "scripts" / "agent_tools" / "browser_url_guard.py"


# =====================================================================
# bash_guard.sh — money patterns
# =====================================================================
class TestBashGuardMoney:
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(BASH_GUARD), *args],
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            timeout=10,
        )

    # --- CONFIRM (Wave 44-T-money-safety v2): money rules require owner_token ---
    def test_blocks_paypal_transfer(self) -> None:
        r = self._run("--cmd", "curl https://paypal.com/sendmoney/abc")
        assert r.returncode == 79, r.stderr
        assert "money" in r.stderr.lower()

    def test_blocks_stripe_charge_api(self) -> None:
        r = self._run(
            "--cmd",
            'curl -X POST https://api.stripe.com/v1/charges -d "amount=100"',
        )
        assert r.returncode == 79, r.stderr

    def test_blocks_browser_open_chase(self) -> None:
        r = self._run(
            "--cmd",
            "python krab_browser.py open --url https://chase.com",
        )
        assert r.returncode == 79, r.stderr
        assert "money" in r.stderr.lower()

    def test_blocks_browser_paypal(self) -> None:
        r = self._run("--cmd", "playwright open https://paypal.com/dashboard")
        assert r.returncode == 79, r.stderr

    def test_blocks_crypto_order(self) -> None:
        r = self._run(
            "--cmd",
            'curl -X POST https://api.binance.com/api/v3/order -d "symbol=BTC"',
        )
        assert r.returncode == 79, r.stderr

    def test_blocks_coinbase_buy(self) -> None:
        r = self._run("--cmd", "curl https://www.coinbase.com/buy/BTC")
        assert r.returncode == 79, r.stderr

    def test_blocks_irs_payment(self) -> None:
        r = self._run("--cmd", "curl https://www.irs.gov/payments/online")
        assert r.returncode == 79, r.stderr

    def test_blocks_send_amount_keyword(self) -> None:
        r = self._run("--cmd", 'echo "please send 100 USD to alice@example.com"')
        assert r.returncode == 79, r.stderr

    def test_blocks_transfer_eth_keyword(self) -> None:
        r = self._run("--cmd", "echo 'transfer 5 ETH to 0xabc'")
        assert r.returncode == 79, r.stderr

    def test_blocks_russian_perevedi_keyword(self) -> None:
        r = self._run("--cmd", "echo 'переведи 1000 RUB на счёт 12345'")
        assert r.returncode == 79, r.stderr

    # --- CONFIRM: read-only financial / purchase keywords ---
    def test_confirm_chase_curl_read(self) -> None:
        # curl https://chase.com (no /transfer path) — financial domain
        # read = soft confirm (per spec test case 4).
        r = self._run("--cmd", "curl https://chase.com")
        assert r.returncode == 79, r.stderr
        assert "OWNER_CONFIRM" in r.stderr or "money" in r.stderr.lower()

    def test_confirm_purchase_keyword(self) -> None:
        r = self._run("--cmd", "echo 'go buy a domain'")
        assert r.returncode == 79, r.stderr

    def test_confirm_subscribe_keyword(self) -> None:
        r = self._run("--cmd", 'echo "please subscribe to newsletter for me"')
        assert r.returncode == 79, r.stderr

    # --- ALLOW: regular safe commands not money-related ---
    def test_allow_normal_curl_google(self) -> None:
        r = self._run("--cmd", "curl https://google.com")
        assert r.returncode == 0, r.stderr

    def test_allow_normal_grep(self) -> None:
        r = self._run("--cmd", "grep foo /tmp/bar.log")
        # 0 if file missing → grep exits 2; we just check guard didn't BLOCK
        assert r.returncode not in (78, 79), r.stderr


# =====================================================================
# browser_url_guard.py
# =====================================================================
class TestBrowserUrlGuard:
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(BROWSER_GUARD), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_allow_google(self) -> None:
        r = self._run("--url", "https://google.com")
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert data["ok"] is True
        assert data["verdict"] == "ALLOW"

    def test_block_paypal_transfer(self) -> None:
        r = self._run("--url", "https://paypal.com/sendmoney/abc")
        assert r.returncode == 78, r.stderr
        data = json.loads(r.stderr)
        assert data["verdict"] == "BLOCK"

    def test_block_generic_transfer_path(self) -> None:
        r = self._run("--url", "https://my-bank.com/transfer/now")
        assert r.returncode == 78, r.stderr

    def test_block_javascript_scheme(self) -> None:
        r = self._run("--url", "javascript:alert(1)")
        assert r.returncode == 78, r.stderr

    def test_block_file_scheme(self) -> None:
        r = self._run("--url", "file:///etc/passwd")
        assert r.returncode == 78, r.stderr

    def test_confirm_chase_dashboard(self) -> None:
        # bank domain read-only → 79 without token
        r = self._run("--url", "https://chase.com/dashboard")
        assert r.returncode == 79, r.stderr
        data = json.loads(r.stderr)
        assert data["verdict"] == "CONFIRM"

    def test_confirm_paypal_root(self) -> None:
        r = self._run("--url", "https://paypal.com/")
        assert r.returncode == 79, r.stderr

    def test_block_binance_order(self) -> None:
        r = self._run("--url", "https://api.binance.com/api/v3/order?symbol=BTC")
        assert r.returncode == 78, r.stderr

    def test_allow_github(self) -> None:
        r = self._run("--url", "https://github.com/user/repo")
        assert r.returncode == 0, r.stderr

    def test_allow_subdomain_of_safe(self) -> None:
        r = self._run("--url", "https://docs.python.org/3/library/")
        assert r.returncode == 0, r.stderr

    def test_token_bypass_confirm(self, tmp_path, monkeypatch) -> None:
        # Write fake token + invoke with --owner-confirm-token, expect ALLOW.
        # Use real TOKEN_PATH override via env? — module reads constant.
        # Skip if real token path is owned by user (don't clobber).
        from importlib import util

        spec = util.spec_from_file_location("bug", str(BROWSER_GUARD))
        mod = util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        fake_token = tmp_path / "owner_confirm.token"
        fake_token.write_text("super-secret-token", encoding="utf-8")
        monkeypatch.setattr(mod, "TOKEN_PATH", fake_token)
        rc = mod.main(
            [
                "--url",
                "https://chase.com/dashboard",
                "--owner-confirm-token",
                "super-secret-token",
            ]
        )
        assert rc == 0

    def test_token_invalid_still_confirms(self, tmp_path, monkeypatch) -> None:
        from importlib import util

        spec = util.spec_from_file_location("bug2", str(BROWSER_GUARD))
        mod = util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        fake_token = tmp_path / "owner_confirm.token"
        fake_token.write_text("real-token", encoding="utf-8")
        monkeypatch.setattr(mod, "TOKEN_PATH", fake_token)
        rc = mod.main(
            [
                "--url",
                "https://chase.com/dashboard",
                "--owner-confirm-token",
                "wrong-token",
            ]
        )
        assert rc == 79


# =====================================================================
# Module-level classify() unit tests (fast)
# =====================================================================
class TestClassifyUnit:
    @pytest.fixture(autouse=True)
    def _load(self):
        from importlib import util

        spec = util.spec_from_file_location("bug3", str(BROWSER_GUARD))
        mod = util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        self.classify = mod.classify

    def test_classify_allow(self) -> None:
        v, _ = self.classify("https://example.com/page")
        assert v == "ALLOW"

    def test_classify_block_paypal_send(self) -> None:
        v, _ = self.classify("https://www.paypal.com/sendmoney/foo")
        assert v == "BLOCK"

    def test_classify_confirm_paypal_root(self) -> None:
        v, _ = self.classify("https://paypal.com/")
        assert v == "CONFIRM"

    def test_classify_block_transfer_path(self) -> None:
        v, _ = self.classify("https://anybank.tld/transfer/now")
        assert v == "BLOCK"

    def test_classify_block_empty(self) -> None:
        v, _ = self.classify("")
        assert v == "BLOCK"
