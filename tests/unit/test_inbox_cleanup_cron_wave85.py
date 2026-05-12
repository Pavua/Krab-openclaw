# -*- coding: utf-8 -*-
"""
Wave 85: Tests for inbox_cleanup_stale.py cron script's two-stage logic.

Проверяем что cron теперь делает bulk-ack для свежих stale (12h+) opens
плюс архивирует старые (7d+) — закрывает gap между janitor`ом и старым
архиватором, из-за которого Session 45 наблюдала "stale 40+ накапливаются".
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.core.inbox_service import InboxIdentity, InboxItem, InboxService

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "inbox_cleanup_stale.py"


def _load_script(monkeypatch, env: dict[str, str] | None = None):
    """Загружает inbox_cleanup_stale.py как модуль (без запуска main)."""
    for key in (
        "INBOX_CLEANUP_MAX_AGE_DAYS",
        "INBOX_CLEANUP_BULK_ACK_HOURS",
        "INBOX_CLEANUP_BULK_ACK_KIND",
        "INBOX_CLEANUP_BULK_ACK_ENABLED",
        "INBOX_CLEANUP_DRY_RUN",
        "KRAB_PANEL_URL",
        "KRAB_WEB_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location(
        "inbox_cleanup_stale_under_test", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_item(
    *,
    item_id: str,
    kind: str,
    status: str = "open",
    age_hours: float = 0.0,
) -> dict:
    created = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    iso = created.isoformat(timespec="seconds")
    item = InboxItem(
        item_id=item_id,
        dedupe_key=f"{kind}:{item_id}",
        kind=kind,
        source="test",
        status=status,
        severity="info",
        title=f"item {item_id}",
        body="b",
        created_at_utc=iso,
        updated_at_utc=iso,
        identity=InboxIdentity(operator_id="op", account_id="acc"),
        metadata={},
    )
    return item.to_dict()


def _seed(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "updated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "items": items,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 1. Defaults loaded from env
# ---------------------------------------------------------------------------


def test_default_env_values(monkeypatch):
    module = _load_script(monkeypatch)
    assert module.MAX_AGE_DAYS == 7
    assert module.BULK_ACK_HOURS == 12
    assert module.BULK_ACK_KIND == "proactive_action"
    assert module.BULK_ACK_ENABLED is True
    assert module.DRY_RUN is False


def test_bulk_ack_disabled_when_env_zero(monkeypatch):
    module = _load_script(monkeypatch, {"INBOX_CLEANUP_BULK_ACK_ENABLED": "0"})
    assert module.BULK_ACK_ENABLED is False


# ---------------------------------------------------------------------------
# 2. Direct mode: bulk_ack moves 13h-old proactive_action open → acked
# ---------------------------------------------------------------------------


def test_direct_bulk_ack_acks_stale_proactive_action(monkeypatch, tmp_path):
    state_path = tmp_path / "inbox.json"
    items = [_make_item(item_id=f"pa{i}", kind="proactive_action", age_hours=13) for i in range(3)]
    _seed(state_path, items)
    service = InboxService(state_path=state_path)

    # Подменяем глобальный inbox_service на наш и заставляем panel API
    # быть "недоступной" чтобы код пошёл по direct mode.
    import src.core.inbox_service as inbox_mod  # noqa: PLC0415

    monkeypatch.setattr(inbox_mod, "inbox_service", service)

    module = _load_script(monkeypatch)
    monkeypatch.setattr(module, "run_bulk_ack_via_api", lambda: None)
    monkeypatch.setattr(module, "run_cleanup_via_api", lambda: None)

    rc = module.main()

    assert rc == 0
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    statuses = [it["status"] for it in saved["items"]]
    assert statuses.count("acked") == 3
    assert statuses.count("open") == 0


# ---------------------------------------------------------------------------
# 3. Boundary: 11h items survive bulk_ack (under 12h threshold)
# ---------------------------------------------------------------------------


def test_bulk_ack_skips_under_threshold(monkeypatch, tmp_path):
    state_path = tmp_path / "inbox.json"
    items = [_make_item(item_id="fresh", kind="proactive_action", age_hours=11)]
    _seed(state_path, items)
    service = InboxService(state_path=state_path)

    import src.core.inbox_service as inbox_mod  # noqa: PLC0415

    monkeypatch.setattr(inbox_mod, "inbox_service", service)

    module = _load_script(monkeypatch)
    monkeypatch.setattr(module, "run_bulk_ack_via_api", lambda: None)
    monkeypatch.setattr(module, "run_cleanup_via_api", lambda: None)

    rc = module.main()
    assert rc == 0
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["items"][0]["status"] == "open"


# ---------------------------------------------------------------------------
# 4. Both stages run in single invocation
# ---------------------------------------------------------------------------


def test_both_stages_compose(monkeypatch, tmp_path):
    state_path = tmp_path / "inbox.json"
    items = [
        # 13h old proactive_action → bulk_ack → acked
        _make_item(item_id="recent", kind="proactive_action", age_hours=13),
        # 10d old info_alert → archive → cancelled
        _make_item(item_id="very_old", kind="info_alert", age_hours=10 * 24),
        # Fresh kept
        _make_item(item_id="fresh", kind="info_alert", age_hours=2),
    ]
    _seed(state_path, items)
    service = InboxService(state_path=state_path)

    import src.core.inbox_service as inbox_mod  # noqa: PLC0415

    monkeypatch.setattr(inbox_mod, "inbox_service", service)

    module = _load_script(monkeypatch)
    monkeypatch.setattr(module, "run_bulk_ack_via_api", lambda: None)
    monkeypatch.setattr(module, "run_cleanup_via_api", lambda: None)

    rc = module.main()
    assert rc == 0
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    by_id = {it["item_id"]: it["status"] for it in saved["items"]}
    assert by_id["recent"] == "acked"
    assert by_id["very_old"] == "cancelled"
    assert by_id["fresh"] == "open"


# ---------------------------------------------------------------------------
# 5. Disabling bulk_ack stage preserves opens
# ---------------------------------------------------------------------------


def test_bulk_ack_disabled_preserves_opens(monkeypatch, tmp_path):
    state_path = tmp_path / "inbox.json"
    items = [_make_item(item_id="pa", kind="proactive_action", age_hours=24)]
    _seed(state_path, items)
    service = InboxService(state_path=state_path)

    import src.core.inbox_service as inbox_mod  # noqa: PLC0415

    monkeypatch.setattr(inbox_mod, "inbox_service", service)

    module = _load_script(monkeypatch, {"INBOX_CLEANUP_BULK_ACK_ENABLED": "0"})
    monkeypatch.setattr(module, "run_bulk_ack_via_api", lambda: None)
    monkeypatch.setattr(module, "run_cleanup_via_api", lambda: None)

    rc = module.main()
    assert rc == 0
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    # 24h proactive_action не достиг 7d, и bulk_ack disabled → open сохраняется
    assert saved["items"][0]["status"] == "open"


# ---------------------------------------------------------------------------
# 6. Dry-run does not mutate state
# ---------------------------------------------------------------------------


def test_dry_run_does_not_mutate(monkeypatch, tmp_path):
    state_path = tmp_path / "inbox.json"
    items = [
        _make_item(item_id="pa", kind="proactive_action", age_hours=24),
        _make_item(item_id="ia", kind="info_alert", age_hours=10 * 24),
    ]
    _seed(state_path, items)
    service = InboxService(state_path=state_path)

    import src.core.inbox_service as inbox_mod  # noqa: PLC0415

    monkeypatch.setattr(inbox_mod, "inbox_service", service)

    module = _load_script(monkeypatch, {"INBOX_CLEANUP_DRY_RUN": "1"})
    monkeypatch.setattr(module, "run_bulk_ack_via_api", lambda: None)
    monkeypatch.setattr(module, "run_cleanup_via_api", lambda: None)

    rc = module.main()
    assert rc == 0
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    statuses = sorted(it["status"] for it in saved["items"])
    assert statuses == ["open", "open"]
