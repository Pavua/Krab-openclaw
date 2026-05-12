"""Wave 158: тесты для scripts/krab_sentry_auto_resolve.py.

Покрытие:
- mock API list returns stale + active mix;
- is_stale() детектит lastSeen старше 7d с flat stats;
- is_stale() не помечает recent issues;
- is_stale() пропускает stale-by-age но с events_in_window > 0;
- dry-run не вызывает PUT;
- --apply вызывает PUT для каждого кандидата;
- snapshot JSON содержит обязательные поля;
- main() возвращает 2 без SENTRY_AUTH_TOKEN.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# Динамический импорт — скрипт лежит в scripts/, не в src/
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "krab_sentry_auto_resolve.py"
_spec = importlib.util.spec_from_file_location("krab_sentry_auto_resolve", _SCRIPT)
assert _spec is not None and _spec.loader is not None
auto_mod = importlib.util.module_from_spec(_spec)
sys.modules["krab_sentry_auto_resolve"] = auto_mod
_spec.loader.exec_module(auto_mod)


# ─── Fake httpx ──────────────────────────────────────────────────────────────


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
    """Маршрутизирует ответы по (method, url-substring). Запоминает все вызовы."""

    def __init__(
        self,
        route_map: dict[tuple[str, str], _FakeResponse],
    ) -> None:
        self.route_map = route_map
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,  # noqa: A002
        headers: Any = None,
    ) -> _FakeResponse:
        self.calls.append((method, url, params, json))
        for (mtd, url_part), resp in self.route_map.items():
            if mtd == method and url_part in url:
                return resp
        return _FakeResponse(status_code=404, text="no match")

    def close(self) -> None:
        pass


# ─── Фабрика issue ───────────────────────────────────────────────────────────


def _make_issue(
    *,
    issue_id: str = "100",
    short_id: str = "KRAB-1",
    title: str = "Some error",
    count: int = 10,
    last_seen_days_ago: float = 1.0,
    recent_events_in_window: int = 0,
    project_slug: str = "python-fastapi",
) -> dict[str, Any]:
    """Минимальный dict как от Sentry API."""
    now = datetime.now(timezone.utc)
    last_seen = now - timedelta(days=last_seen_days_ago)
    # Если recent_events_in_window > 0, кладём фейковый bucket за последние 24ч,
    # чтобы _stats_recent_events суммировал его.
    stats_30d: list[list[float]] = []
    if recent_events_in_window > 0:
        ts_in_window = (now - timedelta(hours=12)).timestamp()
        stats_30d.append([ts_in_window, float(recent_events_in_window)])
    # А ещё bucket вне окна (старее 8d) — чтобы убедиться, что cutoff работает
    ts_outside = (now - timedelta(days=20)).timestamp()
    stats_30d.append([ts_outside, 50.0])

    return {
        "id": issue_id,
        "shortId": short_id,
        "title": title,
        "count": count,
        "lastSeen": last_seen.isoformat().replace("+00:00", "Z"),
        "project": {"slug": project_slug},
        "stats": {"30d": stats_30d},
    }


# ─── is_stale ────────────────────────────────────────────────────────────────


class TestIsStale:
    def test_stale_old_and_flat(self) -> None:
        issue = _make_issue(last_seen_days_ago=10, recent_events_in_window=0)
        stale, reason = auto_mod.is_stale(issue, stale_days=7)
        assert stale is True
        assert "stale" in reason
        assert "age_days=10" in reason

    def test_not_stale_recent(self) -> None:
        issue = _make_issue(last_seen_days_ago=3, recent_events_in_window=0)
        stale, reason = auto_mod.is_stale(issue, stale_days=7)
        assert stale is False
        assert reason.startswith("recent_age_days=")

    def test_not_stale_old_but_has_recent_events(self) -> None:
        # lastSeen 10d ago, но в stats есть events за последние 24h → активен
        issue = _make_issue(last_seen_days_ago=10, recent_events_in_window=5)
        stale, reason = auto_mod.is_stale(issue, stale_days=7)
        assert stale is False
        assert "events_in_window=5" in reason

    def test_no_last_seen_returns_false(self) -> None:
        issue = {"id": "x", "stats": {}}
        stale, reason = auto_mod.is_stale(issue, stale_days=7)
        assert stale is False
        assert reason == "no_last_seen"

    def test_borderline_age_just_under_threshold(self) -> None:
        # 6.9d → ещё не stale (порог 7d)
        issue = _make_issue(last_seen_days_ago=6.9, recent_events_in_window=0)
        stale, _reason = auto_mod.is_stale(issue, stale_days=7)
        assert stale is False


# ─── run_auto_resolve: dry-run ───────────────────────────────────────────────


class TestRunAutoResolveDryRun:
    def test_dry_run_no_put_calls(self) -> None:
        stale = _make_issue(issue_id="111", last_seen_days_ago=10)
        active = _make_issue(issue_id="222", last_seen_days_ago=1)
        list_resp = _FakeResponse(status_code=200, json_data=[stale, active])
        client = _FakeClient({("GET", "/issues/"): list_resp})

        with patch.object(auto_mod, "SENTRY_TOKEN", "fake-token"):
            snap = auto_mod.run_auto_resolve(client=client, apply=False, stale_days=7)

        assert snap["mode"] == "dry-run"
        assert snap["total_unresolved"] == 2
        assert snap["stale_count"] == 1
        assert len(snap["resolved"]) == 1
        assert snap["resolved"][0]["id"] == "111"
        assert snap["errors"] == []
        # Не должно быть ни одного PUT-вызова
        put_calls = [c for c in client.calls if c[0] == "PUT"]
        assert put_calls == []

    def test_dry_run_zero_stale_when_all_recent(self) -> None:
        recent = _make_issue(issue_id="333", last_seen_days_ago=2)
        list_resp = _FakeResponse(status_code=200, json_data=[recent])
        client = _FakeClient({("GET", "/issues/"): list_resp})

        with patch.object(auto_mod, "SENTRY_TOKEN", "fake-token"):
            snap = auto_mod.run_auto_resolve(client=client, apply=False, stale_days=7)

        assert snap["stale_count"] == 0
        assert snap["resolved"] == []


# ─── run_auto_resolve: apply ─────────────────────────────────────────────────


class TestRunAutoResolveApply:
    def test_apply_calls_put_for_each_stale(self) -> None:
        stale1 = _make_issue(issue_id="111", short_id="KRAB-1", last_seen_days_ago=10)
        stale2 = _make_issue(issue_id="222", short_id="KRAB-2", last_seen_days_ago=15)
        active = _make_issue(issue_id="333", last_seen_days_ago=1)
        list_resp = _FakeResponse(
            status_code=200, json_data=[stale1, stale2, active]
        )
        put_resp = _FakeResponse(status_code=200, json_data={"status": "resolved"})
        client = _FakeClient(
            {
                ("GET", "/issues/"): list_resp,
                ("PUT", "/issues/"): put_resp,
            }
        )

        with patch.object(auto_mod, "SENTRY_TOKEN", "fake-token"):
            snap = auto_mod.run_auto_resolve(client=client, apply=True, stale_days=7)

        assert snap["mode"] == "apply"
        assert snap["stale_count"] == 2
        assert len(snap["resolved"]) == 2
        assert {r["id"] for r in snap["resolved"]} == {"111", "222"}
        # Должно быть ровно 2 PUT-вызова, оба с правильными id в URL
        put_calls = [c for c in client.calls if c[0] == "PUT"]
        assert len(put_calls) == 2
        assert any("/issues/111/" in c[1] for c in put_calls)
        assert any("/issues/222/" in c[1] for c in put_calls)
        # И ни одного PUT для active (id=333)
        assert not any("/issues/333/" in c[1] for c in put_calls)

    def test_apply_logs_error_on_put_failure(self) -> None:
        stale = _make_issue(issue_id="999", last_seen_days_ago=10)
        list_resp = _FakeResponse(status_code=200, json_data=[stale])
        put_resp = _FakeResponse(status_code=500, text="server err")
        client = _FakeClient(
            {
                ("GET", "/issues/"): list_resp,
                ("PUT", "/issues/"): put_resp,
            }
        )

        with patch.object(auto_mod, "SENTRY_TOKEN", "fake-token"):
            snap = auto_mod.run_auto_resolve(client=client, apply=True, stale_days=7)

        assert snap["stale_count"] == 1
        assert snap["resolved"] == []
        assert len(snap["errors"]) == 1
        assert snap["errors"][0]["id"] == "999"


# ─── snapshot JSON формат ────────────────────────────────────────────────────


def test_snapshot_required_keys_present() -> None:
    list_resp = _FakeResponse(status_code=200, json_data=[])
    client = _FakeClient({("GET", "/issues/"): list_resp})
    with patch.object(auto_mod, "SENTRY_TOKEN", "fake-token"):
        snap = auto_mod.run_auto_resolve(client=client, apply=False)
    for key in (
        "timestamp",
        "mode",
        "stale_days",
        "total_unresolved",
        "stale_count",
        "resolved",
        "errors",
    ):
        assert key in snap, f"missing key {key}"
    # JSON-сериализация не должна падать (cron pipes stdout)
    json.dumps(snap)


# ─── main() без токена ───────────────────────────────────────────────────────


def test_main_returns_2_without_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch.object(auto_mod, "SENTRY_TOKEN", None):
        rc = auto_mod.main([])
    assert rc == 2


# ─── argparse: --apply flag ──────────────────────────────────────────────────


def test_main_apply_flag_propagates() -> None:
    """--apply должен вызвать run_auto_resolve(apply=True)."""
    captured: dict[str, Any] = {}

    def _spy(*, apply: bool, stale_days: int, **kw: Any) -> dict[str, Any]:
        captured["apply"] = apply
        captured["stale_days"] = stale_days
        return {
            "timestamp": "2026-05-12T09:00:00+00:00",
            "mode": "apply" if apply else "dry-run",
            "stale_days": stale_days,
            "total_unresolved": 0,
            "stale_count": 0,
            "resolved": [],
            "errors": [],
        }

    with (
        patch.object(auto_mod, "SENTRY_TOKEN", "fake-token"),
        patch.object(auto_mod, "run_auto_resolve", side_effect=_spy),
    ):
        rc = auto_mod.main(["--apply", "--stale-days", "14"])

    assert rc == 0
    assert captured == {"apply": True, "stale_days": 14}
