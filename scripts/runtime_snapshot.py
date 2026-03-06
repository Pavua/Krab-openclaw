#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Runtime Snapshot Utility (восстановлено из pre-refactor R24).

Зачем нужен скрипт:
- быстро собрать единый снимок runtime-состояния Krab/OpenClaw;
- получить машиночитаемый JSON для handoff/диагностики без запуска полного bundle;
- держать проверку "живых" endpoint'ов в одном месте.

Связь с проектом:
- использует web runtime endpoints из `src/modules/web_app.py`;
- дополняет `scripts/export_handoff_bundle.py` как лёгкий standalone-инструмент.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request


def _resolve_base_url() -> str:
    """Возвращает базовый URL web runtime с учётом env-переопределений."""
    override = str(os.getenv("KRAB_SMOKE_BASE_URL", "") or "").strip()
    if override:
        return override.rstrip("/")
    host = str(os.getenv("WEB_HOST", "127.0.0.1") or "127.0.0.1").strip()
    port = str(os.getenv("WEB_PORT", "8080") or "8080").strip()
    return f"http://{host}:{port}"


def _fetch_json(url: str, timeout_sec: float = 10.0) -> tuple[dict[str, Any], str | None, int | None]:
    """Безопасно запрашивает JSON endpoint и возвращает (payload, error, status_code)."""
    req = request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
            status_code = int(getattr(resp, "status", 200))
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body), None, status_code
    except error.HTTPError as exc:
        status = int(getattr(exc, "code", 0) or 0) or None
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            raw = str(exc)
        return {}, f"http_error: {status}; body={raw[:500]}", status
    except (error.URLError, TimeoutError, ValueError) as exc:
        return {}, str(exc), None


def build_snapshot(base_url: str) -> dict[str, Any]:
    """Собирает снимок runtime endpoint'ов в единый JSON."""
    targets = [
        ("/api/health/lite", "health_lite"),
        ("/api/openclaw/channels/status", "channels_status"),
        ("/api/openclaw/browser-smoke", "browser_smoke"),
        ("/api/openclaw/photo-smoke", "photo_smoke"),
        ("/api/openclaw/control-compat/status", "control_compat"),
        ("/api/ecosystem/health", "ecosystem_health"),
    ]

    endpoints: dict[str, Any] = {}
    for path, key in targets:
        payload, err, status_code = _fetch_json(f"{base_url.rstrip('/')}{path}")
        endpoints[key] = {
            "ok": err is None,
            "status_code": status_code,
            "error": err,
            "payload": payload,
        }

    all_ok = all(bool(item.get("ok")) for item in endpoints.values())
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "ok": all_ok,
        "endpoints": endpoints,
    }


def main() -> int:
    base_url = _resolve_base_url()
    out_path = Path("temp/runtime_snapshot.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"📸 Сбор runtime snapshot: {base_url}")
    snapshot = build_snapshot(base_url)
    text = json.dumps(snapshot, ensure_ascii=False, indent=2)
    out_path.write_text(text + "\n", encoding="utf-8")
    print(f"✅ Снимок сохранён: {out_path}")
    return 0 if bool(snapshot.get("ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())

