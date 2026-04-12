# -*- coding: utf-8 -*-
"""
Тесты helper-утилиты управления LM Studio.

Проверяем только чистую логику:
- нормализацию URL;
- применение дефолтов к settings.json;
- сбор payload для unload всех моделей.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch


def _load_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "scripts" / "lmstudio_control.py"
    spec = importlib.util.spec_from_file_location("lmstudio_control", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_normalize_base_url_strips_api_suffixes() -> None:
    module = _load_module()
    assert module._normalize_base_url("http://127.0.0.1:1234/v1") == "http://127.0.0.1:1234"
    assert module._normalize_base_url("http://127.0.0.1:1234/api/v1") == "http://127.0.0.1:1234"
    assert module._normalize_base_url("http://127.0.0.1:1234/") == "http://127.0.0.1:1234"


def test_apply_defaults_updates_context_and_ttl() -> None:
    module = _load_module()
    settings = {
        "developer": {"jitModelTTL": {"enabled": False, "ttlSeconds": 0}},
        "ui": {"configureLoadParamsBeforeLoad": False},
        "defaultContextLength": {"type": "max", "value": 8192},
    }

    updated = module.apply_defaults(settings, context_length=32184, ttl_seconds=3600)

    assert updated["defaultContextLength"] == {"type": "max", "value": 32184}
    assert updated["developer"]["jitModelTTL"]["enabled"] is True
    assert updated["developer"]["jitModelTTL"]["ttlSeconds"] == 3600
    assert updated["ui"]["configureLoadParamsBeforeLoad"] is True


def test_build_unload_attempts_prefers_all_then_instances_and_model() -> None:
    module = _load_module()
    models = [
        {
            "id": "nvidia/nemotron-3-nano",
            "loaded_instances": [{"instance_id": "inst-1"}, {"instanceReference": "inst-2"}],
        }
    ]

    attempts = module._build_unload_attempts(models)

    assert attempts[0] == ("all", {"all": True})
    assert ("instance", {"instance_id": "inst-1"}) in attempts
    assert ("instance", {"instance_id": "inst-2"}) in attempts
    assert ("model", {"model": "nvidia/nemotron-3-nano"}) in attempts


def test_load_model_uses_post_for_all_endpoints() -> None:
    module = _load_module()
    seen_calls = []

    module._fetch_models = lambda _base_url: module.HttpResult(
        ok=False, status=0, payload=None, error="offline"
    )

    def fake_http_json(method, url, body=None, timeout=10.0):
        seen_calls.append((method, url, body, timeout))
        return module.HttpResult(ok=True, status=200, payload={"model": body["model"]})

    module._http_json = fake_http_json

    attempts = module.load_model("http://127.0.0.1:1234", "nvidia/nemotron-3-nano", 3600)

    assert len(attempts) == 1
    assert seen_calls == [
        (
            "POST",
            "http://127.0.0.1:1234/api/v1/models/load",
            {"model": "nvidia/nemotron-3-nano"},
            600.0,
        )
    ]


def test_load_model_skips_duplicate_when_model_already_loaded() -> None:
    module = _load_module()
    seen_calls = []

    module._fetch_models = lambda _base_url: module.HttpResult(
        ok=True,
        status=200,
        payload={
            "models": [
                {
                    "key": "nvidia/nemotron-3-nano",
                    "selected_variant": "nvidia/nemotron-3-nano@4bit",
                    "loaded_instances": [{"id": "nvidia/nemotron-3-nano"}],
                }
            ]
        },
    )

    def fake_http_json(method, url, body=None, timeout=10.0):
        seen_calls.append((method, url, body, timeout))
        return module.HttpResult(ok=True, status=200, payload={"model": body["model"]})

    module._http_json = fake_http_json

    attempts = module.load_model("http://127.0.0.1:1234", "nvidia/nemotron-3-nano", 3600)

    assert len(attempts) == 1
    assert attempts[0][0] == "already_loaded"
    assert attempts[0][2].payload["already_loaded"] is True
    assert attempts[0][2].payload["instances"] == 1
    assert seen_calls == []


def test_http_json_adds_auth_headers_from_helper() -> None:
    module = _load_module()

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok": true}'

    seen_headers = {}

    def fake_urlopen(req, timeout=10.0):  # noqa: ARG001
        seen_headers.update(dict(req.headers))
        return _FakeResponse()

    with patch.object(
        module, "build_lm_studio_auth_headers", return_value={"Authorization": "Bearer lm-token"}
    ):
        with patch.object(module.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = module._http_json("GET", "http://127.0.0.1:1234/v1/models")

    assert result.ok is True
    assert seen_headers["Authorization"] == "Bearer lm-token"
