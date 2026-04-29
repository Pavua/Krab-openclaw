# -*- coding: utf-8 -*-
"""
W24 regression tests: photo в DM до LLM — root cause = models.json без 'image' в input[].

Root cause (W24): OpenClaw gateway читает models.json input[] при маршрутизации multimodal.
Если 'image' не заявлен в input[] для модели — gateway стриппит image_url из payload,
Gemini получает только текст и отвечает «не вижу фото».

Fix: ensure_vision_input_in_models_json() на старте + _is_model_declared_vision_in_config()
     + warning лог + авто-починка при photo-route.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_models_json(providers_extra: dict | None = None) -> dict:
    """Базовый models.json с Gemini моделью без image в input."""
    base: dict = {
        "providers": {
            "google": {
                "apiKey": "test-key",
                "models": [
                    {
                        "id": "google/gemini-3-pro-preview",
                        "name": "Gemini 3 Pro Preview",
                        "input": ["text"],  # БЕЗ image — root cause бага
                    },
                    {
                        "id": "google/gemini-2.5-flash",
                        "name": "Gemini 2.5 Flash",
                        "input": ["text"],
                    },
                ],
            },
            "openai": {
                "apiKey": "test-openai-key",
                "models": [
                    {
                        "id": "gpt-4o",
                        "name": "GPT-4o",
                        "input": ["text"],
                    }
                ],
            },
            "lmstudio": {
                "models": [
                    {
                        "id": "llama-local",
                        "name": "Llama Local",
                        "input": ["text"],
                    }
                ]
            },
        }
    }
    if providers_extra:
        base["providers"].update(providers_extra)
    return base


def _make_client_with_models_json(tmp_path: Path, models_data: dict):
    """Создаёт OpenClawClient с временным models.json."""
    from src.openclaw_client import OpenClawClient

    models_file = tmp_path / "models.json"
    models_file.write_text(json.dumps(models_data, ensure_ascii=False, indent=2))

    client = OpenClawClient.__new__(OpenClawClient)
    client._models_path = models_file
    return client


# ---------------------------------------------------------------------------
# 1. ensure_vision_input_in_models_json patches text-only Gemini models
# ---------------------------------------------------------------------------


def test_ensure_vision_patches_gemini_models(tmp_path):
    """ensure_vision_input_in_models_json добавляет image в input для Gemini-моделей."""
    data = _make_models_json()
    client = _make_client_with_models_json(tmp_path, data)

    patched = client.ensure_vision_input_in_models_json()

    assert patched >= 2, f"Ожидали >=2 исправлений, получили {patched}"

    saved = json.loads(client._models_path.read_text())
    for m in saved["providers"]["google"]["models"]:
        assert "image" in m["input"], f"Модель {m['id']} не получила image в input"


def test_ensure_vision_skips_non_vision_models(tmp_path):
    """ensure_vision_input_in_models_json не трогает LLaMA/text-only модели."""
    data = _make_models_json()
    client = _make_client_with_models_json(tmp_path, data)

    client.ensure_vision_input_in_models_json()

    saved = json.loads(client._models_path.read_text())
    llama_models = [
        m
        for m in saved["providers"]["lmstudio"]["models"]
        if "llama" in m["id"].lower()
    ]
    for m in llama_models:
        assert "image" not in m.get("input", []), f"llama не должен получить image: {m}"


def test_ensure_vision_idempotent(tmp_path):
    """ensure_vision_input_in_models_json идемпотентна — двойной вызов не дублирует."""
    data = _make_models_json()
    client = _make_client_with_models_json(tmp_path, data)

    client.ensure_vision_input_in_models_json()
    patched2 = client.ensure_vision_input_in_models_json()

    assert patched2 == 0, "Второй вызов не должен давать исправлений"

    saved = json.loads(client._models_path.read_text())
    for m in saved["providers"]["google"]["models"]:
        assert m["input"].count("image") == 1, f"Дублирование image в {m['id']}"


# ---------------------------------------------------------------------------
# 2. _is_model_declared_vision_in_config — point lookup
# ---------------------------------------------------------------------------


def test_is_model_declared_vision_false_before_patch(tmp_path):
    """Модель без image возвращает False."""
    data = _make_models_json()
    client = _make_client_with_models_json(tmp_path, data)

    result = client._is_model_declared_vision_in_config("google/gemini-3-pro-preview")
    assert result is False


def test_is_model_declared_vision_true_after_patch(tmp_path):
    """После ensure_vision_input модель возвращает True."""
    data = _make_models_json()
    client = _make_client_with_models_json(tmp_path, data)

    client.ensure_vision_input_in_models_json()

    result = client._is_model_declared_vision_in_config("google/gemini-3-pro-preview")
    assert result is True


def test_is_model_declared_vision_unknown_model(tmp_path):
    """Неизвестная модель возвращает False (safe default)."""
    data = _make_models_json()
    client = _make_client_with_models_json(tmp_path, data)

    result = client._is_model_declared_vision_in_config("unknown/model-xyz")
    assert result is False


# ---------------------------------------------------------------------------
# 3. ensure_vision вызывается в __init__ через ensure_vision_input_in_models_json
# ---------------------------------------------------------------------------


def test_ensure_vision_called_on_init(tmp_path):
    """При инициализации OpenClawClient вызывается ensure_vision_input_in_models_json."""
    import importlib
    import sys

    # Патчим конфиг минимально
    config_mock = MagicMock()
    config_mock.OPENCLAW_URL = "http://localhost:18789"
    config_mock.OPENCLAW_TOKEN = "test"
    config_mock.LM_STUDIO_URL = ""
    config_mock.BASE_DIR = str(tmp_path)
    config_mock.GEMINI_API_KEY_FREE = ""
    config_mock.GEMINI_API_KEY_PAID = ""

    with (
        patch("src.openclaw_client.config", config_mock),
        patch(
            "src.openclaw_client.OpenClawClient.ensure_vision_input_in_models_json"
        ) as mock_ensure,
        patch(
            "src.openclaw_client.OpenClawClient._sync_token_from_runtime_on_init"
        ),
    ):
        mock_ensure.return_value = 0
        from src.openclaw_client import OpenClawClient

        client = OpenClawClient()
        mock_ensure.assert_called_once()


# ---------------------------------------------------------------------------
# 4. gpt-4o также патчится (OpenAI vision)
# ---------------------------------------------------------------------------


def test_ensure_vision_patches_gpt4o(tmp_path):
    """gpt-4o тоже должен получить image в input."""
    data = _make_models_json()
    client = _make_client_with_models_json(tmp_path, data)

    client.ensure_vision_input_in_models_json()

    saved = json.loads(client._models_path.read_text())
    gpt4o_models = [
        m for m in saved["providers"]["openai"]["models"] if "gpt-4o" in m["id"]
    ]
    assert gpt4o_models, "gpt-4o модель не найдена"
    for m in gpt4o_models:
        assert "image" in m["input"], f"gpt-4o должен иметь image в input: {m}"


# ---------------------------------------------------------------------------
# 5. models.json отсутствует — ensure_vision не падает
# ---------------------------------------------------------------------------


def test_ensure_vision_missing_file(tmp_path):
    """Если models.json нет — ensure_vision не бросает исключение."""
    from src.openclaw_client import OpenClawClient

    client = OpenClawClient.__new__(OpenClawClient)
    client._models_path = tmp_path / "nonexistent" / "models.json"

    result = client.ensure_vision_input_in_models_json()
    assert result == 0
