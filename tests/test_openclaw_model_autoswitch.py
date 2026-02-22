# -*- coding: utf-8 -*-
"""
Тесты для OpenClaw Model Autoswitch.
Мокируем сетевые вызовы и команды CLI.
"""

from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import MagicMock, patch

from scripts.openclaw_model_autoswitch import (
    _detect_lm_loaded,
    _extract_entries,
    _is_loaded,
    _tick,
)


def test_extract_entries() -> None:
    res = _extract_entries([{"id": "a"}, {"id": "b"}])
    assert len(res) == 2

    res2 = _extract_entries({"models": [{"id": "c"}]})
    assert len(res2) == 1
    assert res2[0]["id"] == "c"

    res3 = _extract_entries({"data": [{"id": "d"}]})
    assert len(res3) == 1
    assert res3[0]["id"] == "d"


def test_is_loaded() -> None:
    assert _is_loaded({"loaded": True}) is True
    assert _is_loaded({"loaded": False}) is False
    assert _is_loaded({"state": "ready"}) is True
    assert _is_loaded({"status": "unloaded"}) is False
    assert _is_loaded({"loaded_instances": [{}]}) is True
    assert _is_loaded({"loaded_instances": []}) is False


@patch("scripts.openclaw_model_autoswitch._fetch_json")
@patch("scripts.openclaw_model_autoswitch._run")
@patch("scripts.openclaw_model_autoswitch.Path.exists")
def test_detect_lm_loaded(mock_exists: MagicMock, mock_run: MagicMock, mock_fetch: MagicMock) -> None:
    # Имитация работы без CLI lms (Path.exists(...) == False)
    mock_exists.return_value = False

    # 1 пустой / не загружен
    mock_fetch.return_value = {"data": []}
    loaded, mid, src = _detect_lm_loaded()
    assert not loaded

    # 2 загружен (JSON HTTP)
    mock_fetch.return_value = {"data": [{"id": "model/123", "loaded": True}]}
    loaded, mid, src = _detect_lm_loaded()
    assert loaded
    assert mid == "model/123"


@patch("scripts.openclaw_model_autoswitch._run")
@patch("scripts.openclaw_model_autoswitch._detect_lm_loaded")
def test_tick(mock_detect: MagicMock, mock_run: MagicMock) -> None:
    args = argparse.Namespace(
        dry_run=True,
        local_default="lmstudio/local",
        cloud_default="google/gemini-2.5-flash",
        cloud_fallback="openai/gpt-4o-mini",
    )

    # 1) локальная загружена
    mock_detect.return_value = (True, "my-local", "http")
    mock_run.return_value = (0, '{"defaultModel": "openai/gpt-4o-mini", "fallbacks": []}', "")

    payload = _tick(args)
    assert payload["desired_default"] == "lmstudio/local"
    assert payload["desired_fallbacks"] == ["google/gemini-2.5-flash", "openai/gpt-4o-mini"]
    assert payload["changed_default"] is True
    assert payload["changed_fallbacks"] is True

    # 2) локальная НЕ загружена
    mock_detect.return_value = (False, "", "http")
    mock_run.return_value = (0, '{"defaultModel": "lmstudio/local", "fallbacks": []}', "")

    payload = _tick(args)
    assert payload["desired_default"] == "google/gemini-2.5-flash"
    assert payload["desired_fallbacks"] == ["openai/gpt-4o-mini", "lmstudio/local"]
    assert payload["changed_default"] is True
