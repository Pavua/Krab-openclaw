# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.translator_admin_router`` — Wave 216.

Покрытие сосредоточено на factory + snapshot builders + endpoints.
Используем tmp_path с monkeypatch для подмены translator JSON-файлов,
чтобы тесты не зависели от реального ``data/translator/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers import translator_admin_router as tar
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.translator_admin_router import (
    build_translator_admin_router,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_client() -> TestClient:
    """Создаёт TestClient с минимальным RouterContext."""
    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    app = FastAPI()
    app.include_router(build_translator_admin_router(ctx))
    return TestClient(app)


@pytest.fixture
def fake_session_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Создаёт fake session_state.json и подменяет env path."""
    path = tmp_path / "session_state.json"
    payload = {
        "session_status": "active",
        "session_id": "sess-42",
        "active_session_label": "trial-1",
        "translation_muted": False,
        "active_chats": [123, 456],
        "last_language_pair": "es-ru",
        "history": [
            {
                "src_lang": "es",
                "tgt_lang": "ru",
                "original": "hola",
                "translation": "привет",
                "latency_ms": 320,
                "timestamp": "2026-05-13T10:00:00Z",
            }
        ],
        "last_event": "session_started",
        "stats": {"total_translations": 7, "total_latency_ms": 2100},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("KRAB_TRANSLATOR_SESSION_STATE_PATH", str(path))
    return path


@pytest.fixture
def fake_profile_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Создаёт fake runtime_profile.json и подменяет env path."""
    path = tmp_path / "runtime_profile.json"
    payload = {
        "language_pair": "en-ru",
        "translation_mode": "auto_to_ru",
        "voice_strategy": "subtitles-first",
        "target_device": "iphone_companion",
        "ordinary_calls_enabled": True,
        "internet_calls_enabled": False,
        "subtitles_enabled": True,
        "timeline_enabled": True,
        "summary_enabled": True,
        "diagnostics_enabled": False,
        "quick_phrases": ["спасибо", "повторите"],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("KRAB_TRANSLATOR_PROFILE_PATH", str(path))
    return path


@pytest.fixture
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Изолирует все translator пути в tmp_path (файлов нет — skeleton)."""
    monkeypatch.setenv(
        "KRAB_TRANSLATOR_SESSION_STATE_PATH",
        str(tmp_path / "no_session.json"),
    )
    monkeypatch.setenv(
        "KRAB_TRANSLATOR_PROFILE_PATH",
        str(tmp_path / "no_profile.json"),
    )
    monkeypatch.setenv(
        "KRAB_TRANSLATOR_FINISH_GATE_PATH",
        str(tmp_path / "no_gate.json"),
    )
    monkeypatch.setenv(
        "KRAB_TRANSLATOR_MOBILE_ONBOARDING_PATH",
        str(tmp_path / "no_mobile.json"),
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def test_build_session_state_snapshot_persisted(fake_session_file: Path) -> None:
    """Если файл существует — читаем persisted state."""
    snap = tar._build_session_state_snapshot()
    assert snap["available"] is True
    assert snap["from_default"] is False
    state = snap["state"]
    assert state["session_status"] == "active"
    assert state["session_id"] == "sess-42"
    assert state["stats"]["total_translations"] == 7


def test_build_session_state_snapshot_skeleton(isolated_paths: Path) -> None:
    """Если файла нет — возвращаем skeleton с available=False."""
    snap = tar._build_session_state_snapshot()
    assert snap["available"] is False
    assert snap["error"] == "file_not_found"
    assert snap["from_default"] is True
    assert snap["state"].get("session_status") == "idle"


def test_build_runtime_profile_snapshot_persisted(fake_profile_file: Path) -> None:
    """Persisted runtime_profile читается и возвращается."""
    snap = tar._build_runtime_profile_snapshot()
    assert snap["available"] is True
    assert snap["from_default"] is False
    assert snap["profile"]["language_pair"] == "en-ru"
    # Allowed sets должны быть включены.
    assert "es-ru" in snap["allowed"]["language_pairs"]
    assert "bilingual" in snap["allowed"]["translation_modes"]


def test_build_runtime_profile_snapshot_skeleton(isolated_paths: Path) -> None:
    """Без файла profile = defaults, from_default=True."""
    snap = tar._build_runtime_profile_snapshot()
    assert snap["available"] is False
    assert snap["from_default"] is True
    # Дефолт обязан содержать language_pair.
    assert snap["profile"].get("language_pair") == "es-ru"


def test_build_engine_config_snapshot_available() -> None:
    """Engine config всегда доступен — модуль импортируется в проекте."""
    snap = tar._build_engine_config_snapshot()
    assert snap["available"] is True
    assert snap["preferred_model"] == "google/gemini-3-flash-preview"
    assert snap["disable_tools"] is True


def test_build_finish_gate_and_mobile_snapshots_missing(
    isolated_paths: Path,
) -> None:
    """Без ops-артефактов — snapshot.available=False."""
    gate = tar._build_finish_gate_snapshot()
    mobile = tar._build_mobile_onboarding_snapshot()
    assert gate["available"] is False
    assert gate["error"] == "file_not_found"
    assert mobile["available"] is False


def test_build_metrics_snapshot_does_not_raise() -> None:
    """Metric collection — best-effort, не должен бросать наружу."""
    snap = tar._build_metrics_snapshot()
    # available может быть True/False в зависимости от prometheus_client,
    # но структура должна быть корректной.
    assert "samples" in snap
    assert isinstance(snap["samples"], list)


def test_read_json_safe_invalid(tmp_path: Path) -> None:
    """Файл с битым JSON — error возвращается, не raise."""
    path = tmp_path / "broken.json"
    path.write_text("not a json {", encoding="utf-8")
    available, data, error = tar._read_json_safe(path)
    assert available is False
    assert data is None
    assert "json_decode_failed" in (error or "")


def test_read_json_safe_oversized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Слишком большой файл отклоняется без чтения."""
    path = tmp_path / "huge.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tar, "_MAX_FILE_BYTES", 1)
    available, _, error = tar._read_json_safe(path)
    assert available is False
    assert error is not None and error.startswith("file_too_large")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_state_endpoint_returns_200_with_skeleton(isolated_paths: Path) -> None:
    """GET /api/admin/translator/state — 200 даже когда нет файлов."""
    client = _make_client()
    resp = client.get("/api/admin/translator/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["session"]["available"] is False
    assert data["profile"]["available"] is False
    assert data["engine"]["available"] is True
    assert data["finish_gate"]["available"] is False
    assert data["mobile_onboarding"]["available"] is False
    assert "metrics" in data


def test_state_endpoint_with_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_session_file: Path,
    fake_profile_file: Path,
) -> None:
    """Когда session+profile есть — endpoint их отражает."""
    # Ops пути нужно явно изолировать в tmp_path (не путать с реальными).
    monkeypatch.setenv("KRAB_TRANSLATOR_FINISH_GATE_PATH", str(tmp_path / "no_gate.json"))
    monkeypatch.setenv(
        "KRAB_TRANSLATOR_MOBILE_ONBOARDING_PATH",
        str(tmp_path / "no_mobile.json"),
    )
    client = _make_client()
    resp = client.get("/api/admin/translator/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session"]["available"] is True
    assert data["session"]["state"]["session_id"] == "sess-42"
    assert data["profile"]["available"] is True
    assert data["profile"]["profile"]["language_pair"] == "en-ru"


def test_html_page_renders(isolated_paths: Path) -> None:
    """GET /admin/translator — 200 HTML."""
    client = _make_client()
    resp = client.get("/admin/translator")
    assert resp.status_code == 200
    body = resp.text
    assert "Krab · Translator Admin" in body
    # Sanity: критичные элементы JS присутствуют.
    assert "/api/admin/translator/state" in body
    assert "session-cards" in body
    assert "profile-cards" in body


def test_builder_returns_router() -> None:
    """build_translator_admin_router возвращает APIRouter с нужными routes."""
    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    router = build_translator_admin_router(ctx)
    paths = {r.path for r in router.routes}  # type: ignore[attr-defined]
    assert "/api/admin/translator/state" in paths
    assert "/admin/translator" in paths
