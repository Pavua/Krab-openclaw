# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.commands_admin_router`` — Wave 190 (Session 48).

Покрытие:
- factory + endpoints (HTML page + JSON list + usage_summary)
- payload structure: commands / summary / categories / aliases
- usage merging: counts + last_ts из command_usage.json
- custom_aliases merging из command_aliases.json
- TTL-cache (cached_payload reuses)
- graceful handling: пропавшие файлы, повреждённый JSON, registry import fail
- top_10/never_used semantics
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers import commands_admin_router as car
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.commands_admin_router import (
    build_commands_admin_router,
)


def _make_client() -> TestClient:
    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    app = FastAPI()
    app.include_router(build_commands_admin_router(ctx))
    return TestClient(app)


# ── Категории / лейблы ───────────────────────────────────────────────────────


def test_category_labels_cover_registry_categories() -> None:
    """Все категории из registry имеют label в _CATEGORY_LABELS."""
    from src.core.command_registry import registry

    registry_cats = {cmd.category for cmd in registry.all()}
    label_cats = set(car._CATEGORY_LABELS.keys())
    # все registry-категории есть в labels (label может содержать лишние)
    missing = registry_cats - label_cats
    assert not missing, f"Категории без label: {missing}"


def test_recent_window_days_is_7() -> None:
    """Окно "недавно используемых" — ровно 7 дней."""
    assert car._RECENT_WINDOW_DAYS == 7


# ── Build payload ────────────────────────────────────────────────────────────


def test_build_payload_returns_commands_with_registry_count(tmp_path) -> None:
    """Payload содержит все команды из registry."""
    car._invalidate_cache()
    with (
        patch.object(car, "_USAGE_FILE", tmp_path / "missing_usage.json"),
        patch.object(car, "_ALIASES_FILE", tmp_path / "missing_alias.json"),
    ):
        payload = car._build_payload()

    from src.core.command_registry import registry

    expected = len(registry.all())
    assert payload["summary"]["total_commands"] == expected
    assert len(payload["commands"]) == expected


def test_build_payload_summary_fields_present(tmp_path) -> None:
    """summary имеет все ожидаемые поля."""
    car._invalidate_cache()
    with (
        patch.object(car, "_USAGE_FILE", tmp_path / "missing.json"),
        patch.object(car, "_ALIASES_FILE", tmp_path / "missing2.json"),
    ):
        payload = car._build_payload()
    s = payload["summary"]
    for field in (
        "total_commands",
        "total_invocations",
        "unique_commands_used",
        "never_used_count",
        "recent_used_count",
        "owner_only_count",
        "by_category",
        "top_10",
        "never_used",
        "recent_window_days",
    ):
        assert field in s, f"Missing summary field: {field}"


def test_build_payload_merges_usage_counts(tmp_path) -> None:
    """counts из command_usage.json попадают в usage_count."""
    car._invalidate_cache()
    usage_path = tmp_path / "command_usage.json"
    now = time.time()
    usage_path.write_text(
        json.dumps(
            {
                "counts": {"help": 42, "swarm": 7},
                "last_ts": {"help": now, "swarm": now - 100},
            }
        )
    )
    with (
        patch.object(car, "_USAGE_FILE", usage_path),
        patch.object(car, "_ALIASES_FILE", tmp_path / "missing.json"),
    ):
        payload = car._build_payload()

    by_name = {c["name"]: c for c in payload["commands"]}
    assert by_name["help"]["usage_count"] == 42
    assert by_name["swarm"]["usage_count"] == 7
    assert by_name["help"]["recent_used"] is True


def test_build_payload_top10_sorted_desc(tmp_path) -> None:
    """Top-10 отсортирован по убыванию usage_count."""
    car._invalidate_cache()
    usage_path = tmp_path / "command_usage.json"
    usage_path.write_text(
        json.dumps(
            {
                "counts": {"help": 5, "swarm": 50, "status": 10},
                "last_ts": {},
            }
        )
    )
    with (
        patch.object(car, "_USAGE_FILE", usage_path),
        patch.object(car, "_ALIASES_FILE", tmp_path / "missing.json"),
    ):
        payload = car._build_payload()

    counts = [c["usage_count"] for c in payload["summary"]["top_10"]]
    assert counts == sorted(counts, reverse=True)
    assert payload["summary"]["top_10"][0]["name"] == "swarm"


def test_build_payload_never_used_excludes_counted(tmp_path) -> None:
    """never_used не содержит команд с count>0."""
    car._invalidate_cache()
    usage_path = tmp_path / "command_usage.json"
    usage_path.write_text(json.dumps({"counts": {"help": 5}, "last_ts": {}}))
    with (
        patch.object(car, "_USAGE_FILE", usage_path),
        patch.object(car, "_ALIASES_FILE", tmp_path / "missing.json"),
    ):
        payload = car._build_payload()
    assert "help" not in payload["summary"]["never_used"]


def test_build_payload_includes_custom_aliases(tmp_path) -> None:
    """Кастомные алиасы из command_aliases.json привязываются к команде."""
    car._invalidate_cache()
    aliases_path = tmp_path / "command_aliases.json"
    aliases_path.write_text(json.dumps({"helpme": "help", "h2": "help"}))
    with (
        patch.object(car, "_USAGE_FILE", tmp_path / "no_usage.json"),
        patch.object(car, "_ALIASES_FILE", aliases_path),
    ):
        payload = car._build_payload()

    by_name = {c["name"]: c for c in payload["commands"]}
    custom = sorted(by_name["help"]["custom_aliases"])
    assert custom == ["h2", "helpme"]
    assert payload["custom_aliases_total"] == 2


# ── File-IO robustness ──────────────────────────────────────────────────────


def test_read_usage_missing_file(tmp_path) -> None:
    """Отсутствующий файл → {counts: {}, last_ts: {}}."""
    with patch.object(car, "_USAGE_FILE", tmp_path / "absent.json"):
        out = car._read_usage_file()
    assert out == {"counts": {}, "last_ts": {}}


def test_read_usage_corrupt_json(tmp_path) -> None:
    """Повреждённый JSON → fallback к пустому payload (без exception)."""
    usage_path = tmp_path / "command_usage.json"
    usage_path.write_text("not a json {")
    with patch.object(car, "_USAGE_FILE", usage_path):
        out = car._read_usage_file()
    assert out == {"counts": {}, "last_ts": {}}


def test_read_usage_legacy_flat_format(tmp_path) -> None:
    """Старый формат {name: count} тоже читается."""
    usage_path = tmp_path / "command_usage.json"
    usage_path.write_text(json.dumps({"help": 3, "ask": 1}))
    with patch.object(car, "_USAGE_FILE", usage_path):
        out = car._read_usage_file()
    assert out["counts"] == {"help": 3, "ask": 1}
    assert out["last_ts"] == {}


def test_read_aliases_missing_file(tmp_path) -> None:
    """command_aliases.json может отсутствовать."""
    with patch.object(car, "_ALIASES_FILE", tmp_path / "absent.json"):
        out = car._read_aliases_file()
    assert out == {}


def test_read_aliases_corrupt_json(tmp_path) -> None:
    """Повреждённый JSON алиасов → пусто, без exception."""
    p = tmp_path / "command_aliases.json"
    p.write_text("{{{")
    with patch.object(car, "_ALIASES_FILE", p):
        out = car._read_aliases_file()
    assert out == {}


# ── Cache ────────────────────────────────────────────────────────────────────


def test_cached_payload_reuses_within_ttl(tmp_path) -> None:
    """Второй вызов в окне TTL не пересчитывает payload."""
    car._invalidate_cache()
    with (
        patch.object(car, "_USAGE_FILE", tmp_path / "u.json"),
        patch.object(car, "_ALIASES_FILE", tmp_path / "a.json"),
    ):
        first = car._cached_payload()
        second = car._cached_payload()
    assert first is second  # same object reference (cached)


# ── Endpoints ────────────────────────────────────────────────────────────────


def test_list_endpoint_ok(tmp_path) -> None:
    """GET /api/admin/commands/list возвращает 200 + поля payload."""
    car._invalidate_cache()
    with (
        patch.object(car, "_USAGE_FILE", tmp_path / "u.json"),
        patch.object(car, "_ALIASES_FILE", tmp_path / "a.json"),
    ):
        client = _make_client()
        resp = client.get("/api/admin/commands/list")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "commands" in body
    assert "summary" in body
    assert "categories" in body
    assert body["summary"]["total_commands"] > 100  # registry > 100


def test_usage_summary_endpoint_ok(tmp_path) -> None:
    """GET /api/admin/commands/usage_summary возвращает компактный payload."""
    car._invalidate_cache()
    usage = tmp_path / "command_usage.json"
    usage.write_text(json.dumps({"counts": {"help": 1}, "last_ts": {}}))
    with (
        patch.object(car, "_USAGE_FILE", usage),
        patch.object(car, "_ALIASES_FILE", tmp_path / "a.json"),
    ):
        client = _make_client()
        resp = client.get("/api/admin/commands/usage_summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["total_invocations"] >= 1
    assert isinstance(body["top_10"], list)
    assert isinstance(body["never_used"], list)


def test_html_page_endpoint_returns_html(tmp_path) -> None:
    """GET /admin/commands возвращает HTML 200."""
    with (
        patch.object(car, "_USAGE_FILE", tmp_path / "u.json"),
        patch.object(car, "_ALIASES_FILE", tmp_path / "a.json"),
    ):
        client = _make_client()
        resp = client.get("/admin/commands")
    assert resp.status_code == 200
    body = resp.text
    assert "<!DOCTYPE html>" in body
    assert "Admin Commands" in body
    assert "Wave 190" in body


# ── Registry import failure ─────────────────────────────────────────────────


def test_registry_import_failure_returns_empty(tmp_path) -> None:
    """При фейле импорта registry — payload пустой, не падает."""
    car._invalidate_cache()
    with patch.object(car, "_load_registry_safe", return_value=[]):
        with (
            patch.object(car, "_USAGE_FILE", tmp_path / "u.json"),
            patch.object(car, "_ALIASES_FILE", tmp_path / "a.json"),
        ):
            payload = car._build_payload()
    assert payload["commands"] == []
    assert payload["summary"]["total_commands"] == 0
    assert payload["summary"]["never_used"] == []
