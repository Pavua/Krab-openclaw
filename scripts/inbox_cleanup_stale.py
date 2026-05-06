#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wave 34-C: Ежедневная автозачистка stale open inbox items.

Запускается из LaunchAgent ai.krab.inbox-cleanup через Owner Panel API.
Если panel недоступна — выполняет cleanup напрямую через InboxService.

Выход 0 — успех (включая "нечего чистить").
Выход 1 — ошибка.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

# Настройки по умолчанию
MAX_AGE_DAYS = int(os.environ.get("INBOX_CLEANUP_MAX_AGE_DAYS", "7"))
PANEL_URL = os.environ.get("KRAB_PANEL_URL", "http://127.0.0.1:8080")
WEB_KEY = os.environ.get("KRAB_WEB_KEY", "")
DRY_RUN = os.environ.get("INBOX_CLEANUP_DRY_RUN", "").lower() in ("1", "true", "yes")


def run_via_api() -> dict | None:
    """Пробует выполнить cleanup через Owner Panel API."""
    params = f"max_age_days={MAX_AGE_DAYS}&dry_run={'true' if DRY_RUN else 'false'}"
    url = f"{PANEL_URL}/api/inbox/cleanup-stale?{params}"
    req = urllib.request.Request(url, method="POST")
    if WEB_KEY:
        req.add_header("X-Krab-Web-Key", WEB_KEY)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:  # noqa: BLE001 — panel может быть недоступна
        return None


def run_direct() -> dict:
    """Выполняет cleanup напрямую через InboxService (fallback без panel)."""
    # Добавляем src в path для standalone запуска
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))
    from src.core.inbox_service import inbox_service  # noqa: PLC0415
    return inbox_service.cleanup_stale_open_items(max_age_days=MAX_AGE_DAYS, dry_run=DRY_RUN)


def main() -> int:
    result = run_via_api()
    via = "api"

    if result is None:
        # Panel недоступна — используем direct mode
        via = "direct"
        try:
            result = run_direct()
        except Exception as exc:  # noqa: BLE001
            print(f"[inbox-cleanup] ОШИБКА direct mode: {exc}", file=sys.stderr)
            return 1

    archived = result.get("archived_count", 0)
    kept = result.get("kept_count", 0)
    by_kind = result.get("by_kind", {})
    is_dry = result.get("dry_run", DRY_RUN)

    prefix = "[DRY-RUN] " if is_dry else ""
    print(
        f"[inbox-cleanup] {prefix}via={via} "
        f"archived={archived} kept={kept} "
        f"by_kind={json.dumps(by_kind, ensure_ascii=False)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
