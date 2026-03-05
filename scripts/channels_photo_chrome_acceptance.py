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
import subprocess
import time
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


def _fetch_json_with_retries(
    url: str,
    *,
    timeout_sec: float = 10.0,
    attempts: int = 1,
) -> tuple[dict[str, Any], str | None]:
    """Повторяет запрос endpoint'а, чтобы сгладить кратковременные timeout-скачки."""
    safe_attempts = max(1, int(attempts))
    last_payload: dict[str, Any] = {}
    last_error: str | None = None
    for _ in range(safe_attempts):
        payload, err = _fetch_json(url, timeout_sec=timeout_sec)
        if err is None:
            return payload, None
        last_payload = payload
        last_error = err
    return last_payload, last_error


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


def _is_transient_disconnect_failure(entry: dict[str, Any]) -> bool:
    """
    Определяет кратковременный канал-флап, который обычно лечится health-monitor.

    Признак: канал в failed и meta содержит `disconnected`, но это не permanent `not configured`.
    """
    meta = str(entry.get("meta") or "").lower()
    if "not configured" in meta:
        return False
    return "disconnected" in meta


def _fetch_stable_channels_payload(
    url: str,
    *,
    timeout_sec: float = 14.0,
    attempts: int = 3,
    settle_attempts: int = 2,
    settle_delay_sec: float = 1.2,
) -> tuple[dict[str, Any], str | None]:
    """
    Забирает channels/status и сглаживает короткие флап-окна reconnect.

    Логика:
    - сначала обычные HTTP retries;
    - если endpoint успешен, но есть только `disconnected` фейлы — делаем
      короткие повторные пробы перед тем, как считать acceptance красным.
    """
    payload, err = _fetch_json_with_retries(url, timeout_sec=timeout_sec, attempts=attempts)
    if err is not None:
        return payload, err

    current_payload = payload
    current_summary = _classify_channels(current_payload)
    for _ in range(max(0, int(settle_attempts))):
        failed = current_summary.get("failed") or []
        if not failed:
            return current_payload, None
        if not all(_is_transient_disconnect_failure(item) for item in failed):
            return current_payload, None
        time.sleep(max(0.0, float(settle_delay_sec)))
        next_payload, next_err = _fetch_json_with_retries(url, timeout_sec=timeout_sec, attempts=1)
        if next_err is not None:
            return current_payload, None
        current_payload = next_payload
        current_summary = _classify_channels(current_payload)

    return current_payload, None


def _run_cli_json(cmd: list[str], timeout_sec: float = 20.0) -> tuple[dict[str, Any], str | None]:
    """Запускает CLI-команду и пытается прочитать JSON-ответ."""
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except Exception as exc:  # noqa: BLE001
        return {}, str(exc)

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return {}, stderr or stdout or f"exit_code={proc.returncode}"

    if not stdout:
        return {}, None

    try:
        payload = json.loads(stdout)
        return payload if isinstance(payload, dict) else {"raw": payload}, None
    except ValueError:
        return {}, f"invalid_json_output: {stdout[:300]}"


def _run_browser_action_probe(url: str = "https://example.com") -> dict[str, Any]:
    """
    Практический flow-check Chrome relay через CLI.

    Контур:
    1) `openclaw browser status --json`
    2) `openclaw browser tabs --json`
    3) при наличии вкладки: `navigate` + `snapshot`
    4) при отсутствии вкладки: явная explainable-деградация (tab_not_connected)
    """
    status_payload, status_err = _run_cli_json(["openclaw", "browser", "--json", "status"], timeout_sec=12.0)
    tabs_payload, tabs_err = _run_cli_json(["openclaw", "browser", "--json", "tabs"], timeout_sec=12.0)

    tabs = tabs_payload.get("tabs") if isinstance(tabs_payload, dict) else []
    if not isinstance(tabs, list):
        tabs = []

    if status_err:
        return {
            "ok": False,
            "state": "status_error",
            "blocking": True,
            "detail": status_err,
            "tabs_count": len(tabs),
        }
    if tabs_err:
        return {
            "ok": False,
            "state": "tabs_error",
            "blocking": True,
            "detail": tabs_err,
            "tabs_count": len(tabs),
        }

    if not tabs:
        _, open_err = _run_cli_json(["openclaw", "browser", "--json", "open", url], timeout_sec=15.0)
        low = str(open_err or "").lower()
        if "no tab is connected" in low:
            return {
                "ok": False,
                "state": "tab_not_connected",
                "blocking": False,
                "detail": "Chrome relay ожидает attach вкладки через расширение OpenClaw.",
                "tabs_count": 0,
                "attach_hint": "Открой вкладку в Chrome и нажми иконку расширения OpenClaw для attach.",
            }
        return {
            "ok": False,
            "state": "open_failed",
            "blocking": True,
            "detail": open_err or "open_failed_unknown",
            "tabs_count": 0,
        }

    _, nav_err = _run_cli_json(["openclaw", "browser", "--json", "navigate", url], timeout_sec=20.0)
    if nav_err:
        return {
            "ok": False,
            "state": "navigate_failed",
            "blocking": True,
            "detail": nav_err,
            "tabs_count": len(tabs),
        }

    snapshot_payload, snapshot_err = _run_cli_json(
        ["openclaw", "browser", "--json", "snapshot", "--format", "aria", "--limit", "60"],
        timeout_sec=20.0,
    )
    if snapshot_err:
        low = str(snapshot_err).lower()
        # На macOS/Chrome в некоторых режимах CDP может быть ограничен политикой
        # безопасности браузера. Это не означает, что relay недоступен: маршрут
        # может быть "gateway reachable + auth required". Для readiness считаем
        # это explainable-деградацией, а не жёстким блокером.
        if "target.attachtobrowsertarget" in low and "not allowed" in low:
            return {
                "ok": False,
                "state": "snapshot_auth_not_allowed",
                "blocking": False,
                "detail": "CDP snapshot недоступен (Not allowed). Нужна авторизация/attach в Chrome relay.",
                "tabs_count": len(tabs),
            }
        return {
            "ok": False,
            "state": "snapshot_failed",
            "blocking": True,
            "detail": snapshot_err,
            "tabs_count": len(tabs),
        }

    snapshot_keys = sorted(snapshot_payload.keys()) if isinstance(snapshot_payload, dict) else []
    return {
        "ok": True,
        "state": "ok",
        "blocking": False,
        "detail": "Browser action probe выполнен: navigate + snapshot.",
        "tabs_count": len(tabs),
        "snapshot_keys": snapshot_keys[:10],
    }


def build_report(
    base_url: str,
    *,
    include_browser_action: bool = True,
    strict_browser_action: bool = False,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    health, health_err = _fetch_json_with_retries(
        f"{base}/api/health/lite",
        timeout_sec=10.0,
        attempts=2,
    )
    channels_payload, channels_err = _fetch_stable_channels_payload(
        f"{base}/api/openclaw/channels/status",
        timeout_sec=14.0,
        attempts=3,
    )
    browser_payload, browser_err = _fetch_json_with_retries(
        f"{base}/api/openclaw/browser-smoke",
        timeout_sec=12.0,
        attempts=2,
    )
    photo_payload, photo_err = _fetch_json_with_retries(
        f"{base}/api/openclaw/photo-smoke",
        timeout_sec=12.0,
        attempts=3,
    )
    compat_payload, compat_err = _fetch_json_with_retries(
        f"{base}/api/openclaw/control-compat/status",
        timeout_sec=12.0,
        attempts=2,
    )

    channels = _classify_channels(channels_payload)

    browser_smoke = ((browser_payload.get("report") or {}).get("browser_smoke") or {}) if isinstance(browser_payload, dict) else {}
    photo_smoke = ((photo_payload.get("report") or {}).get("photo_smoke") or {}) if isinstance(photo_payload, dict) else {}
    browser_action = (
        _run_browser_action_probe()
        if include_browser_action
        else {
            "ok": True,
            "state": "skipped",
            "blocking": False,
            "detail": "browser action probe skipped",
            "tabs_count": None,
        }
    )

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
        "browser_action_ready": (
            bool(browser_action.get("ok"))
            if strict_browser_action
            else (bool(browser_action.get("ok")) or not bool(browser_action.get("blocking", True)))
        ),
    }

    ok = all(bool(v) for v in checks.values())

    warnings: list[str] = []
    browser_state = str(browser_smoke.get("browser_http_state") or "")
    if browser_state == "auth_required":
        warnings.append("Chrome relay требует авторизацию (browser_http_state=auth_required). Это не блокер readiness.")
    if str(browser_action.get("state") or "") == "tab_not_connected":
        warnings.append(
            "Chrome relay: вкладка не подключена к расширению OpenClaw (tab_not_connected). "
            "Для полного action-flow подключи вкладку и повтори acceptance."
        )
    if str(browser_action.get("state") or "") == "snapshot_auth_not_allowed":
        warnings.append(
            "Chrome relay: snapshot через CDP отклонён (Not allowed). "
            "Для полного action-flow авторизуй relay в Chrome и повтори acceptance."
        )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "inputs": {"base_url": base},
        "health_lite": {"error": health_err, "payload": health},
        "channels": {"error": channels_err, "payload": channels_payload, "summary": channels},
        "browser_smoke": {"error": browser_err, "payload": browser_payload, "summary": browser_smoke},
        "browser_action": browser_action,
        "photo_smoke": {"error": photo_err, "payload": photo_payload, "summary": photo_smoke},
        "control_compat": {"error": compat_err, "payload": compat_payload},
        "checks": checks,
        "warnings": warnings,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Acceptance channels + photo + Chrome relay")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--skip-browser-action",
        action="store_true",
        help="Пропустить реальный browser action probe (navigate/snapshot).",
    )
    parser.add_argument(
        "--strict-browser-action",
        action="store_true",
        help="Считать acceptance fail, если browser action probe не выполнен успешно.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = build_report(
        args.base_url,
        include_browser_action=not bool(args.skip_browser_action),
        strict_browser_action=bool(args.strict_browser_action),
    )
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
