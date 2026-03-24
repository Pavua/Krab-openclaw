#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Собирает translator mobile onboarding packet и пишет его в artifacts/ops.

Что делает:
1) Читает live endpoint `/api/translator/mobile/onboarding`.
2) Пишет versioned и latest JSON-артефакты.
3) Даёт простой one-click путь для handoff и работы с другой учётки.

Зачем нужно:
- owner panel не всегда открыта в момент handoff;
- документированный `.command`-путь удобнее для соседней учётки, чем ручной curl;
- attach-ready пакет должен собираться детерминированно и без лишнего UI-ручного шага.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
OPS_DIR = ROOT / "artifacts" / "ops"
ENDPOINT = "http://127.0.0.1:8080/api/translator/mobile/onboarding"


def _http_json(url: str, *, timeout: float = 8.0) -> dict:
    req = Request(url, method="GET", headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - локальный endpoint
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw)


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    OPS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        payload = _http_json(ENDPOINT)
    except (URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"translator_mobile_onboarding_fetch_failed: {exc}")
        return 1

    versioned_path = OPS_DIR / f"translator_mobile_onboarding_{stamp}.json"
    latest_path = OPS_DIR / "translator_mobile_onboarding_latest_cli.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    versioned_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")
    print(f"ok: {versioned_path}")
    print(f"latest: {latest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
