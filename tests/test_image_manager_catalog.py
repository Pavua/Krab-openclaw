# -*- coding: utf-8 -*-
"""Тесты каталога image-моделей и правки workflow для ComfyUI."""

from src.core.image_manager import ImageManager


async def _fake_online() -> bool:
    return True


async def _fake_offline() -> bool:
    return False


def test_estimate_cost_for_known_model() -> None:
    manager = ImageManager(config={})
    info = manager.estimate_cost("cloud:imagen3")
    assert info["ok"] is True
    assert info["unit_cost_usd"] is not None


def test_patch_workflow_prompt_updates_positive_nodes() -> None:
    manager = ImageManager(config={})
    workflow = {
        "1": {
            "class_type": "CLIPTextEncode",
            "title": "CLIP Text Encode (positive)",
            "inputs": {"text": "old prompt"},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "title": "CLIP Text Encode (negative)",
            "inputs": {"text": "bad anatomy"},
        },
    }

    patched = manager._patch_workflow_prompt(workflow, "new prompt")
    assert patched["1"]["inputs"]["text"] == "new prompt"
    assert patched["2"]["inputs"]["text"] == "bad anatomy"


import pytest


@pytest.mark.asyncio
async def test_list_models_reflects_backend_availability() -> None:
    manager = ImageManager(config={})
    manager._is_comfy_online = _fake_offline  # type: ignore[method-assign]

    rows = await manager.list_models()
    by_alias = {row["alias"]: row for row in rows}

    assert by_alias["local:flux-dev"]["available"] is False
    assert by_alias["cloud:imagen3"]["available"] is False


def test_set_default_alias_and_mode_runtime() -> None:
    manager = ImageManager(config={})

    local = manager.set_default_alias("local", "local:flux-uncensored")
    cloud = manager.set_default_alias("cloud", "cloud:imagen3")
    mode = manager.set_prefer_mode("cloud")

    assert local["ok"] is True
    assert cloud["ok"] is True
    assert mode["ok"] is True
    assert mode["prefer_local"] is False
    defaults = manager.get_defaults()
    assert defaults["default_local_alias"] == "local:flux-uncensored"
    assert defaults["default_cloud_alias"] == "cloud:imagen3"


def test_env_fallback_for_image_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_DEFAULT_LOCAL_MODEL", "local:flux-uncensored")
    monkeypatch.setenv("IMAGE_DEFAULT_CLOUD_MODEL", "cloud:imagen3")
    manager = ImageManager(config={})
    defaults = manager.get_defaults()
    assert defaults["default_local_alias"] == "local:flux-uncensored"
    assert defaults["default_cloud_alias"] == "cloud:imagen3"
