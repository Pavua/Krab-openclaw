# -*- coding: utf-8 -*-
"""
Тесты Wave 34-A: quota history snapshot + GET /api/quota/history.

5 тестов:
1. snapshot_script пишет корректную JSONL-строку
2. snapshot_script обрабатывает Краб-down без исключений
3. endpoint /quota/history с пустым файлом возвращает пустой результат
4. endpoint фильтрует по window (отсекает старые снимки)
5. aggregated вычисляет max per day per provider
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.quota_router import build_quota_router

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_client(log_path: Path) -> TestClient:
    """Создаём тестовый FastAPI app с quota router и подменённым лог-путём."""
    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    app = FastAPI()
    with patch("src.modules.web_routers.quota_router._QUOTA_HISTORY_LOG", new=log_path):
        router = build_quota_router(ctx)
    app.include_router(router)
    return TestClient(app)


def _make_snapshot(ts: float, ok: bool = True, providers: dict | None = None) -> str:
    """Создаёт валидную JSONL-строку снимка."""
    if providers is None:
        providers = {
            "gemini-cli": {"today_calls": 10},
            "codex-cli": {"today_calls": 5},
            "google-vertex": {"today_calls": 3},
            "anthropic-vertex": {"today_calls": 1},
        }
    snap: dict = {"ts": ts, "ok": ok}
    if ok:
        snap["providers"] = providers
    return json.dumps(snap)


# ── Тест 1: snapshot_script пишет корректную JSONL-строку ────────────────────


def test_snapshot_writes_jsonl_correctly(tmp_path: Path) -> None:
    """main() должен записать валидный JSON с ts и полями из /api/quota."""
    log_file = tmp_path / "quota_history.jsonl"

    # Мок ответа от owner panel
    mock_response_data = {
        "ok": True,
        "date": "2026-05-06",
        "providers": {
            "gemini-cli": {"today_calls": 42},
            "codex-cli": {"today_calls": 7},
        },
    }

    import urllib.request
    from unittest.mock import MagicMock

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_response_data).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with (
        patch("urllib.request.urlopen", return_value=mock_resp),
        patch("scripts.quota_history_snapshot.LOG", new=log_file),
    ):
        from scripts import quota_history_snapshot

        result = quota_history_snapshot.main()

    assert result == 0
    assert log_file.exists(), "JSONL-файл должен быть создан"

    lines = [l for l in log_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 1, "Должна быть записана одна строка"

    snap = json.loads(lines[0])
    assert "ts" in snap, "Снимок должен содержать метку времени ts"
    assert snap["ok"] is True
    assert snap["providers"]["gemini-cli"]["today_calls"] == 42
    assert snap["providers"]["codex-cli"]["today_calls"] == 7
    assert isinstance(snap["ts"], float)


# ── Тест 2: snapshot_script обрабатывает Краб-down ───────────────────────────


def test_snapshot_handles_krab_down(tmp_path: Path) -> None:
    """Если Краб недоступен — записывает placeholder без исключений."""
    import urllib.error

    log_file = tmp_path / "quota_history.jsonl"

    with (
        patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ),
        patch("scripts.quota_history_snapshot.LOG", new=log_file),
    ):
        from scripts import quota_history_snapshot

        result = quota_history_snapshot.main()

    assert result == 0
    assert log_file.exists()

    lines = [l for l in log_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 1

    snap = json.loads(lines[0])
    assert snap["ok"] is False
    assert "error" in snap
    assert "Connection refused" in snap["error"]
    assert "ts" in snap


# ── Тест 3: endpoint с несуществующим файлом возвращает пустой результат ──────


def test_quota_history_empty_file(tmp_path: Path) -> None:
    """GET /api/quota/history — файл не существует → snapshots=[], aggregated={}."""
    nonexistent = tmp_path / "does_not_exist.jsonl"
    client = _make_client(nonexistent)

    with patch("src.modules.web_routers.quota_router._QUOTA_HISTORY_LOG", new=nonexistent):
        r = client.get("/api/quota/history")

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["snapshots_count"] == 0
    assert body["aggregated"] == {}
    assert body["window"] == "24h"


# ── Тест 4: endpoint фильтрует по window ─────────────────────────────────────


def test_quota_history_filters_by_window(tmp_path: Path) -> None:
    """window=1h — старые снимки (>1h назад) не должны попасть в результат."""
    log_file = tmp_path / "quota_history.jsonl"
    now = time.time()

    # Старый снимок (2 часа назад) — за пределами 1h окна
    old_snap = _make_snapshot(ts=now - 7_300)  # ~2h назад
    # Свежий снимок (30 минут назад) — попадает в 1h окно
    fresh_snap = _make_snapshot(ts=now - 1_800, providers={
        "gemini-cli": {"today_calls": 15},
        "codex-cli": {"today_calls": 3},
    })

    log_file.write_text(old_snap + "\n" + fresh_snap + "\n")

    with patch("src.modules.web_routers.quota_router._QUOTA_HISTORY_LOG", new=log_file):
        client = _make_client(log_file)
        r = client.get("/api/quota/history?window=1h")

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # Только свежий снимок попал в window
    assert body["snapshots_count"] == 1, f"Ожидали 1 снимок, получили {body['snapshots_count']}"
    assert body["window"] == "1h"


# ── Тест 5: aggregated вычисляет max per day per provider ────────────────────


def test_quota_history_aggregated_max_per_day(tmp_path: Path) -> None:
    """aggregated должен содержать max(today_calls) per (date, provider) за период."""
    log_file = tmp_path / "quota_history.jsonl"

    # Используем фиксированный timestamp (1 января 2026 12:00 UTC)
    import datetime as dt

    base_date = dt.datetime(2026, 5, 6, 12, 0, 0, tzinfo=dt.timezone.utc)
    ts1 = base_date.timestamp()           # 12:00 — calls: gemini=10, vertex=5
    ts2 = (base_date + dt.timedelta(hours=2)).timestamp()  # 14:00 — calls: gemini=20, vertex=3
    ts3 = (base_date + dt.timedelta(hours=4)).timestamp()  # 16:00 — calls: gemini=15, vertex=8

    snaps = [
        _make_snapshot(ts=ts1, providers={
            "gemini-cli": {"today_calls": 10},
            "google-vertex": {"today_calls": 5},
        }),
        _make_snapshot(ts=ts2, providers={
            "gemini-cli": {"today_calls": 20},  # max за день
            "google-vertex": {"today_calls": 3},
        }),
        _make_snapshot(ts=ts3, providers={
            "gemini-cli": {"today_calls": 15},
            "google-vertex": {"today_calls": 8},  # max за день
        }),
    ]
    log_file.write_text("\n".join(snaps) + "\n")

    with patch("src.modules.web_routers.quota_router._QUOTA_HISTORY_LOG", new=log_file):
        client = _make_client(log_file)
        r = client.get("/api/quota/history?window=24h")

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["snapshots_count"] == 3

    aggregated = body["aggregated"]
    date_key = "2026-05-06"

    assert date_key in aggregated, f"Ожидали дату {date_key} в aggregated: {aggregated}"
    day = aggregated[date_key]

    # Max за день: gemini=20, vertex=8
    assert day["gemini-cli"] == 20, f"Ожидали max gemini-cli=20, получили {day.get('gemini-cli')}"
    assert day["google-vertex"] == 8, f"Ожидали max google-vertex=8, получили {day.get('google-vertex')}"
