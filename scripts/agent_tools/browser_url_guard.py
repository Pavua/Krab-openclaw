#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wave 44-T-money-safety: pre-navigation URL guard для browser-tool.

Вызывается ПЕРЕД любым browser open/click/navigate. Возвращает JSON +
exit-code:
  0  — safe, можно открывать
  78 — BLOCK (financial txn / known-malicious / sandbox-violating)
  79 — NEEDS_OWNER_CONFIRM (read-only финансовый домен; подтверждение нужно)

Usage:
  python browser_url_guard.py --url "https://google.com"            # exit 0
  python browser_url_guard.py --url "https://paypal.com/transfer"   # exit 78
  python browser_url_guard.py --url "https://chase.com/dashboard" \\
      --owner-confirm-token <T>                                     # exit 0/79

Token bypass: --owner-confirm-token; stored at
/Users/pablito/.openclaw/krab_runtime_state/owner_confirm.token
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

TOKEN_PATH = Path("/Users/pablito/.openclaw/krab_runtime_state/owner_confirm.token")

# Hard-block: payment processors + bank txn paths + crypto write ops.
_BLOCK_DOMAIN_PATH_PATTERNS: tuple[tuple[str, str], ...] = (
    # payment processors — transactional paths
    (r"(?:^|\.)paypal\.com$", r"^/(?:send|sendmoney|cgi-bin|checkout|donate)"),
    (r"(?:^|\.)venmo\.com$", r"^/(?:payment|pay)"),
    (r"(?:^|\.)wise\.com$", r"/transfer"),
    (r"(?:^|\.)revolut\.com$", r"/transfer"),
    (r"(?:^|\.)cash\.app$", r"^/\$"),
    (r"(?:^|\.)zellepay\.com$", r"/send"),
    (
        r"(?:^|\.)stripe\.com$",
        r"^/v1/(?:charges|payment_intents|transfers)",
    ),
    # crypto write ops
    (r"(?:^|\.)binance\.com$", r"/api/.*order"),
    (r"(?:^|\.)coinbase\.com$", r"^/(?:buy|sell|send)"),
    (r"(?:^|\.)kraken\.com$", r"(?:AddOrder|Withdraw)"),
    (r"(?:^|\.)bybit\.com$", r"order"),
    (r"(?:^|\.)okx\.com$", r"^/api/v5/trade/order"),
    # gov/tax
    (r"(?:^|\.)irs\.gov$", r"payment"),
    (r"(?:^|\.)gov\.uk$", r"^/pay"),
    (r"(?:^|\.)agenciatributaria\.es$", r"pago"),
)

# Generic transactional path keywords on ANY domain — block.
_BLOCK_PATH_KEYWORDS = (
    "/wire-transfer",
    "/sendmoney",
    "/billpay",
    "/checkout/pay",
    "/tax-return-submit",
)

# Financial domains where READ-only access requires owner confirm.
_CONFIRM_FINANCIAL_DOMAINS = (
    "paypal.com",
    "venmo.com",
    "wise.com",
    "revolut.com",
    "stripe.com",
    "cash.app",
    "zellepay.com",
    "chase.com",
    "bankofamerica.com",
    "citibank.com",
    "wellsfargo.com",
    "hsbc.com",
    "barclays.co.uk",
    "santander.com",
    "bbva.com",
    "caixabank.es",
    "sabadell.com",
    "ing.com",
    "deutsche-bank.de",
    "binance.com",
    "coinbase.com",
    "kraken.com",
    "bybit.com",
    "okx.com",
)


def _domain_matches(host: str, suffix: str) -> bool:
    host = host.lower()
    suffix = suffix.lower()
    return host == suffix or host.endswith("." + suffix)


def classify(url: str) -> tuple[str, str]:
    """Return (verdict, reason). verdict ∈ {ALLOW, BLOCK, CONFIRM}."""
    if not url:
        return "BLOCK", "empty url"
    try:
        parsed = urlparse(url)
    except Exception as e:  # noqa: BLE001
        return "BLOCK", f"unparseable url: {e!s}"

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        # file://, javascript:, data: — block
        if scheme in ("javascript", "data", "file", "ftp"):
            return "BLOCK", f"scheme {scheme!r} blocked"
        # Treat as confirm (e.g. about:, chrome:)
        return "CONFIRM", f"non-http scheme {scheme!r}"

    host = (parsed.hostname or "").lower()
    path = parsed.path or ""

    # 1. Hard-block transactional patterns
    for dom_re, path_re in _BLOCK_DOMAIN_PATH_PATTERNS:
        if re.search(dom_re, host) and re.search(path_re, path):
            return "BLOCK", f"transactional: {host}{path}"

    # 2. Generic txn path keywords
    low_path = path.lower()
    for kw in _BLOCK_PATH_KEYWORDS:
        if kw in low_path:
            return "BLOCK", f"txn keyword in path: {kw}"

    # 3. /transfer, /pay, /wire on any domain — block (covers most banks)
    if re.search(r"(?:^|/)(transfer|pay|wire)(?:/|$)", low_path):
        return "BLOCK", f"transactional path: {path}"

    # 4. Financial domain read-only — require confirm
    for fin in _CONFIRM_FINANCIAL_DOMAINS:
        if _domain_matches(host, fin):
            return "CONFIRM", f"financial domain read: {host}"

    return "ALLOW", "ok"


def _verify_token(token: str | None) -> bool:
    if not token:
        return False
    if not TOKEN_PATH.is_file():
        return False
    try:
        stored = TOKEN_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return bool(stored) and stored == token.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="browser url guard (Wave 44-T)")
    parser.add_argument("--url", required=True, help="URL to check")
    parser.add_argument("--owner-confirm-token", default=None, help="bypass token for CONFIRM")
    args = parser.parse_args(argv)

    verdict, reason = classify(args.url)
    payload = {
        "ok": verdict == "ALLOW",
        "verdict": verdict,
        "reason": reason,
        "url": args.url,
    }

    if verdict == "ALLOW":
        print(json.dumps(payload))
        return 0
    if verdict == "BLOCK":
        print(json.dumps(payload), file=sys.stderr)
        return 78
    # CONFIRM
    if _verify_token(args.owner_confirm_token):
        payload["ok"] = True
        payload["verdict"] = "CONFIRM_OK"
        print(json.dumps(payload))
        return 0
    print(json.dumps(payload), file=sys.stderr)
    return 79


if __name__ == "__main__":
    sys.exit(main())
