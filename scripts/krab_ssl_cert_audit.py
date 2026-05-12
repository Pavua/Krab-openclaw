#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/krab_ssl_cert_audit.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Wave 130: weekly SSL cert expiry probe.

Подключается к списку важных hosts (Anthropic / Google / Sentry / etc.),
получает peer cert через TLS handshake, парсит notAfter и считает
days_until_expiry. Результат:
- JSON report в ~/.openclaw/krab_runtime_state/ssl_cert_audit.json (last 10 runs)
- Prometheus Gauge krab_ssl_cert_days_remaining{host}
- Alert SSLCertExpiringSoon при <14 days за 1h.

LaunchAgent: scripts/launchagents/ai.krab.ssl-cert-audit.plist (Thu 07:00).

ENV:
- KRAB_SSL_AUDIT_HOSTS — CSV списка hosts (default ниже).
- KRAB_SSL_AUDIT_PORT — port (default 443).
- KRAB_SSL_AUDIT_TIMEOUT — TLS handshake timeout sec (default 10).
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Default hosts — критичные эндпоинты, от которых зависит Krab runtime.
DEFAULT_HOSTS = (
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "sentry.io",
)

DEFAULT_STATE_DIR = Path(os.path.expanduser("~/.openclaw/krab_runtime_state"))
DEFAULT_REPORT_PATH = DEFAULT_STATE_DIR / "ssl_cert_audit.json"
MAX_HISTORY = 10

# Формат notAfter в peer cert: "May 12 11:22:33 2026 GMT".
_NOT_AFTER_FMT = "%b %d %H:%M:%S %Y %Z"


def _parse_not_after(raw: str) -> datetime:
    """Парсит peer cert notAfter в aware datetime (UTC)."""
    dt = datetime.strptime(raw, _NOT_AFTER_FMT)
    return dt.replace(tzinfo=timezone.utc)


def _compute_days_until(expiry: datetime, now: datetime | None = None) -> float:
    """Вернуть кол-во дней (float) от now до expiry. Может быть отрицательным."""
    base = now or datetime.now(timezone.utc)
    delta = expiry - base
    return delta.total_seconds() / 86400.0


def fetch_peer_cert(host: str, port: int = 443, timeout: float = 10.0) -> dict[str, Any]:
    """TLS handshake и получение peer cert.

    Возвращает peer cert dict (как ssl.SSLSocket.getpeercert()).
    Бросает OSError/ssl.SSLError при сбое.
    """
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as raw_sock:
        with ctx.wrap_socket(raw_sock, server_hostname=host) as tls_sock:
            cert = tls_sock.getpeercert()
    return cert or {}


def probe_host(
    host: str, port: int = 443, timeout: float = 10.0, now: datetime | None = None
) -> dict[str, Any]:
    """Опросить одного хоста, вернуть структуру результата.

    Никогда не бросает — все ошибки оборачиваются в {error: ...}.
    """
    entry: dict[str, Any] = {"host": host, "port": port}
    try:
        cert = fetch_peer_cert(host, port=port, timeout=timeout)
        not_after_raw = cert.get("notAfter")
        if not not_after_raw:
            entry["error"] = "no notAfter in peer cert"
            return entry
        expiry = _parse_not_after(not_after_raw)
        days = _compute_days_until(expiry, now=now)
        entry["not_after"] = not_after_raw
        entry["expiry_iso"] = expiry.isoformat()
        entry["days_until_expiry"] = round(days, 2)
        entry["expired"] = days < 0
    except (OSError, ssl.SSLError, ValueError) as exc:
        entry["error"] = f"{type(exc).__name__}: {exc}"
    return entry


def _resolve_hosts() -> list[str]:
    raw = os.environ.get("KRAB_SSL_AUDIT_HOSTS", "").strip()
    if not raw:
        return list(DEFAULT_HOSTS)
    items = [h.strip() for h in raw.split(",") if h.strip()]
    return items or list(DEFAULT_HOSTS)


def _resolve_port() -> int:
    try:
        return int(os.environ.get("KRAB_SSL_AUDIT_PORT", "443"))
    except ValueError:
        return 443


def _resolve_timeout() -> float:
    try:
        return float(os.environ.get("KRAB_SSL_AUDIT_TIMEOUT", "10"))
    except ValueError:
        return 10.0


def run_audit(
    hosts: list[str] | None = None,
    port: int | None = None,
    timeout: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Сформировать снапшот audit'а по списку hosts."""
    targets = hosts if hosts is not None else _resolve_hosts()
    p = port if port is not None else _resolve_port()
    t = timeout if timeout is not None else _resolve_timeout()
    ts = (now or datetime.now(timezone.utc)).isoformat()

    results: list[dict[str, Any]] = []
    for h in targets:
        results.append(probe_host(h, port=p, timeout=t, now=now))

    # Обновляем Prometheus gauge без падения если модуль не подгружается.
    try:
        from src.core.metrics.ssl_audit import record_cert_days

        for entry in results:
            if "days_until_expiry" in entry:
                record_cert_days(
                    host=entry["host"], days_until_expiry=float(entry["days_until_expiry"])
                )
    except Exception:  # noqa: BLE001 - metrics opt-in
        pass

    return {"timestamp": ts, "hosts": results}


def persist_report(
    report: dict[str, Any], path: Path = DEFAULT_REPORT_PATH, max_history: int = MAX_HISTORY
) -> None:
    """Дописать report в history JSON, обрезать до last N runs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    history: list[dict[str, Any]] = []
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("history"), list):
                history = raw["history"]
        except (json.JSONDecodeError, OSError):
            history = []
    history.append(report)
    history = history[-max_history:]
    path.write_text(
        json.dumps({"history": history}, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    report = run_audit()
    persist_report(report)
    # stdout — компактный JSON для launchd log.
    print(json.dumps(report, ensure_ascii=False))
    # Exit-code: 1 если есть expired host или <14 days; иначе 0.
    warn = False
    for entry in report["hosts"]:
        days = entry.get("days_until_expiry")
        if entry.get("expired"):
            warn = True
            break
        if isinstance(days, (int, float)) and days < 14:
            warn = True
            break
    return 1 if warn else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
