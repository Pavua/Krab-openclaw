# -*- coding: utf-8 -*-
"""
Unit tests для context_router (Phase 2 Part 2A, Session 27).

RouterContext-based extraction — без полного WebApp instance.
Проверяет endpoints /api/context/{checkpoint,transition-pack,latest}.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.context_router import build_context_router


def _build_ctx(
    *,
    deps: dict[str, Any] | None = None,
    project_root: Path | None = None,
) -> RouterContext:
    return RouterContext(
        deps=deps or {},
        project_root=project_root or Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _client(ctx: RouterContext) -> TestClient:
    app = FastAPI()
    app.include_router(build_context_router(ctx))
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /api/context/checkpoint
# ---------------------------------------------------------------------------


def test_context_checkpoint_ok(tmp_path: Path) -> None:
    """checkpoint возвращает artifact_path при успешном run."""
    artifact = tmp_path / "checkpoint_2026.md"
    artifact.write_text("ok", encoding="utf-8")

    def _runner(script_path, timeout_seconds: int) -> dict:
        return {"ok": True, "stdout_tail": "done", "exit_code": 0}

    def _glob(pattern: str):
        return artifact

    ctx = _build_ctx(
        deps={
            "context_run_local_script_helper": _runner,
            "runtime_handoff_latest_path_by_glob_helper": _glob,
        }
    )
    resp = _client(ctx).post("/api/context/checkpoint")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["artifact_type"] == "checkpoint"
    assert data["artifact_path"] == str(artifact)
    assert data["exit_code"] == 0


def test_context_checkpoint_run_failed() -> None:
    """checkpoint 500 если script run failed."""

    def _runner(script_path, timeout_seconds: int) -> dict:
        return {"ok": False, "error": "boom", "exit_code": 1}

    ctx = _build_ctx(deps={"context_run_local_script_helper": _runner})
    resp = _client(ctx).post("/api/context/checkpoint")
    assert resp.status_code == 500
    assert "context_checkpoint_failed" in resp.json()["detail"]


def test_context_checkpoint_no_artifact() -> None:
    """checkpoint 500 если no artifact found by glob."""

    def _runner(script_path, timeout_seconds: int) -> dict:
        return {"ok": True, "exit_code": 0}

    def _glob(pattern: str):
        return None

    ctx = _build_ctx(
        deps={
            "context_run_local_script_helper": _runner,
            "runtime_handoff_latest_path_by_glob_helper": _glob,
        }
    )
    resp = _client(ctx).post("/api/context/checkpoint")
    assert resp.status_code == 500
    assert "no_artifact" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/context/transition-pack
# ---------------------------------------------------------------------------


def test_context_transition_pack_ok(tmp_path: Path) -> None:
    """transition-pack возвращает pack_dir + transfer_prompt."""
    pack_dir = tmp_path / "pack_2026"
    pack_dir.mkdir()
    (pack_dir / "TRANSFER_PROMPT_RU.md").write_text("p", encoding="utf-8")
    (pack_dir / "FILES_TO_ATTACH.txt").write_text("f", encoding="utf-8")

    def _runner(script_path, timeout_seconds: int) -> dict:
        return {"ok": True, "stdout_tail": "", "exit_code": 0}

    def _glob(pattern: str):
        return pack_dir

    ctx = _build_ctx(
        deps={
            "context_run_local_script_helper": _runner,
            "runtime_handoff_latest_path_by_glob_helper": _glob,
        }
    )
    resp = _client(ctx).post("/api/context/transition-pack")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["pack_dir"] == str(pack_dir)
    assert data["transfer_prompt_path"] is not None
    assert data["files_to_attach_path"] is not None


def test_context_transition_pack_no_pack_dir() -> None:
    """transition-pack 500 если no pack dir."""

    def _runner(script_path, timeout_seconds: int) -> dict:
        return {"ok": True, "exit_code": 0}

    def _glob(pattern: str):
        return None

    ctx = _build_ctx(
        deps={
            "context_run_local_script_helper": _runner,
            "runtime_handoff_latest_path_by_glob_helper": _glob,
        }
    )
    resp = _client(ctx).post("/api/context/transition-pack")
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/context/latest
# ---------------------------------------------------------------------------


def test_context_latest_no_artifacts() -> None:
    """latest без артефактов возвращает None во всех полях."""

    def _glob(pattern: str):
        return None

    ctx = _build_ctx(deps={"runtime_handoff_latest_path_by_glob_helper": _glob})
    resp = _client(ctx).get("/api/context/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["latest_checkpoint_path"] is None
    assert data["latest_pack_dir"] is None


def test_context_latest_with_artifacts(tmp_path: Path) -> None:
    """latest возвращает paths когда артефакты существуют."""
    pack_dir = tmp_path / "pack_x"
    pack_dir.mkdir()
    (pack_dir / "TRANSFER_PROMPT_RU.md").write_text("p", encoding="utf-8")
    (pack_dir / "FILES_TO_ATTACH.txt").write_text("f", encoding="utf-8")
    checkpoint = tmp_path / "checkpoint_x.md"
    checkpoint.write_text("c", encoding="utf-8")

    def _glob(pattern: str):
        if "checkpoint" in pattern:
            return checkpoint
        return pack_dir

    ctx = _build_ctx(deps={"runtime_handoff_latest_path_by_glob_helper": _glob})
    resp = _client(ctx).get("/api/context/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["latest_checkpoint_path"] == str(checkpoint)
    assert data["latest_pack_dir"] == str(pack_dir)
    assert data["latest_transfer_prompt_path"] is not None
    assert data["latest_files_to_attach_path"] is not None
