#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wave 34-C + Wave 85: Ежедневная автозачистка inbox в два этапа.

Этап 1 (новый, Wave 85): bulk-ack stale open `proactive_action` items —
переводит их open→acked если они "висят" дольше N часов
(INBOX_CLEANUP_BULK_ACK_HOURS, по умолчанию 12).
Эти acked будут позже подметены janitor-ом proactive-watch → done.

Этап 2 (Wave 34-C): архивация очень старых open items (>= 7d)
из safe-to-cleanup kinds — переводит open→cancelled.

Запускается из LaunchAgent ai.krab.inbox-cleanup через Owner Panel API.
Если panel недоступна — выполняет cleanup напрямую через InboxService.

Выход 0 — успех (включая "нечего чистить").
Выход 1 — ошибка.

ROOT CAUSE (Wave 85): cron с 7-дневным порогом не успевал убирать
свежие, но уже "шумные" proactive_action items (40+ накапливались за
неделю до бывшего срабатывания cron). Bulk-ack step закрывает этот gap.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Настройки по умолчанию
MAX_AGE_DAYS = int(os.environ.get("INBOX_CLEANUP_MAX_AGE_DAYS", "7"))
BULK_ACK_HOURS = int(os.environ.get("INBOX_CLEANUP_BULK_ACK_HOURS", "12"))
BULK_ACK_KIND = (os.environ.get("INBOX_CLEANUP_BULK_ACK_KIND") or "proactive_action").strip()
BULK_ACK_ENABLED = os.environ.get("INBOX_CLEANUP_BULK_ACK_ENABLED", "1").lower() not in (
    "0",
    "false",
    "no",
)
PANEL_URL = os.environ.get("KRAB_PANEL_URL", "http://127.0.0.1:8080")
WEB_KEY = os.environ.get("KRAB_WEB_KEY", "")
DRY_RUN = os.environ.get("INBOX_CLEANUP_DRY_RUN", "").lower() in ("1", "true", "yes")


def _post_panel(url: str, payload: dict | None = None) -> dict | None:
    """POST в Owner Panel API; None если panel недоступна."""
    body = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, method="POST")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if WEB_KEY:
        req.add_header("X-Krab-Web-Key", WEB_KEY)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
        return None


def run_bulk_ack_via_api() -> dict | None:
    """Wave 85: bulk-ack этап через Owner Panel."""
    return _post_panel(
        f"{PANEL_URL}/api/inbox/bulk-ack-stale",
        {
            "kind": BULK_ACK_KIND,
            "age_threshold_hours": BULK_ACK_HOURS,
            "dry_run": DRY_RUN,
            "actor": "inbox-cleanup-cron",
            "note": f"wave85_bulk_ack_age>{BULK_ACK_HOURS}h",
            "target_status": "acked",
        },
    )


def run_cleanup_via_api() -> dict | None:
    """Этап архивации через Owner Panel API."""
    params = f"max_age_days={MAX_AGE_DAYS}&dry_run={'true' if DRY_RUN else 'false'}"
    return _post_panel(f"{PANEL_URL}/api/inbox/cleanup-stale?{params}")


def run_direct() -> dict:
    """Direct fallback: bulk_ack + cleanup напрямую через InboxService."""
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))
    from src.core.inbox_service import inbox_service  # noqa: PLC0415

    bulk_ack_result: dict = {}
    if BULK_ACK_ENABLED:
        try:
            bulk_ack_result = inbox_service.bulk_acknowledge_stale(
                kind=BULK_ACK_KIND,
                age_threshold_hours=BULK_ACK_HOURS,
                dry_run=DRY_RUN,
                actor="inbox-cleanup-cron",
                note=f"wave85_bulk_ack_age>{BULK_ACK_HOURS}h",
                target_status="acked",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[inbox-cleanup] WARN direct bulk_ack failed: {exc}", file=sys.stderr)

    cleanup_result = inbox_service.cleanup_stale_open_items(
        max_age_days=MAX_AGE_DAYS,
        dry_run=DRY_RUN,
    )
    return {"bulk_ack": bulk_ack_result, "cleanup": cleanup_result}


def main() -> int:
    via = "api"
    bulk_ack_result: dict | None = None
    cleanup_result: dict | None = None

    if BULK_ACK_ENABLED:
        bulk_ack_envelope = run_bulk_ack_via_api()
        if bulk_ack_envelope is not None:
            bulk_ack_result = bulk_ack_envelope.get("result") or {}

    cleanup_envelope = run_cleanup_via_api()
    if cleanup_envelope is not None:
        cleanup_result = cleanup_envelope

    # Если хотя бы один из вызовов упал — fallback на direct mode для обоих
    # (так проще: единый источник истины и не нужно частично патчить state).
    if cleanup_result is None or (BULK_ACK_ENABLED and bulk_ack_result is None):
        via = "direct"
        try:
            direct = run_direct()
            bulk_ack_result = direct["bulk_ack"] if BULK_ACK_ENABLED else {}
            cleanup_result = direct["cleanup"]
        except Exception as exc:  # noqa: BLE001
            print(f"[inbox-cleanup] ОШИБКА direct mode: {exc}", file=sys.stderr)
            return 1

    cleanup_result = cleanup_result or {}
    bulk_ack_result = bulk_ack_result or {}

    archived = cleanup_result.get("archived_count", 0)
    kept = cleanup_result.get("kept_count", 0)
    by_kind = cleanup_result.get("by_kind", {})
    acked = bulk_ack_result.get("acked", 0)
    matched = bulk_ack_result.get("matched", 0)
    is_dry = cleanup_result.get("dry_run", DRY_RUN)

    prefix = "[DRY-RUN] " if is_dry else ""
    print(
        f"[inbox-cleanup] {prefix}via={via} "
        f"bulk_ack_matched={matched} bulk_ack_acked={acked} "
        f"archived={archived} kept={kept} "
        f"by_kind={json.dumps(by_kind, ensure_ascii=False)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
