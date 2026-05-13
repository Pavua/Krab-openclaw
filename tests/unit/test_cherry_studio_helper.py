# -*- coding: utf-8 -*-
"""
Тесты для `scripts/krab_cherry_studio_helper.py` (Wave 242).

Покрытие:
- discover_all_backends → mock httpx, 200/offline/non-200;
- format_models_table → корректно показывает модели и offline;
- build_cherry_config → 4 провайдера, нужные ключи;
- save_config → создаёт parent dir + валидный JSON;
- main CLI → --list-all-models печатает таблицу;
- main CLI → --export-cherry-config + --save-to сохраняет в tmp_path.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "krab_cherry_studio_helper.py"


@pytest.fixture(scope="module")
def helper():
    """Загружаем helper как модуль."""
    spec = importlib.util.spec_from_file_location("krab_cherry_studio_helper", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["krab_cherry_studio_helper"] = module
    spec.loader.exec_module(module)
    return module


def _mock_response(status: int, payload):
    """Создать мок httpx.Response."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    return resp


def test_discover_all_backends_mixed(helper):
    """LM Studio → 200, MLX → connection refused, OpenClaw → 200."""

    def fake_get(url, headers=None, timeout=None):
        if "1234" in url:
            return _mock_response(200, {"data": [{"id": "gemma-4-e4b"}, {"id": "qwen3"}]})
        if "8088" in url:
            raise ConnectionError("refused")
        if "18789" in url:
            return _mock_response(200, {"data": [{"id": "openclaw/main"}]})
        return _mock_response(404, {})

    with patch.object(helper.httpx, "get", side_effect=fake_get):
        snap = helper.discover_all_backends(lm_studio_token="tok", openclaw_token="oc")

    assert snap["lm_studio"]["error"] is None
    assert snap["lm_studio"]["models"] == ["gemma-4-e4b", "qwen3"]
    assert snap["mlx_direct"]["error"] is not None
    assert snap["mlx_direct"]["error"].startswith("offline:")
    assert snap["openclaw"]["models"] == ["openclaw/main"]


def test_format_models_table_includes_total_and_offline(helper):
    snap = {
        "lm_studio": {"url": "http://x:1234/v1", "models": ["a", "b"], "error": None},
        "mlx_direct": {"url": "http://x:8088/v1", "models": [], "error": "offline:ConnectionError"},
        "openclaw": {"url": "http://x:18789/v1", "models": ["openclaw/main"], "error": None},
    }
    out = helper.format_models_table(snap)
    assert "TOTAL: 3 models across 3 backends" in out
    assert "offline:ConnectionError" in out
    assert "- a" in out
    assert "- openclaw/main" in out


def test_build_cherry_config_has_four_providers(helper):
    snap = {
        "lm_studio": {"url": helper.LM_STUDIO_URL, "models": ["gemma-4-e4b"], "error": None},
        "mlx_direct": {"url": helper.MLX_DIRECT_URL, "models": ["mlx-gemma-26b"], "error": None},
        "openclaw": {"url": helper.OPENCLAW_URL, "models": ["openclaw/main"], "error": None},
    }
    cfg = helper.build_cherry_config(snap, openclaw_token="OCTOK", lm_studio_token="LMTOK")
    assert cfg["schema"] == "krab-cherry-studio-config/v1"
    names = [p["name"] for p in cfg["providers"]]
    assert names == [
        "Krab MCP Gateway",
        "MLX Direct",
        "LM Studio Sync",
        "OpenClaw Smart",
    ]
    # Проверка sync flag для LM Studio
    lm = next(p for p in cfg["providers"] if p["name"] == "LM Studio Sync")
    assert lm["sync_supported"] is True
    assert lm["api_key"] == "LMTOK"
    assert lm["models"] == ["gemma-4-e4b"]
    # Проверка OpenClaw extra_headers
    oc = next(p for p in cfg["providers"] if p["name"] == "OpenClaw Smart")
    assert oc["extra_headers"]["x-openclaw-scopes"] == "operator.write"


def test_save_config_creates_parents(helper, tmp_path):
    target = tmp_path / "nested" / "sub" / "cherry.json"
    cfg = {"schema": "test", "providers": []}
    helper.save_config(cfg, target)
    assert target.exists()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["schema"] == "test"


def test_main_list_all_models_prints_table(helper, capsys):
    fake_snap = {
        "lm_studio": {"url": helper.LM_STUDIO_URL, "models": ["m1"], "error": None},
        "mlx_direct": {"url": helper.MLX_DIRECT_URL, "models": [], "error": "offline:X"},
        "openclaw": {"url": helper.OPENCLAW_URL, "models": ["openclaw/main"], "error": None},
    }
    with patch.object(helper, "discover_all_backends", return_value=fake_snap):
        rc = helper.main(["--list-all-models"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "lm_studio" in captured.out
    assert "TOTAL: 2 models" in captured.out


def test_main_export_cherry_config_saves_file(helper, tmp_path, capsys):
    fake_snap = {
        "lm_studio": {"url": helper.LM_STUDIO_URL, "models": ["m1"], "error": None},
        "mlx_direct": {"url": helper.MLX_DIRECT_URL, "models": ["mlx1"], "error": None},
        "openclaw": {"url": helper.OPENCLAW_URL, "models": ["openclaw/main"], "error": None},
    }
    target = tmp_path / "cherry.json"
    with patch.object(helper, "discover_all_backends", return_value=fake_snap):
        rc = helper.main(["--export-cherry-config", "--save-to", str(target)])
    assert rc == 0
    assert target.exists()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert len(loaded["providers"]) == 4
    captured = capsys.readouterr()
    assert "Cherry Studio config saved" in captured.out
