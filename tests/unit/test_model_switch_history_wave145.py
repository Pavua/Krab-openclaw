# -*- coding: utf-8 -*-
"""
Unit tests для ``src.core.model_switch_history`` — Wave 145 (Session 53).

Покрывает:
- log_switch + query_recent round-trip
- FIFO trim до 100 записей
- Atomic write через tempfile + os.replace
- Corrupt JSON → graceful empty state
- to_json_safe возвращает копии (не references)
- configure_default_path переинициализирует store
- /api/models/registry endpoint включает history field после log_switch
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.model_switch_history import ModelSwitchHistory
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.models_admin_router import build_models_admin_router

# ── Helpers / fakes ────────────────────────────────────────────────────────


def _make_history(tmp_path: Path, *, fixed_clock: list[datetime] | None = None) -> ModelSwitchHistory:
    """Создаёт изолированный ModelSwitchHistory с tmp_path storage.

    `fixed_clock` — mutable list для управления временем из теста (если None,
    используется реальный datetime.now).
    """
    storage = tmp_path / "model_switch_history.json"
    if fixed_clock is not None:
        return ModelSwitchHistory(
            storage_path=storage,
            now_fn=lambda: fixed_clock[0],
        )
    return ModelSwitchHistory(storage_path=storage)


class _FakeMM:
    def __init__(self) -> None:
        self.active_model_id = "google-vertex/gemini-3-pro-preview"

    def set_model(self, model_id: str) -> None:
        self.active_model_id = model_id

    def set_provider(self, provider: str) -> None:
        self.active_model_id = f"mode:{provider}"


class _FakeOC:
    def get_last_runtime_route(self) -> dict[str, Any]:
        return {
            "channel": "cloud",
            "model": "google-vertex/gemini-3-pro-preview",
            "provider": "google-vertex",
            "status": "ok",
            "timestamp": 1778617439,
        }


class _FakeQuarantine:
    def list_entries(self) -> list[dict[str, Any]]:
        return []


# ── log_switch + query_recent ──────────────────────────────────────────────


def test_log_switch_and_query_recent_round_trip(tmp_path: Path) -> None:
    """log_switch добавляет запись, query_recent её возвращает."""
    h = _make_history(tmp_path)
    entry = h.log_switch(
        by="owner_panel",
        from_provider="google-vertex",
        from_model="google-vertex/gemini-3-pro-preview",
        to_provider="anthropic-vertex",
        to_model="anthropic-vertex/claude-sonnet-4-5",
        reason="manual_switch",
        success=True,
    )
    assert entry["by"] == "owner_panel"
    assert entry["from_model"] == "google-vertex/gemini-3-pro-preview"
    assert entry["to_model"] == "anthropic-vertex/claude-sonnet-4-5"
    assert entry["success"] is True
    # ISO ts формат.
    datetime.fromisoformat(entry["ts"])

    recent = h.query_recent(limit=5)
    assert len(recent) == 1
    assert recent[0]["to_model"] == "anthropic-vertex/claude-sonnet-4-5"
    # Возвращены копии — мутация не влияет на store.
    recent[0]["to_model"] = "MUTATED"
    again = h.query_recent(limit=5)
    assert again[0]["to_model"] == "anthropic-vertex/claude-sonnet-4-5"


def test_log_switch_persists_to_disk(tmp_path: Path) -> None:
    """После log_switch файл существует и содержит JSON list."""
    storage = tmp_path / "model_switch_history.json"
    h = ModelSwitchHistory(storage_path=storage)
    h.log_switch(
        by="cli",
        from_provider="codex-cli",
        from_model="codex-cli/gpt-5.5",
        to_provider="google-vertex",
        to_model="google-vertex/gemini-3-pro-preview",
        reason="quota_exhausted",
        success=True,
    )
    assert storage.exists()
    data = json.loads(storage.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["reason"] == "quota_exhausted"
    assert data[0]["by"] == "cli"


def test_fifo_trim_to_100_entries(tmp_path: Path) -> None:
    """После 105 log_switch → query_recent(200) возвращает только 100."""
    h = _make_history(tmp_path)
    for i in range(105):
        h.log_switch(
            by="test",
            from_provider="a",
            from_model=f"model-{i}",
            to_provider="b",
            to_model=f"model-{i + 1}",
            reason="fifo_test",
        )
    assert h.size() == 100
    recent = h.query_recent(limit=200)
    assert len(recent) == 100
    # Самые старые (0..4) выкинуты, остались 5..104.
    assert recent[0]["from_model"] == "model-5"
    assert recent[-1]["from_model"] == "model-104"


def test_load_from_disk_persists_across_instances(tmp_path: Path) -> None:
    """Записанное одним instance читается другим (persistence)."""
    storage = tmp_path / "model_switch_history.json"
    h1 = ModelSwitchHistory(storage_path=storage)
    h1.log_switch(
        by="A",
        from_provider="p1",
        from_model="m1",
        to_provider="p2",
        to_model="m2",
    )
    h2 = ModelSwitchHistory(storage_path=storage)
    recent = h2.query_recent()
    assert len(recent) == 1
    assert recent[0]["from_model"] == "m1"
    assert recent[0]["to_model"] == "m2"


def test_corrupt_json_returns_empty_state(tmp_path: Path) -> None:
    """Если JSON битый — load возвращает empty + не ронят store."""
    storage = tmp_path / "model_switch_history.json"
    storage.write_text("{not valid json{{", encoding="utf-8")
    h = ModelSwitchHistory(storage_path=storage)
    assert h.size() == 0
    # Дальше можно нормально писать.
    h.log_switch(
        by="recovery",
        from_provider="x",
        from_model="x/m1",
        to_provider="y",
        to_model="y/m2",
    )
    assert h.size() == 1


def test_malformed_root_list_returns_empty_state(tmp_path: Path) -> None:
    """JSON-valid но корень не list (например dict) → empty state."""
    storage = tmp_path / "model_switch_history.json"
    storage.write_text(json.dumps({"oops": "wrong shape"}), encoding="utf-8")
    h = ModelSwitchHistory(storage_path=storage)
    assert h.size() == 0


def test_failed_switch_logged_with_success_false(tmp_path: Path) -> None:
    """log_switch с success=False сохраняет флаг."""
    h = _make_history(tmp_path)
    h.log_switch(
        by="owner_panel",
        from_provider="a",
        from_model="a/m1",
        to_provider="b",
        to_model="b/m2",
        reason="value_error:bad model",
        success=False,
    )
    recent = h.query_recent()
    assert recent[0]["success"] is False
    assert "bad model" in recent[0]["reason"]


def test_configure_default_path_reinitializes(tmp_path: Path) -> None:
    """configure_default_path сбрасывает entries и подгружает с нового пути."""
    h = ModelSwitchHistory()
    storage = tmp_path / "model_switch_history.json"
    storage.write_text(
        json.dumps(
            [
                {
                    "ts": "2026-01-01T00:00:00+00:00",
                    "by": "preload",
                    "from_provider": "x",
                    "from_model": "x/m1",
                    "to_provider": "y",
                    "to_model": "y/m2",
                    "reason": "",
                    "success": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    h.configure_default_path(storage)
    recent = h.query_recent()
    assert len(recent) == 1
    assert recent[0]["by"] == "preload"


def test_query_recent_limit_zero_returns_empty(tmp_path: Path) -> None:
    """query_recent(0) и query_recent(-5) → []."""
    h = _make_history(tmp_path)
    h.log_switch(
        by="x",
        from_provider="p",
        from_model="m",
        to_provider="p",
        to_model="m2",
    )
    assert h.query_recent(0) == []
    assert h.query_recent(-3) == []


# ── /api/models/registry integration ───────────────────────────────────────


def _build_registry_ctx(tmp_path: Path) -> RouterContext:
    """RouterContext без black_box (чтобы тестировать ТОЛЬКО новый store)."""
    deps: dict[str, Any] = {
        "router": object(),
        "resolve_local_runtime_truth_helper": lambda _r: {},
    }
    return RouterContext(
        deps=deps,
        project_root=tmp_path,
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda *_a, **_kw: None,
    )


def test_registry_includes_history_from_persistent_store(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """После log_switch в store → /api/models/registry.history содержит запись."""
    storage = tmp_path / "model_switch_history.json"
    fresh = ModelSwitchHistory(storage_path=storage)
    fresh.log_switch(
        by="owner_panel",
        from_provider="google-vertex",
        from_model="google-vertex/gemini-3-pro-preview",
        to_provider="anthropic-vertex",
        to_model="anthropic-vertex/claude-sonnet-4-5",
        reason="manual_switch",
        success=True,
    )
    # Подменяем module-level singleton на наш тестовый instance, чтобы router
    # читал его через `from src.core.model_switch_history import …`.
    monkeypatch.setattr(
        "src.core.model_switch_history.model_switch_history",
        fresh,
    )

    ctx = _build_registry_ctx(tmp_path)
    app = FastAPI()
    app.include_router(build_models_admin_router(ctx))
    client = TestClient(app)

    with patch("src.model_manager.model_manager", _FakeMM()), patch(
        "src.openclaw_client.openclaw_client", _FakeOC()
    ), patch(
        "src.core.provider_quarantine.provider_quarantine", _FakeQuarantine()
    ), patch(
        "src.integrations.codex_quota_state.is_codex_disabled",
        lambda: False,
    ), patch(
        "src.core.openclaw_runtime_models.get_runtime_primary_model",
        lambda: "google-vertex/gemini-3-pro-preview",
    ):
        resp = client.get("/api/models/registry")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["history"], list)
    assert len(data["history"]) == 1
    entry = data["history"][0]
    assert entry["from"] == "google-vertex/gemini-3-pro-preview"
    assert entry["to"] == "anthropic-vertex/claude-sonnet-4-5"
    assert entry["actor"] == "owner_panel"
    # Wave 145 расширенные поля.
    assert entry.get("reason") == "manual_switch"
    assert entry.get("success") is True


def test_post_switch_writes_history_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """POST /api/admin/model/switch добавляет запись в history."""
    storage = tmp_path / "model_switch_history.json"
    fresh = ModelSwitchHistory(storage_path=storage)
    monkeypatch.setattr(
        "src.core.model_switch_history.model_switch_history",
        fresh,
    )
    monkeypatch.delenv("WEB_API_KEY", raising=False)

    ctx = _build_registry_ctx(tmp_path)
    app = FastAPI()
    app.include_router(build_models_admin_router(ctx))
    client = TestClient(app)

    fake_mm = _FakeMM()
    with patch("src.model_manager.model_manager", fake_mm), patch(
        "src.openclaw_client.openclaw_client", _FakeOC()
    ), patch(
        "src.core.provider_quarantine.provider_quarantine", _FakeQuarantine()
    ), patch(
        "src.integrations.codex_quota_state.is_codex_disabled",
        lambda: False,
    ), patch(
        "src.core.openclaw_runtime_models.get_runtime_primary_model",
        lambda: fake_mm.active_model_id,
    ):
        resp = client.post(
            "/api/admin/model/switch",
            json={
                "provider": "google-vertex",
                "model": "google-vertex/gemini-2.5-pro",
                "reason": "user_request",
            },
        )

    assert resp.status_code == 200
    # Запись появилась в фресном store.
    assert fresh.size() == 1
    recorded = fresh.query_recent()[0]
    assert recorded["to_model"] == "google-vertex/gemini-2.5-pro"
    assert recorded["from_model"] == "google-vertex/gemini-3-pro-preview"
    assert recorded["reason"] == "user_request"
    assert recorded["success"] is True
    assert recorded["by"] == "owner_panel"
