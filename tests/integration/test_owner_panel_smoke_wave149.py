# -*- coding: utf-8 -*-
"""Wave 149: smoke test для всех owner panel GET endpoints.

Цель — ловить regression 5xx раньше Sentry alert: иногда endpoint падает
после refactor, и мы узнаём только когда кто-то его дёрнул.

Поведение:
  - Skip если Krab не запущен (curl /api/health не отвечает).
  - Тянем live список endpoints через GET /api/endpoints.
  - Для каждого GET без path params:
      * Пропускаем dangerous patterns (delete/restart/shutdown/kill/logout).
      * Пропускаем streaming/SSE endpoints (stream/sse/subscribe/events).
      * Дёргаем endpoint, assert status < 500.
      * Таймаут 15s — медленные endpoints (cloud calls) ловим отдельно.
  - Если есть 5xx — fail с summary всех проблем.
  - Slow endpoints (>2s) reported как warning, не как failure.

Run: `pytest tests/integration/test_owner_panel_smoke_wave149.py -q -m live`.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

OWNER_PANEL_BASE = "http://127.0.0.1:8080"
HEALTH_URL = f"{OWNER_PANEL_BASE}/api/health"
ENDPOINTS_URL = f"{OWNER_PANEL_BASE}/api/endpoints"

# Per-request timeout — большой для cloud-backed endpoints (model catalog,
# memory doctor и т.п. могут вызывать LM Studio / OpenClaw синхронно).
REQUEST_TIMEOUT_SEC = 15.0

# Порог для slow warning (не failure).
SLOW_WARN_SEC = 2.0

# Dangerous keywords — пропускаем чтобы не сломать runtime.
# NB: "executive-summary" / "auto-executor/status" содержат слова из stop-list
# но это read-only status; для строгой защиты используем точные substrings.
DANGEROUS_PATTERNS = (
    "/delete",
    "/restart",
    "/shutdown",
    "/kill",
    "/logout",
    "/purge",
    "/drop",
    "/flush",
)

# Streaming / SSE endpoints — блокирующие, мы не хотим их в smoke.
STREAMING_PATTERNS = (
    "/stream",
    "/sse",
    "/subscribe",
    "/events",  # /api/inbox/events, /api/swarm/events
)


def _krab_alive() -> bool:
    """True если /api/health отвечает 200 в пределах 5s."""
    try:
        req = urllib.request.Request(HEALTH_URL, method="GET")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def _fetch_endpoints() -> list[dict[str, str]]:
    """Тянет live список endpoints через GET /api/endpoints.

    Возвращает [{"method": "GET", "path": "/..."}].
    """
    req = urllib.request.Request(ENDPOINTS_URL, method="GET")
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        payload = json.load(resp)
    endpoints = payload.get("endpoints", [])
    assert isinstance(endpoints, list) and endpoints, "Endpoints list пуст"
    return endpoints


def _is_testable_get(endpoint: dict[str, str]) -> bool:
    """Можно ли smoke-тестировать этот endpoint."""
    if endpoint.get("method") != "GET":
        return False
    path = endpoint.get("path", "")
    if not path:
        return False
    # Path parameters требуют fixture data — skip.
    if "{" in path:
        return False
    path_lower = path.lower()
    # Stop-list по dangerous и streaming substrings.
    if any(p in path_lower for p in DANGEROUS_PATTERNS):
        return False
    if any(p in path_lower for p in STREAMING_PATTERNS):
        return False
    return True


def _probe(path: str) -> tuple[Any, float]:
    """Дёргает GET endpoint, возвращает (status_or_error, duration_sec).

    status: int (HTTP code) или строка "ERR:<ExceptionType>".
    """
    url = f"{OWNER_PANEL_BASE}{path}"
    ts = time.time()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
            status: Any = resp.status
    except urllib.error.HTTPError as exc:
        # HTTPError несёт код (400/403/404/500) — это валидный response, не network fail.
        status = exc.code
    except Exception as exc:
        # Network timeout, connection refused — отдельный bucket.
        status = f"ERR:{type(exc).__name__}"
    duration = time.time() - ts
    return status, duration


@pytest.mark.live
def test_owner_panel_smoke_all_safe_gets() -> None:
    """Hit every safe GET endpoint, fail если есть 5xx.

    Slow (>2s) и timeout endpoints не считаются failures, но логируются для visibility.
    """
    if not _krab_alive():
        pytest.skip("Krab owner panel не запущен на :8080")

    endpoints = _fetch_endpoints()
    testable = [e for e in endpoints if _is_testable_get(e)]
    assert testable, "Не нашли testable GET endpoints"

    results: list[tuple[str, Any, float]] = []
    for ep in testable:
        path = ep["path"]
        status, duration = _probe(path)
        results.append((path, status, duration))

    # Categorize results.
    ok_200_range: list[tuple[str, int, float]] = []
    client_errors: list[tuple[str, int, float]] = []  # 4xx — OK для smoke.
    server_errors: list[tuple[str, int, float]] = []  # 5xx — fail.
    network_errors: list[tuple[str, str, float]] = []  # ERR:* — отдельно.
    slow: list[tuple[str, Any, float]] = []

    for path, status, duration in results:
        if duration > SLOW_WARN_SEC:
            slow.append((path, status, duration))
        if isinstance(status, int):
            if 200 <= status < 400:
                ok_200_range.append((path, status, duration))
            elif 400 <= status < 500:
                client_errors.append((path, status, duration))
            else:
                server_errors.append((path, status, duration))
        else:
            network_errors.append((path, str(status), duration))

    # Summary в test output (capsys / stdout for -s flag).
    print()
    print(f"=== Wave 149 smoke results: {len(testable)} endpoints probed ===")
    print(f"  2xx/3xx OK:     {len(ok_200_range)}")
    print(f"  4xx client:     {len(client_errors)}")
    print(f"  5xx server:     {len(server_errors)}")
    print(f"  network errors: {len(network_errors)}")
    print(f"  slow (>{SLOW_WARN_SEC}s): {len(slow)}")
    if client_errors:
        print("  4xx breakdown:")
        for path, status, _ in client_errors[:20]:
            print(f"    {status}  {path}")
    if server_errors:
        print("  5xx breakdown (FAIL):")
        for path, status, _ in server_errors:
            print(f"    {status}  {path}")
    if network_errors:
        print("  network errors (not counted as fail):")
        for path, err, _ in network_errors[:10]:
            print(f"    {err}  {path}")
    if slow:
        print("  slow endpoints (>2s, top 10):")
        for path, status, duration in sorted(slow, key=lambda x: -x[2])[:10]:
            print(f"    {duration:5.2f}s  {status}  {path}")

    # Hard assertion: no 5xx.
    if server_errors:
        details = "\n".join(f"  {code}  {path}" for path, code, _ in server_errors)
        pytest.fail(f"{len(server_errors)} endpoint(s) вернули 5xx:\n{details}")


@pytest.mark.live
def test_owner_panel_endpoints_list_reachable() -> None:
    """Sanity: /api/endpoints отвечает и список не пустой."""
    if not _krab_alive():
        pytest.skip("Krab owner panel не запущен на :8080")

    endpoints = _fetch_endpoints()
    assert len(endpoints) > 100, f"Endpoints list подозрительно мал: {len(endpoints)}"
    gets = [e for e in endpoints if e.get("method") == "GET"]
    assert len(gets) > 50, f"GET endpoints подозрительно мало: {len(gets)}"
