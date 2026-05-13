# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.scheduler_admin_router`` — Wave 218.

Покрытие: factory build, list endpoint (messages + history + swarm jobs),
cancel endpoint (success / not-found / already / write-access), helpers
(_preview, _iso_to_epoch, _validate_record_id), HTML page render.

Все file IO мокаются через tmp_path / monkeypatch для изоляции от
реального ``~/.openclaw`` и от ``data/message_scheduler/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.modules.web_routers import scheduler_admin_router as sar
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.scheduler_admin_router import build_scheduler_admin_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(
    *,
    deps: dict[str, Any] | None = None,
    write_access_raises: Exception | None = None,
) -> TestClient:
    def _assert_write(*a: Any, **kw: Any) -> None:
        if write_access_raises is not None:
            raise write_access_raises

    ctx = RouterContext(
        deps=deps or {},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=_assert_write,
    )
    app = FastAPI()
    app.include_router(build_scheduler_admin_router(ctx))
    return TestClient(app)


def _seed_messages_store(tmp_path: Path, records: list[dict[str, Any]]) -> Path:
    """Записывает scheduled.json и патчит msg_scheduler_store.storage_path."""
    from src.core import message_scheduler as ms

    storage = tmp_path / "scheduled.json"
    storage.write_text(
        json.dumps({"records": records}, ensure_ascii=False),
        encoding="utf-8",
    )
    ms.msg_scheduler_store.storage_path = storage
    return storage


def _seed_swarm_jobs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, jobs: list[dict[str, Any]]
) -> Path:
    path = tmp_path / "swarm_recurring_jobs.json"
    path.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sar, "_SWARM_SCHED_STATE", path)
    return path


# ---------------------------------------------------------------------------
# Helpers — _preview / _iso_to_epoch / _validate_record_id
# ---------------------------------------------------------------------------


def test_preview_short_returns_as_is() -> None:
    assert sar._preview("hello") == "hello"


def test_preview_long_truncates_with_ellipsis() -> None:
    text = "a" * 80
    out = sar._preview(text, limit=50)
    assert len(out) <= 52  # 50 chars + "…"
    assert out.endswith("…")


def test_iso_to_epoch_valid() -> None:
    epoch = sar._iso_to_epoch("2026-05-13T12:00:00+00:00")
    assert isinstance(epoch, float)
    assert epoch > 1_700_000_000


def test_iso_to_epoch_empty_or_invalid() -> None:
    assert sar._iso_to_epoch("") is None
    assert sar._iso_to_epoch("not-a-date") is None


def test_validate_record_id_ok() -> None:
    assert sar._validate_record_id("abcd1234") == "abcd1234"


def test_validate_record_id_rejects_bad() -> None:
    with pytest.raises(HTTPException) as exc:
        sar._validate_record_id("../etc/passwd")
    assert exc.value.status_code == 400


def test_validate_record_id_rejects_empty() -> None:
    with pytest.raises(HTTPException):
        sar._validate_record_id("")


# ---------------------------------------------------------------------------
# /api/admin/scheduler/list
# ---------------------------------------------------------------------------


def test_list_empty_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Указываем несуществующий путь — все источники возвращают пусто.
    from src.core import message_scheduler as ms

    ms.msg_scheduler_store.storage_path = tmp_path / "missing.json"
    monkeypatch.setattr(sar, "_SWARM_SCHED_STATE", tmp_path / "missing_swarm.json")

    client = _make_client()
    res = client.get("/api/admin/scheduler/list")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["messages"] == []
    assert data["messages_history"] == []
    assert data["swarm_jobs"] == []
    assert data["messages_active_count"] == 0


def test_list_returns_active_messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sar, "_SWARM_SCHED_STATE", tmp_path / "missing.json")
    _seed_messages_store(
        tmp_path,
        [
            {
                "record_id": "aa11bb22",
                "chat_id": "1234567",
                "text": "Привет, это длинное сообщение " * 5,
                "schedule_time_iso": "2026-12-01T10:00:00+00:00",
                "tg_message_id": 999,
                "created_at_iso": "2026-05-13T09:00:00+00:00",
                "status": "pending",
            },
            {
                "record_id": "cc33dd44",
                "chat_id": "1234567",
                "text": "cancelled",
                "schedule_time_iso": "2026-05-13T09:00:00+00:00",
                "tg_message_id": 1000,
                "created_at_iso": "2026-05-13T08:55:00+00:00",
                "status": "cancelled",
            },
        ],
    )

    client = _make_client()
    res = client.get("/api/admin/scheduler/list")
    assert res.status_code == 200
    data = res.json()
    assert data["messages_active_count"] == 1
    assert data["messages"][0]["record_id"] == "aa11bb22"
    # preview обрезан
    assert "…" in data["messages"][0]["text_preview"]
    assert data["messages"][0]["text_len"] > 50
    # History содержит cancelled
    assert data["messages_history_count"] == 1
    assert data["messages_history"][0]["status"] == "cancelled"


def test_list_returns_swarm_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import message_scheduler as ms

    ms.msg_scheduler_store.storage_path = tmp_path / "missing.json"
    _seed_swarm_jobs(
        monkeypatch,
        tmp_path,
        [
            {
                "job_id": "deadbeef",
                "team": "traders",
                "topic": "BTC analysis daily",
                "interval_sec": 14400,
                "workflow_type": "research",
                "next_run_at": "2026-05-13T14:00:00+00:00",
                "total_runs": 7,
                "enabled": True,
            }
        ],
    )

    client = _make_client()
    res = client.get("/api/admin/scheduler/list")
    assert res.status_code == 200
    data = res.json()
    assert data["swarm_jobs_count"] == 1
    j = data["swarm_jobs"][0]
    assert j["job_id"] == "deadbeef"
    assert j["team"] == "traders"
    assert j["enabled"] is True
    assert j["interval_sec"] == 14400
    assert j["next_run_epoch"] is not None


def test_list_ignores_corrupt_swarm_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import message_scheduler as ms

    ms.msg_scheduler_store.storage_path = tmp_path / "missing.json"
    bad = tmp_path / "swarm_bad.json"
    bad.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(sar, "_SWARM_SCHED_STATE", bad)

    client = _make_client()
    res = client.get("/api/admin/scheduler/list")
    assert res.status_code == 200
    assert res.json()["swarm_jobs"] == []


# ---------------------------------------------------------------------------
# /api/admin/scheduler/cancel/{record_id}
# ---------------------------------------------------------------------------


def test_cancel_marks_pending_as_cancelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sar, "_SWARM_SCHED_STATE", tmp_path / "missing.json")
    _seed_messages_store(
        tmp_path,
        [
            {
                "record_id": "aabbccdd",
                "chat_id": "555",
                "text": "test",
                "schedule_time_iso": "2026-12-01T10:00:00+00:00",
                "tg_message_id": 0,  # без TG — попадаем в no_tg_message_id branch
                "created_at_iso": "2026-05-13T09:00:00+00:00",
                "status": "pending",
            }
        ],
    )

    client = _make_client()
    res = client.post("/api/admin/scheduler/cancel/aabbccdd")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["marked"] is True
    assert body["tg_deleted"] is False

    # Проверяем что store обновился
    from src.core.message_scheduler import msg_scheduler_store

    rec = msg_scheduler_store.get("aabbccdd")
    assert rec is not None
    assert rec.status == "cancelled"


def test_cancel_404_when_record_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import message_scheduler as ms

    ms.msg_scheduler_store.storage_path = tmp_path / "empty.json"
    ms.msg_scheduler_store.storage_path.write_text('{"records": []}', encoding="utf-8")
    monkeypatch.setattr(sar, "_SWARM_SCHED_STATE", tmp_path / "missing.json")

    client = _make_client()
    res = client.post("/api/admin/scheduler/cancel/aabbccdd")
    assert res.status_code == 404


def test_cancel_already_cancelled_returns_already(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sar, "_SWARM_SCHED_STATE", tmp_path / "missing.json")
    _seed_messages_store(
        tmp_path,
        [
            {
                "record_id": "aabbccdd",
                "chat_id": "1",
                "text": "x",
                "schedule_time_iso": "",
                "tg_message_id": 0,
                "created_at_iso": "",
                "status": "cancelled",
            }
        ],
    )

    client = _make_client()
    res = client.post("/api/admin/scheduler/cancel/aabbccdd")
    assert res.status_code == 200
    assert res.json()["already"] == "cancelled"


def test_cancel_rejects_invalid_record_id() -> None:
    client = _make_client()
    res = client.post("/api/admin/scheduler/cancel/..%2Fbad")
    assert res.status_code in (400, 404)


def test_cancel_blocked_without_write_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sar, "_SWARM_SCHED_STATE", tmp_path / "missing.json")
    _seed_messages_store(
        tmp_path,
        [
            {
                "record_id": "aabbccdd",
                "chat_id": "1",
                "text": "x",
                "schedule_time_iso": "2026-12-01T10:00:00+00:00",
                "tg_message_id": 0,
                "created_at_iso": "",
                "status": "pending",
            }
        ],
    )

    client = _make_client(write_access_raises=HTTPException(status_code=403, detail="forbidden"))
    res = client.post("/api/admin/scheduler/cancel/aabbccdd")
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# /admin/scheduler HTML page
# ---------------------------------------------------------------------------


def test_admin_scheduler_html_returns_page() -> None:
    client = _make_client()
    res = client.get("/admin/scheduler")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    body = res.text
    assert "Krab" in body and "Scheduler" in body
    # Убеждаемся что XSS-safe: используем textContent, а не innerHTML.
    assert "innerHTML" not in body
    assert "textContent" in body
    # Polling
    assert "setInterval" in body and "/api/admin/scheduler/list" in body
