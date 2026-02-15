# -*- coding: utf-8 -*-
"""Тесты управления загрузкой моделей LM Studio."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.core.model_manager import ModelRouter


@pytest.mark.asyncio
async def test_load_local_model_precheck_rejects_unknown_alias() -> None:
    router = ModelRouter({"LM_STUDIO_URL": "http://localhost:1234/v1"})

    with patch.object(router, "list_local_models", new=AsyncMock(return_value=["qwen2.5-7b-instruct"])), patch(
        "aiohttp.ClientSession"
    ) as session_mock:
        ok = await router.load_local_model("chat")

    assert ok is False
    assert str(router.last_local_load_error).startswith("model_not_found_precheck:chat")
    session_mock.assert_not_called()


@pytest.mark.asyncio
async def test_load_local_model_cli_fallback_without_gpu_auto() -> None:
    router = ModelRouter({"LM_STUDIO_URL": "http://localhost:1234/v1"})

    class _ResponseCtx:
        def __init__(self):
            self.status = 500

        async def text(self):
            return '{"error":"model_not_found"}'

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _SessionCtx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return _ResponseCtx()

    cli_proc = AsyncMock()
    cli_proc.returncode = 0
    cli_proc.communicate = AsyncMock(return_value=(b"ok", b""))

    with patch.object(router, "list_local_models", new=AsyncMock(return_value=["qwen2.5-7b"])), patch(
        "aiohttp.ClientSession", return_value=_SessionCtx()
    ), patch("os.path.exists", return_value=True), patch(
        "asyncio.create_subprocess_exec", return_value=cli_proc
    ) as exec_mock:
        ok = await router.load_local_model("qwen2.5-7b")

    assert ok is True
    args = exec_mock.call_args.args
    assert "--gpu" not in args
    assert args[1:] == ("load", "qwen2.5-7b", "-y")


def test_build_lms_load_command_honors_valid_gpu_values() -> None:
    router = ModelRouter({"LM_STUDIO_GPU_OFFLOAD": "max"})
    cmd = router._build_lms_load_command("/tmp/lms", "model-a")
    assert cmd == ["/tmp/lms", "load", "model-a", "-y", "--gpu", "max"]

    router_numeric = ModelRouter({"LM_STUDIO_GPU_OFFLOAD": "0.5"})
    cmd_numeric = router_numeric._build_lms_load_command("/tmp/lms", "model-a")
    assert cmd_numeric == ["/tmp/lms", "load", "model-a", "-y", "--gpu", "0.5"]

    router_invalid = ModelRouter({"LM_STUDIO_GPU_OFFLOAD": "auto"})
    cmd_invalid = router_invalid._build_lms_load_command("/tmp/lms", "model-a")
    assert cmd_invalid == ["/tmp/lms", "load", "model-a", "-y"]
