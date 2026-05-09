# -*- coding: utf-8 -*-
"""Wave 51-D: тесты для snapshots + route-switches observability tabs.

Покрывают новые endpoints:
- GET /api/observability/snapshots — list snapshots from
  ``~/.openclaw/krab_runtime_state/snapshots/``.
- GET /api/observability/route-switches — tail
  ``~/.openclaw/krab_runtime_state/route_switches.jsonl``.

Тесты используют tmp_path + monkeypatch для изоляции от реального state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.observability_router import build_observability_router


def _build_ctx() -> RouterContext:
    """Минимальный RouterContext для unit-тестов."""
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _api_client() -> TestClient:
    app = FastAPI()
    app.include_router(build_observability_router(_build_ctx()))
    return TestClient(app)


# ───────────────────────── snapshots fixtures ─────────────────────────────


@pytest.fixture
def temp_snapshot_dir(tmp_path, monkeypatch):
    """Подменяет _runtime_state_dir() для StateSnapshotManager на tmp_path.

    Создаём структуру ``<tmp>/snapshots/<ts>/<file>.bak`` и возвращаем
    корневой путь runtime_state, чтобы тесты могли создавать фикстуры.
    """
    runtime_state = tmp_path / "krab_runtime_state"
    runtime_state.mkdir(parents=True, exist_ok=True)
    # Подменяем helper, который возвращает root.
    monkeypatch.setattr(
        "src.core.state_snapshots._runtime_state_dir",
        lambda: runtime_state,
    )
    return runtime_state


def _make_snapshot(runtime_state: Path, ts: str, files: dict[str, bytes]) -> Path:
    """Создаёт snapshot-директорию ``snapshots/<ts>/<name>.bak`` с контентом."""
    snap_dir = runtime_state / "snapshots" / ts
    snap_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (snap_dir / name).write_bytes(content)
    return snap_dir


# ───────────────────── snapshots endpoint tests ───────────────────────────


def test_get_snapshots_endpoint_returns_list(temp_snapshot_dir: Path) -> None:
    """Mock filesystem с двумя snapshot-папками → GET возвращает их list."""
    _make_snapshot(
        temp_snapshot_dir,
        "20260510T000000Z",
        {"chat_response_policy.json.bak": b"{}", "codex_quota_state.json.bak": b"{}"},
    )
    _make_snapshot(
        temp_snapshot_dir,
        "20260510T010000Z",
        {"route_switches.jsonl.bak": b"line1\nline2\n"},
    )

    client = _api_client()
    res = client.get("/api/observability/snapshots")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["count"] == 2
    # Reverse chronological: новые первыми.
    assert data["snapshots"][0]["timestamp"] == "20260510T010000Z"
    assert data["snapshots"][1]["timestamp"] == "20260510T000000Z"


def test_get_snapshots_includes_size_kb(temp_snapshot_dir: Path) -> None:
    """Размер в KB должен быть рассчитан корректно (≥ 0.1 для 100+ байт)."""
    payload = b"x" * 2048  # 2 KB
    _make_snapshot(
        temp_snapshot_dir,
        "20260510T000000Z",
        {"chat_response_policy.json.bak": payload},
    )

    client = _api_client()
    res = client.get("/api/observability/snapshots")
    assert res.status_code == 200
    data = res.json()
    snap = data["snapshots"][0]
    assert snap["files_count"] == 1
    assert snap["total_size_kb"] == 2.0
    assert "created_at" in snap
    assert isinstance(snap["created_at"], (int, float))


def test_get_snapshots_returns_empty_when_dir_missing(tmp_path, monkeypatch) -> None:
    """Если snapshots/ directory отсутствует — возвращается пустой list, без ошибок."""
    runtime_state = tmp_path / "missing_runtime"
    # Не создаём — directory не существует.
    monkeypatch.setattr(
        "src.core.state_snapshots._runtime_state_dir",
        lambda: runtime_state,
    )

    client = _api_client()
    res = client.get("/api/observability/snapshots")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["count"] == 0
    assert data["snapshots"] == []


def test_get_snapshots_respects_limit(temp_snapshot_dir: Path) -> None:
    """Параметр ?limit ограничивает count returned snapshots."""
    for i in range(5):
        _make_snapshot(
            temp_snapshot_dir,
            f"2026051{i}T000000Z",
            {"chat_response_policy.json.bak": b"{}"},
        )

    client = _api_client()
    res = client.get("/api/observability/snapshots?limit=2")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 2


# ─────────────────── route-switches fixtures ──────────────────────────────


@pytest.fixture
def temp_route_switches_log(tmp_path, monkeypatch):
    """Подменяет route_switch_log.LOG_FILE на tmp_path."""
    log_path = tmp_path / "route_switches.jsonl"
    monkeypatch.setattr(
        "src.integrations.route_switch_log.LOG_FILE",
        log_path,
    )
    return log_path


# ──────────────── route-switches endpoint tests ──────────────────────────


def test_get_route_switches_returns_tail(temp_route_switches_log: Path) -> None:
    """JSONL → последние строки парсятся и возвращаются reverse chronological."""
    entries = [
        {"ts": "2026-05-10T00:10:00+00:00", "from": "a", "to": "b", "reason": "quota"},
        {"ts": "2026-05-10T00:11:00+00:00", "from": "c", "to": "d", "reason": "timeout"},
        {"ts": "2026-05-10T00:12:00+00:00", "from": "e", "to": "f", "reason": "recovery"},
    ]
    temp_route_switches_log.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )

    client = _api_client()
    res = client.get("/api/observability/route-switches?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["count"] == 3
    # Reverse: newest first.
    assert data["switches"][0]["reason"] == "recovery"
    assert data["switches"][2]["reason"] == "quota"


def test_get_route_switches_handles_malformed_lines(temp_route_switches_log: Path) -> None:
    """Corrupt JSON line должна тихо пропускаться, остальные — возвращаться."""
    valid = json.dumps({"ts": "2026-05-10T00:10:00+00:00", "from": "a", "to": "b", "reason": "quota"})
    invalid = "{{ bad json garbage"
    valid2 = json.dumps({"ts": "2026-05-10T00:11:00+00:00", "from": "c", "to": "d", "reason": "timeout"})
    temp_route_switches_log.write_text(
        f"{valid}\n{invalid}\n{valid2}\n",
        encoding="utf-8",
    )

    client = _api_client()
    res = client.get("/api/observability/route-switches")
    assert res.status_code == 200
    data = res.json()
    # Только 2 valid записи, malformed пропущена.
    assert data["count"] == 2
    reasons = [s["reason"] for s in data["switches"]]
    assert "quota" in reasons
    assert "timeout" in reasons


def test_get_route_switches_empty_when_file_missing(tmp_path, monkeypatch) -> None:
    """Файл не существует → пустой ответ, без ошибок."""
    missing = tmp_path / "no_such_file.jsonl"
    monkeypatch.setattr(
        "src.integrations.route_switch_log.LOG_FILE",
        missing,
    )

    client = _api_client()
    res = client.get("/api/observability/route-switches")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["count"] == 0
    assert data["switches"] == []


def test_get_route_switches_respects_limit(temp_route_switches_log: Path) -> None:
    """Параметр ?limit ограничивает tail size."""
    entries = [
        {"ts": f"2026-05-10T00:{i:02d}:00+00:00", "from": "x", "to": "y", "reason": "quota"}
        for i in range(10)
    ]
    temp_route_switches_log.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )

    client = _api_client()
    res = client.get("/api/observability/route-switches?limit=3")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 3


def test_existing_runs_endpoint_still_works() -> None:
    """Регрессионный тест: existing /runs endpoint не сломан Wave 51-D."""
    client = _api_client()
    res = client.get("/api/observability/runs?limit=10")
    # Должен вернуть 200 даже с пустым логом.
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "runs" in data
