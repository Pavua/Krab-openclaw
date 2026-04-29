# -*- coding: utf-8 -*-
"""
Context router — Phase 2 Part 2A extraction (Session 27).

Endpoints (anti-413 transition workflow):
- POST /api/context/checkpoint — создает checkpoint для нового чата
- POST /api/context/transition-pack — собирает transition-pack
- GET /api/context/latest — ссылки на последние артефакты

Helpers (injected via deps_dict из ``_make_router_context``):
- ``context_run_local_script_helper(script_path, timeout_seconds)`` — запуск
  локального .command скрипта (late-bound для тестируемости).
- ``runtime_handoff_latest_path_by_glob_helper(pattern)`` — overlap с system_router.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query

from ._context import RouterContext


def build_context_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с /api/context/* endpoints."""
    router = APIRouter(tags=["context"])

    def _run_local_script(script_path, timeout_seconds: int) -> dict:
        helper = ctx.get_dep("context_run_local_script_helper")
        if helper is None:
            raise HTTPException(
                status_code=500,
                detail="context_run_local_script_helper_missing",
            )
        return helper(script_path, timeout_seconds=timeout_seconds)

    def _latest_path_by_glob(pattern: str):
        helper = ctx.get_dep("runtime_handoff_latest_path_by_glob_helper")
        if helper is None:
            return None
        return helper(pattern)

    @router.post("/api/context/checkpoint")
    async def context_checkpoint(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ):
        """Создает checkpoint для перехода в новый чат (anti-413)."""
        ctx.assert_write_access(x_krab_web_key, token)
        script_path = ctx.project_root / "new_chat_checkpoint.command"
        run = _run_local_script(script_path, timeout_seconds=120)
        if not bool(run.get("ok")):
            detail = str(run.get("error") or f"exit_code={run.get('exit_code', 1)}")
            raise HTTPException(status_code=500, detail=f"context_checkpoint_failed:{detail}")

        artifact = _latest_path_by_glob("artifacts/context_checkpoints/checkpoint_*.md")
        if artifact is None:
            raise HTTPException(status_code=500, detail="context_checkpoint_failed:no_artifact")

        return {
            "ok": True,
            "artifact_type": "checkpoint",
            "artifact_path": str(artifact),
            "stdout_tail": str(run.get("stdout_tail") or ""),
            "exit_code": int(run.get("exit_code", 0)),
        }

    @router.post("/api/context/transition-pack")
    async def context_transition_pack(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ):
        """Собирает transition-pack для восстановления состояния в новом чате."""
        ctx.assert_write_access(x_krab_web_key, token)
        script_path = ctx.project_root / "build_transition_pack.command"
        run = _run_local_script(script_path, timeout_seconds=180)
        if not bool(run.get("ok")):
            detail = str(run.get("error") or f"exit_code={run.get('exit_code', 1)}")
            raise HTTPException(status_code=500, detail=f"context_transition_pack_failed:{detail}")

        pack_dir = _latest_path_by_glob("artifacts/context_transition/pack_*")
        if pack_dir is None:
            raise HTTPException(
                status_code=500, detail="context_transition_pack_failed:no_pack_dir"
            )

        transfer_prompt = pack_dir / "TRANSFER_PROMPT_RU.md"
        files_to_attach = pack_dir / "FILES_TO_ATTACH.txt"
        return {
            "ok": True,
            "artifact_type": "transition_pack",
            "pack_dir": str(pack_dir),
            "transfer_prompt_path": str(transfer_prompt) if transfer_prompt.exists() else None,
            "files_to_attach_path": str(files_to_attach) if files_to_attach.exists() else None,
            "stdout_tail": str(run.get("stdout_tail") or ""),
            "exit_code": int(run.get("exit_code", 0)),
        }

    @router.get("/api/context/latest")
    async def context_latest():
        """Возвращает ссылки на последние anti-413 артефакты."""
        checkpoint = _latest_path_by_glob("artifacts/context_checkpoints/checkpoint_*.md")
        pack_dir = _latest_path_by_glob("artifacts/context_transition/pack_*")
        transfer_prompt = (pack_dir / "TRANSFER_PROMPT_RU.md") if pack_dir else None
        files_to_attach = (pack_dir / "FILES_TO_ATTACH.txt") if pack_dir else None
        return {
            "ok": True,
            "latest_checkpoint_path": str(checkpoint) if checkpoint else None,
            "latest_pack_dir": str(pack_dir) if pack_dir else None,
            "latest_transfer_prompt_path": str(transfer_prompt)
            if transfer_prompt and transfer_prompt.exists()
            else None,
            "latest_files_to_attach_path": str(files_to_attach)
            if files_to_attach and files_to_attach.exists()
            else None,
        }

    return router
