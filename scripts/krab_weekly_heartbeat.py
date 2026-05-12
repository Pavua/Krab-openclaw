#!/usr/bin/env python3
"""Wave 114: еженедельный heartbeat — DM owner со сводкой за неделю.

Aggregates:
- /metrics (Prometheus) — Sentry, Smart Routing, latency
- /api/network/probes — состояние probes
- /api/costs/budget — бюджет недели
- /api/moderation/audit — последние модерации

Send path: POST /api/notify (owner panel → userbot.client.send_message).
Owner id: OWNER_USER_IDS из ENV или config (берём первый).

Usage:
    python scripts/krab_weekly_heartbeat.py            # send
    python scripts/krab_weekly_heartbeat.py --dry-run  # only print
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError

logger = logging.getLogger("krab.weekly_heartbeat")

PANEL_URL = os.getenv("KRAB_PANEL_URL", "http://127.0.0.1:8080").rstrip("/")
HTTP_TIMEOUT = 8.0


# ── HTTP helpers ───────────────────────────────────────────────────────


def _http_get(path: str, *, timeout: float = HTTP_TIMEOUT) -> tuple[int, str]:
    """GET панель — возвращает (status, body). Сетевая ошибка → (0, "")."""
    url = f"{PANEL_URL}{path}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        return exc.code, ""
    except (URLError, TimeoutError, OSError) as exc:
        logger.warning(
            "heartbeat_http_get_failed path=%s error=%s error_type=%s",
            path,
            exc,
            type(exc).__name__,
        )
        return 0, ""


def _http_post_json(path: str, payload: dict, *, timeout: float = HTTP_TIMEOUT) -> tuple[int, str]:
    """POST JSON в панель."""
    url = f"{PANEL_URL}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace") if exc.fp else ""
    except (URLError, TimeoutError, OSError) as exc:
        logger.warning(
            "heartbeat_http_post_failed path=%s error=%s error_type=%s",
            path,
            exc,
            type(exc).__name__,
        )
        return 0, ""


# ── Owner id ───────────────────────────────────────────────────────────


def resolve_owner_id() -> str | None:
    """Возвращает первый owner_id из KRAB_OWNER_USER_ID / OWNER_USER_IDS."""
    raw = os.getenv("KRAB_OWNER_USER_ID", "").strip()
    if raw:
        return raw
    raw = os.getenv("OWNER_USER_IDS", "").strip()
    if raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if parts:
            return parts[0]
    # Попытка через config (если запуск из проектной директории)
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from src.config import config  # type: ignore[import-not-found]

        ids = getattr(config, "OWNER_USER_IDS", []) or []
        if ids:
            return str(ids[0])
    except Exception:  # noqa: BLE001 — best effort
        pass
    return None


# ── Aggregators ────────────────────────────────────────────────────────

_METRIC_RE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+(?P<val>[-+0-9.eE]+)")


def parse_prom_metrics(body: str) -> dict[str, float]:
    """Парсит Prometheus-text → суммарные значения метрик (без labels).

    Простая агрегация: для каждой метрики суммирует все её labelset-значения.
    """
    out: dict[str, float] = {}
    if not body:
        return out
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _METRIC_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        try:
            val = float(m.group("val"))
        except ValueError:
            continue
        out[name] = out.get(name, 0.0) + val
    return out


def fetch_aggregates() -> dict[str, Any]:
    """Собирает данные из всех endpoint'ов панели. Missing → None gracefully."""
    aggregates: dict[str, Any] = {
        "metrics": {},
        "probes": None,
        "budget": None,
        "moderation": None,
    }

    status, body = _http_get("/metrics")
    if status == 200 and body:
        aggregates["metrics"] = parse_prom_metrics(body)

    status, body = _http_get("/api/network/probes")
    if status == 200 and body:
        try:
            aggregates["probes"] = json.loads(body)
        except json.JSONDecodeError:
            pass

    status, body = _http_get("/api/costs/budget")
    if status == 200 and body:
        try:
            aggregates["budget"] = json.loads(body)
        except json.JSONDecodeError:
            pass

    status, body = _http_get("/api/moderation/audit?limit=10")
    if status == 200 and body:
        try:
            aggregates["moderation"] = json.loads(body)
        except json.JSONDecodeError:
            pass

    return aggregates


# ── Compose ────────────────────────────────────────────────────────────


def _fmt_int(val: Any) -> str:
    try:
        return f"{int(float(val))}"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_eur(val: Any) -> str:
    try:
        return f"€{float(val):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _pick_metric(metrics: dict[str, float], *names: str) -> float | None:
    """Возвращает первое найденное значение из списка имён."""
    for n in names:
        if n in metrics:
            return metrics[n]
    return None


def compose_summary(aggregates: dict[str, Any], *, now: datetime | None = None) -> str:
    """Формирует markdown summary. Missing data → 'n/a' graceful."""
    now = now or datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    metrics = aggregates.get("metrics") or {}

    sentry_total = _pick_metric(
        metrics,
        "krab_sentry_events_total",
        "krab_sentry_events_week_total",
        "krab_sentry_events",
    )
    sr_total = _pick_metric(
        metrics,
        "krab_smart_routing_decisions_total",
        "krab_smart_routing_decisions",
    )
    sr_deny = _pick_metric(
        metrics,
        "krab_smart_routing_deny_total",
        "krab_smart_routing_deny",
    )
    latency_p95 = _pick_metric(
        metrics,
        "krab_llm_latency_p95_seconds",
        "krab_response_latency_p95",
        "krab_latency_p95",
    )

    deny_pct = "n/a"
    if sr_total and sr_deny is not None and sr_total > 0:
        deny_pct = f"{(sr_deny / sr_total) * 100:.0f}%"

    budget = aggregates.get("budget") or {}
    week_eur = budget.get("week_eur") or budget.get("week") or budget.get("spent_week")
    budget_eur = budget.get("budget_eur") or budget.get("budget") or budget.get("limit")

    moderation = aggregates.get("moderation") or {}
    mod_count = moderation.get("count", 0) if isinstance(moderation, dict) else 0

    probes = aggregates.get("probes") or {}
    probes_ok = "n/a"
    if isinstance(probes, dict):
        snapshot = probes.get("probes") or probes
        if isinstance(snapshot, dict) and snapshot:
            healthy = sum(
                1
                for v in snapshot.values()
                if isinstance(v, dict) and v.get("healthy", v.get("ok")) is True
            )
            probes_ok = f"{healthy}/{len(snapshot)}"

    latency_str = f"{latency_p95:.2f}s" if latency_p95 is not None else "n/a"

    lines = [
        f"🦀 Weekly Heartbeat {date_str}",
        "",
        f"Sentry events (7d): {_fmt_int(sentry_total)}",
        f"Smart Routing: {_fmt_int(sr_total)} decisions, {deny_pct} deny",
        f"Cost (week): {_fmt_eur(week_eur)} / {_fmt_eur(budget_eur)} budget",
        f"Moderation actions: {_fmt_int(mod_count)} recent",
        f"Network probes: {probes_ok} healthy",
        f"Latency P95: {latency_str}",
    ]
    return "\n".join(lines)


# ── Send ───────────────────────────────────────────────────────────────


def send_heartbeat(owner_id: str, text: str) -> tuple[bool, str]:
    """Отправляет DM через /api/notify. Возвращает (ok, detail)."""
    status, body = _http_post_json("/api/notify", {"chat_id": owner_id, "text": text})
    if status == 200:
        return True, body
    return False, f"status={status} body={body[:200]}"


# ── Entry ──────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="не отправлять, только напечатать")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    owner_id = resolve_owner_id()
    if not owner_id:
        logger.warning("heartbeat_owner_id_missing")
        print("KRAB_OWNER_USER_ID / OWNER_USER_IDS не заданы — skip", file=sys.stderr)
        return 0  # graceful skip, не падаем в LaunchAgent

    aggregates = fetch_aggregates()
    summary = compose_summary(aggregates)

    if args.dry_run:
        print(summary)
        return 0

    ok, detail = send_heartbeat(owner_id, summary)
    if ok:
        logger.info("heartbeat_sent owner=%s", owner_id)
        return 0
    logger.error("heartbeat_send_failed owner=%s detail=%s", owner_id, detail)
    print(f"send failed: {detail}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
