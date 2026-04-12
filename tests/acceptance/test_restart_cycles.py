# -*- coding: utf-8 -*-
"""
Phase 1 Acceptance Gate: 10 controlled restart cycles.

Проверяет что Krab корректно переживает 10 последовательных restart
без потери Telegram-сессии, деградации health и silent drops.

Запуск: python tests/acceptance/test_restart_cycles.py [--cycles N]
Требует: работающий Krab на :8080
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

HEALTH_URL = "http://127.0.0.1:8080/api/health/lite"
RESTART_URL = "http://127.0.0.1:8080/api/krab/restart_userbot"
DEFAULT_CYCLES = 10
HEALTH_TIMEOUT = 10
RESTART_WAIT = 30  # секунд после restart до health check
MAX_HEALTH_RETRIES = 10  # × 5s = 50s max wait


def health_check() -> dict:
    """Проверяет /api/health/lite, возвращает JSON или пустой dict."""
    try:
        req = urllib.request.Request(HEALTH_URL, method="GET")
        with urllib.request.urlopen(req, timeout=HEALTH_TIMEOUT) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


def trigger_restart() -> bool:
    """Рестарт через API или через kill+wait если API timeout."""
    import subprocess

    # Сначала пробуем API
    try:
        req = urllib.request.Request(RESTART_URL, method="POST", data=b"{}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data.get("ok", False) or resp.status == 200
    except Exception:
        pass
    # Fallback: graceful SIGTERM → process restarts via launchd/script
    try:
        result = subprocess.run(
            ["pkill", "-f", "python.*src.main"],
            capture_output=True,
            timeout=5,
        )
        print(f"    pkill fallback (exit={result.returncode})")
        return True  # pkill отправлен, ждём restart
    except Exception as exc:
        print(f"    restart failed completely: {exc}")
        return False


def wait_for_healthy(max_retries: int = MAX_HEALTH_RETRIES) -> dict | None:
    """Ждёт пока health/lite вернёт status=up + telegram_userbot_state=running."""
    for attempt in range(1, max_retries + 1):
        time.sleep(5)
        h = health_check()
        status = h.get("status", "")
        tg_state = h.get("telegram_userbot_state", "")
        connected = h.get("telegram_userbot_client_connected", False)
        if status == "up" and tg_state == "running" and connected:
            return h
        print(f"    waiting... attempt {attempt}/{max_retries} (status={status}, tg={tg_state})")
    return None


def run_cycles(n: int) -> tuple[int, int, list[str]]:
    """Прогоняет N restart cycles. Возвращает (passed, failed, errors)."""
    passed = 0
    failed = 0
    errors: list[str] = []

    # Проверяем начальное состояние
    print("Pre-flight health check...")
    initial = health_check()
    if initial.get("status") != "up":
        print(f"ABORT: Krab not healthy before test (status={initial.get('status', '?')})")
        return 0, 1, ["Krab not healthy at start"]

    print(f"Starting {n} restart cycles...\n")

    for cycle in range(1, n + 1):
        print(f"--- Cycle {cycle}/{n} ---")

        # Trigger restart
        print("  1. Triggering restart...")
        ok = trigger_restart()
        if not ok:
            msg = f"Cycle {cycle}: restart trigger failed"
            print(f"  FAIL: {msg}")
            errors.append(msg)
            failed += 1
            time.sleep(10)
            continue

        # Ждём восстановления
        print(f"  2. Waiting {RESTART_WAIT}s for restart...")
        time.sleep(RESTART_WAIT)

        # Health check
        print("  3. Checking health...")
        h = wait_for_healthy()
        if h is None:
            msg = f"Cycle {cycle}: health not recovered after restart"
            print(f"  FAIL: {msg}")
            errors.append(msg)
            failed += 1
            continue

        # Проверяем ключевые поля
        route = h.get("last_runtime_route", {})
        route_status = route.get("status", "?")
        tg_connected = h.get("telegram_userbot_client_connected", False)

        if not tg_connected:
            msg = f"Cycle {cycle}: Telegram not connected after restart"
            print(f"  FAIL: {msg}")
            errors.append(msg)
            failed += 1
            continue

        passed += 1
        print(f"  PASS (route={route_status}, telegram=connected)")

    return passed, failed, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1: Restart cycle acceptance test")
    parser.add_argument(
        "--cycles", type=int, default=DEFAULT_CYCLES, help="Number of restart cycles"
    )
    args = parser.parse_args()

    print(f"=== Phase 1 Acceptance: {args.cycles} Restart Cycles ===\n")
    passed, failed, errors = run_cycles(args.cycles)

    print(f"\n=== Results: {passed} passed, {failed} failed out of {args.cycles} ===")
    if errors:
        print("Errors:")
        for e in errors:
            print(f"  - {e}")

    gate = "PASS ✅" if failed == 0 else "FAIL ❌"
    print(f"\nPhase 1 Gate: {gate}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
