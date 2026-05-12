"""Wave 71: тесты для scripts/krab_sentry_quota_check.py.

Покрытие:
- baseline init на первом run (no file → create);
- regression detection (current > baseline * 1.20);
- no false-positive на flat data;
- guard baseline<=0 → regression=False;
- JSON snapshot формат (timestamp, total_events, top_5_issues, regression, baseline_total);
- mock Sentry stats_v2 parsing;
- mock fetch_top_issues парсит list response.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Динамический импорт без модификации sys.path (скрипт лежит в scripts/)
_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "krab_sentry_quota_check.py"
)
_spec = importlib.util.spec_from_file_location("krab_sentry_quota_check", _SCRIPT)
assert _spec is not None and _spec.loader is not None
quota_mod = importlib.util.module_from_spec(_spec)
sys.modules["krab_sentry_quota_check"] = quota_mod
_spec.loader.exec_module(quota_mod)


# ─── Fake httpx.Client / Response ────────────────────────────────────────────


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        json_data: Any = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.headers = headers or {}

    def json(self) -> Any:
        if isinstance(self._json, Exception):
            raise self._json
        return self._json


class _FakeClient:
    """Мокает httpx.Client: возвращает заранее заготовленные ответы по URL substring."""

    def __init__(self, route_map: dict[str, _FakeResponse]) -> None:
        self.route_map = route_map
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, params: dict[str, Any] | None = None, headers: Any = None):
        self.calls.append((url, params or {}))
        for key, resp in self.route_map.items():
            if key in url:
                return resp
        return _FakeResponse(status_code=404, text="no match")

    def post(self, url: str, json: Any = None):  # noqa: A002 - mirror httpx API
        return _FakeResponse(status_code=200, json_data={"ok": True})

    def close(self) -> None:
        pass

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ─── detect_regression ───────────────────────────────────────────────────────


def test_regression_detected_above_threshold() -> None:
    # baseline 100, threshold 0.20 → trigger при current > 120
    assert quota_mod.detect_regression(121, 100, threshold=0.20) is True


def test_no_false_positive_on_flat_data() -> None:
    # Точно равно — не regression
    assert quota_mod.detect_regression(100, 100, threshold=0.20) is False
    # Ниже baseline — тем более
    assert quota_mod.detect_regression(80, 100, threshold=0.20) is False
    # +10% — ниже threshold +20%
    assert quota_mod.detect_regression(110, 100, threshold=0.20) is False


def test_baseline_zero_disables_regression() -> None:
    # Защита от division-by-zero / cold start
    assert quota_mod.detect_regression(500, 0, threshold=0.20) is False
    assert quota_mod.detect_regression(500, -5, threshold=0.20) is False


# ─── baseline init / load / save ─────────────────────────────────────────────


def test_baseline_init_on_first_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    baseline_path = tmp_path / "sentry_quota_baseline.json"
    assert not baseline_path.exists()

    # stats_v2: total = 50 + 30 = 80
    stats_resp = _FakeResponse(
        status_code=200,
        json_data={
            "groups": [
                {"totals": {"sum(quantity)": 50}},
                {"totals": {"sum(quantity)": 30}},
            ],
        },
    )
    issues_resp = _FakeResponse(status_code=200, json_data=[])
    client = _FakeClient(
        {
            "stats_v2": stats_resp,
            "/issues/": issues_resp,
        }
    )

    snap = quota_mod.run_check(
        client=client, baseline_path=baseline_path, threshold=0.20, alert_telegram=False
    )

    assert snap["total_events"] == 80
    assert snap["regression"] is False
    assert snap["baseline_initialized"] is False  # not yet existed
    assert baseline_path.exists()
    saved = json.loads(baseline_path.read_text())
    assert saved["total_events"] == 80
    assert "initialized_at" in saved


def test_regression_triggers_when_above_threshold(
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "sentry_quota_baseline.json"
    baseline_path.write_text(
        json.dumps({"initialized_at": "2026-05-01T00:00:00+00:00", "total_events": 100})
    )

    stats_resp = _FakeResponse(
        status_code=200,
        json_data={"groups": [{"totals": {"sum(quantity)": 150}}]},
    )
    issues_resp = _FakeResponse(
        status_code=200,
        json_data=[
            {
                "shortId": "KRAB-1",
                "title": "Sample error",
                "count": 50,
                "level": "error",
                "project": {"slug": "python-fastapi"},
            }
        ],
    )
    client = _FakeClient({"stats_v2": stats_resp, "/issues/": issues_resp})

    snap = quota_mod.run_check(
        client=client, baseline_path=baseline_path, threshold=0.20, alert_telegram=False
    )

    assert snap["total_events"] == 150
    assert snap["baseline_total"] == 100
    assert snap["regression"] is True
    assert len(snap["top_5_issues"]) == 1
    assert snap["top_5_issues"][0]["shortId"] == "KRAB-1"
    assert snap["top_5_issues"][0]["count"] == 50


def test_snapshot_format_keys_and_types(tmp_path: Path) -> None:
    baseline_path = tmp_path / "sentry_quota_baseline.json"
    stats_resp = _FakeResponse(
        status_code=200,
        json_data={"groups": [{"totals": {"sum(quantity)": 42}}]},
    )
    issues_resp = _FakeResponse(status_code=200, json_data=[])
    client = _FakeClient({"stats_v2": stats_resp, "/issues/": issues_resp})

    snap = quota_mod.run_check(
        client=client, baseline_path=baseline_path, threshold=0.20, alert_telegram=False
    )

    for required_key in (
        "timestamp",
        "total_events",
        "top_5_issues",
        "regression",
        "baseline_total",
    ):
        assert required_key in snap, f"missing key {required_key}"

    assert isinstance(snap["timestamp"], str)
    assert isinstance(snap["total_events"], int)
    assert isinstance(snap["top_5_issues"], list)
    assert isinstance(snap["regression"], bool)
    assert isinstance(snap["baseline_total"], int)


def test_stats_v2_parses_empty_groups() -> None:
    stats_resp = _FakeResponse(status_code=200, json_data={"groups": []})
    issues_resp = _FakeResponse(status_code=200, json_data=[])
    client = _FakeClient({"stats_v2": stats_resp, "/issues/": issues_resp})

    total = quota_mod.fetch_weekly_event_count(client)
    assert total == 0


def test_stats_v2_returns_none_on_http_error() -> None:
    stats_resp = _FakeResponse(status_code=500, text="server err")
    client = _FakeClient({"stats_v2": stats_resp})
    # Retry exhaustion вернёт response с 5xx → fetch вернёт None
    total = quota_mod.fetch_weekly_event_count(client)
    assert total is None


def test_fetch_top_issues_truncates_and_normalizes() -> None:
    raw_issues = [
        {
            "shortId": f"KRAB-{i}",
            "title": "x" * 300,  # должно обрезаться до 200
            "count": i * 10,
            "level": "error",
            "project": {"slug": "python-fastapi"},
        }
        for i in range(1, 8)
    ]
    issues_resp = _FakeResponse(status_code=200, json_data=raw_issues)
    client = _FakeClient({"/issues/": issues_resp})

    top = quota_mod.fetch_top_issues(client, limit=5)
    assert len(top) == 5
    assert all(len(t["title"]) <= 200 for t in top)
    assert top[0]["count"] == 10
    assert top[0]["project"] == "python-fastapi"


def test_load_baseline_missing_returns_none(tmp_path: Path) -> None:
    assert quota_mod.load_baseline(tmp_path / "nope.json") is None


def test_load_baseline_corrupt_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "baseline.json"
    p.write_text("not-json{")
    assert quota_mod.load_baseline(p) is None
