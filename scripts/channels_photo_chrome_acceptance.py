#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Acceptance-проверка этапа "каналы + фото + Chrome relay".

Назначение:
1) дать единый JSON-отчёт по readiness каналов, photo-route и browser relay;
2) использовать только runtime endpoints как источник истины;
3) отделить acceptance следующего этапа от E1→E3 KPI.

Проверяемые endpoint'ы:
- /api/health/lite
- /api/openclaw/channels/status
- /api/openclaw/browser-smoke
- /api/openclaw/photo-smoke
- /api/openclaw/control-compat/status
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any
from urllib import error, request

DEFAULT_BASE = "http://127.0.0.1:8080"


def _fetch_json(url: str, timeout_sec: float = 10.0) -> tuple[dict[str, Any], str | None]:
    req = request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body), None
    except (error.URLError, error.HTTPError, TimeoutError, ValueError) as exc:
        return {}, str(exc)


def _classify_channels(channels_payload: dict[str, Any]) -> dict[str, Any]:
    channels = channels_payload.get("channels") if isinstance(channels_payload, dict) else []
    if not isinstance(channels, list):
        channels = []

    passed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for item in channels:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "unknown")
        status = str(item.get("status") or "WARN").upper()
        meta = str(item.get("meta") or "")
        entry = {"name": name, "status": status, "meta": meta}
        if status == "OK":
            passed.append(entry)
            continue
        if "not configured" in meta.lower():
            skipped.append(entry)
            continue
        failed.append(entry)

    required_total = len(passed) + len(failed)
    success_rate = 100.0 if required_total == 0 else round((len(passed) / required_total) * 100.0, 2)
    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "required_total": required_total,
        "success_rate": success_rate,
        "gateway_reachable": bool(channels_payload.get("gateway_reachable")),
    }


def build_report(base_url: str) -> dict[str, Any]:
    base = base_url.rstrip("/")
    health, health_err = _fetch_json(f"{base}/api/health/lite")
    channels_payload, channels_err = _fetch_json(f"{base}/api/openclaw/channels/status")
    browser_payload, browser_err = _fetch_json(f"{base}/api/openclaw/browser-smoke")
    photo_payload, photo_err = _fetch_json(f"{base}/api/openclaw/photo-smoke")
    compat_payload, compat_err = _fetch_json(f"{base}/api/openclaw/control-compat/status")

    channels = _classify_channels(channels_payload)

    browser_smoke = ((browser_payload.get("report") or {}).get("browser_smoke") or {}) if isinstance(browser_payload, dict) else {}
    photo_smoke = ((photo_payload.get("report") or {}).get("photo_smoke") or {}) if isinstance(photo_payload, dict) else {}

    checks = {
        "health_up": health_err is None and bool(health.get("ok")),
        "channels_endpoint_ok": channels_err is None,
        "channels_success_ge_95": channels["success_rate"] >= 95.0,
        "channels_failed_zero": len(channels["failed"]) == 0,
        "browser_endpoint_ok": browser_err is None and bool(browser_payload.get("available")),
        "browser_gateway_reachable": bool(browser_smoke.get("gateway_reachable")),
        "browser_http_reachable": bool(browser_smoke.get("browser_http_reachable")),
        "photo_endpoint_ok": photo_err is None and bool(photo_payload.get("available")),
        "photo_ready": bool(photo_smoke.get("ok")),
        "control_compat_ok": compat_err is None and bool(compat_payload.get("runtime_channels_ok")),
        "control_impact_not_runtime_risk": str(compat_payload.get("impact_level") or "") != "runtime_risk",
    }

    ok = all(bool(v) for v in checks.values())

    warnings: list[str] = []
    browser_state = str(browser_smoke.get("browser_http_state") or "")
    if browser_state == "auth_required":
        warnings.append("Chrome relay требует авторизацию (browser_http_state=auth_required). Это не блокер readiness.")

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "inputs": {"base_url": base},
        "health_lite": {"error": health_err, "payload": health},
        "channels": {"error": channels_err, "payload": channels_payload, "summary": channels},
        "browser_smoke": {"error": browser_err, "payload": browser_payload, "summary": browser_smoke},
        "photo_smoke": {"error": photo_err, "payload": photo_payload, "summary": photo_smoke},
        "control_compat": {"error": compat_err, "payload": compat_payload},
        "checks": checks,
        "warnings": warnings,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Acceptance channels + photo + Chrome relay")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = build_report(args.base_url)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)

    if args.output:
        from pathlib import Path

        path = Path(args.output).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")

    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
