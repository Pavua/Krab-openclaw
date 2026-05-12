"""Тесты генератора Grafana dashboard (Wave 132)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.krab_grafana_dashboard_gen import (
    ROWS,
    build_dashboard,
    main,
    validate_dashboard,
)


def test_dashboard_has_expected_rows() -> None:
    """Дашборд содержит 5 row-секций (System / Telegram / Cost / AI / External)."""
    dashboard = build_dashboard()
    row_titles = [p["title"] for p in dashboard["panels"] if p.get("type") == "row"]
    assert row_titles == ["System", "Telegram", "Cost", "AI Routing", "External"]
    assert len(ROWS) == 5


def test_dashboard_panel_count_matches_rows_definition() -> None:
    """Количество panel равно sum(panels per row) + 5 row-заголовков."""
    dashboard = build_dashboard()
    expected_data_panels = sum(len(row[1]) for row in ROWS)
    expected_total = expected_data_panels + len(ROWS)  # +row headers
    assert len(dashboard["panels"]) == expected_total
    # Каждый panel data-типа имеет non-empty expr
    data_panels = [p for p in dashboard["panels"] if p.get("type") != "row"]
    assert len(data_panels) == expected_data_panels
    for p in data_panels:
        assert p["targets"][0]["expr"]


def test_dashboard_top_level_structure() -> None:
    """Top-level ключи Grafana дашборда присутствуют."""
    dashboard = build_dashboard()
    for key in ("title", "uid", "schemaVersion", "panels", "templating", "time"):
        assert key in dashboard
    assert dashboard["uid"] == "krab-overview"
    assert "wave132" in dashboard["tags"]


def test_validate_dashboard_passes_for_generated() -> None:
    """validate_dashboard не возвращает ошибок для сгенерированного дашборда."""
    dashboard = build_dashboard()
    assert validate_dashboard(dashboard) == []


def test_validate_detects_missing_targets() -> None:
    """validate_dashboard ловит panel без targets/expr."""
    broken = {
        "title": "x",
        "uid": "x",
        "schemaVersion": 38,
        "panels": [
            {"id": 1, "title": "no targets", "gridPos": {}, "targets": []},
            {
                "id": 1,  # дубль
                "title": "empty expr",
                "gridPos": {},
                "targets": [{"expr": ""}],
            },
        ],
    }
    errors = validate_dashboard(broken)
    assert any("no targets" in e for e in errors)
    assert any("empty expr" in e for e in errors)
    assert any("duplicate panel id" in e for e in errors)


def test_panel_ids_are_unique() -> None:
    """Все panel.id уникальны."""
    dashboard = build_dashboard()
    ids = [p["id"] for p in dashboard["panels"]]
    assert len(ids) == len(set(ids))


def test_cli_writes_file(tmp_path: Path) -> None:
    """CLI пишет валидный JSON в указанный --output."""
    out = tmp_path / "krab_overview.json"
    rc = main(["--output", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["uid"] == "krab-overview"
    assert payload["panels"]


def test_cli_validate_flag() -> None:
    """--validate возвращает 0 для корректного дашборда."""
    assert main(["--validate"]) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
