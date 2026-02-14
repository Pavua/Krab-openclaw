#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unified Health Dashboard (Phase G).

Проверяет доступность основных сервисов 3-проектной экосистемы:
- OpenClaw Gateway (cloud brain)
- LM Studio local endpoint (local fallback)
- Krab Voice Gateway
- Krab Ear backend (опционально)

Выводит итоговый уровень деградации цепочки `cloud -> local fallback`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin
from urllib.request import Request, urlopen


@dataclass
class CheckResult:
    name: str
    ok: bool
    status: str
    latency_ms: Optional[int] = None


def _check_json(url: str, timeout_sec: int = 3) -> CheckResult:
    started = datetime.now(timezone.utc)
    try:
        request = Request(url, headers={"User-Agent": "KrabHealth/1.0"})
        with urlopen(request, timeout=timeout_sec) as response:
            status_code = int(getattr(response, "status", 0))
        latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        if status_code != 200:
            return CheckResult(name=url, ok=False, status=f"HTTP {status_code}", latency_ms=latency)
        return CheckResult(name=url, ok=True, status="OK", latency_ms=latency)
    except Exception as exc:
        latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return CheckResult(name=url, ok=False, status=str(exc), latency_ms=latency)


def _normalize_lm_url(raw_url: str) -> str:
    base = (raw_url or "http://127.0.0.1:1234").rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/models"
    return f"{base}/v1/models"


def main() -> int:
    openclaw_base = os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789").rstrip("/")
    voice_base = os.getenv("VOICE_GATEWAY_URL", "http://127.0.0.1:8090").rstrip("/")
    lm_studio_url = os.getenv("LM_STUDIO_URL", "http://127.0.0.1:1234")
    krab_ear_base = os.getenv("KRAB_EAR_BACKEND_URL", "http://127.0.0.1:8765").rstrip("/")

    checks = {
        "openclaw": _check_json(urljoin(openclaw_base + "/", "health")),
        "local_lm": _check_json(_normalize_lm_url(lm_studio_url)),
        "voice_gateway": _check_json(urljoin(voice_base + "/", "health")),
        "krab_ear": _check_json(urljoin(krab_ear_base + "/", "health")),
    }

    cloud_ok = checks["openclaw"].ok
    local_ok = checks["local_lm"].ok

    if cloud_ok:
        degradation = "normal"
    elif local_ok:
        degradation = "degraded_to_local_fallback"
    else:
        degradation = "critical_no_ai_backend"

    payload = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "degradation": degradation,
        "checks": {
            key: {
                "ok": value.ok,
                "status": value.status,
                "latency_ms": value.latency_ms,
            }
            for key, value in checks.items()
        },
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
