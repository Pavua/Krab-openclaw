# -*- coding: utf-8 -*-
"""
Tests for `scripts/setup_lm_studio_token.py`.

Покрытие:
- token validation (whitespace, empty, length);
- probe success → .env updated;
- probe 401 → .env not modified;
- --check mode is read-only.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "setup_lm_studio_token.py"


@pytest.fixture(scope="module")
def setup_module():
    """Загружаем скрипт как модуль для тестов."""
    spec = importlib.util.spec_from_file_location("setup_lm_studio_token", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["setup_lm_studio_token"] = module
    spec.loader.exec_module(module)
    return module


def test_token_validation_empty(setup_module):
    ok, err = setup_module.validate_token("")
    assert not ok
    assert "пустой" in err


def test_token_validation_whitespace_inside(setup_module):
    ok, err = setup_module.validate_token("sk-lm-foo bar")
    assert not ok
    assert "whitespace" in err


def test_token_validation_leading_whitespace(setup_module):
    ok, err = setup_module.validate_token(" sk-lm-foo")
    assert not ok
    # leading whitespace is detected via strip mismatch
    assert "whitespace" in err


def test_token_validation_too_short(setup_module):
    ok, err = setup_module.validate_token("ab")
    assert not ok
    assert "коротк" in err


def test_token_validation_ok(setup_module):
    ok, err = setup_module.validate_token("sk-lm-AbCd:EfGh1234")
    assert ok
    assert err == ""


def test_read_existing_token_missing(setup_module, tmp_path):
    env = tmp_path / ".env"
    assert setup_module.read_existing_token(env) == ""


def test_read_existing_token_present(setup_module, tmp_path):
    env = tmp_path / ".env"
    env.write_text('FOO=bar\nLM_API_TOKEN="sk-test-1234"\nBAZ=qux\n', encoding="utf-8")
    assert setup_module.read_existing_token(env) == "sk-test-1234"


def test_read_env_url_default(setup_module, tmp_path):
    env = tmp_path / ".env"
    assert setup_module.read_env_url(env) == setup_module.DEFAULT_LM_URL


def test_read_env_url_custom(setup_module, tmp_path):
    env = tmp_path / ".env"
    env.write_text("LM_STUDIO_URL=http://1.2.3.4:9999\n", encoding="utf-8")
    assert setup_module.read_env_url(env) == "http://1.2.3.4:9999"


def test_upsert_env_token_creates_new(setup_module, tmp_path):
    env = tmp_path / ".env"
    replaced = setup_module.upsert_env_token("sk-test-new", env)
    assert replaced is False
    content = env.read_text(encoding="utf-8")
    assert 'LM_API_TOKEN="sk-test-new"' in content


def test_upsert_env_token_replaces_existing(setup_module, tmp_path):
    env = tmp_path / ".env"
    env.write_text('FOO=bar\nLM_API_TOKEN="old-token"\nBAZ=qux\n', encoding="utf-8")
    replaced = setup_module.upsert_env_token("sk-test-NEW", env)
    assert replaced is True
    content = env.read_text(encoding="utf-8")
    assert 'LM_API_TOKEN="sk-test-NEW"' in content
    assert "old-token" not in content
    assert "FOO=bar" in content
    assert "BAZ=qux" in content


class _FakeResponse:
    def __init__(self, status_code: int, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


def test_probe_success(setup_module):
    fake = _FakeResponse(200, {"data": [{"id": "model-1"}, {"id": "model-2"}]})
    with patch.object(setup_module.httpx, "get", return_value=fake):
        ok, msg = setup_module.probe_lm_studio("http://x:1234", "sk-test")
    assert ok is True
    assert "200" in msg
    assert "2 models" in msg


def test_probe_401(setup_module):
    fake = _FakeResponse(401, text="Unauthorized")
    with patch.object(setup_module.httpx, "get", return_value=fake):
        ok, msg = setup_module.probe_lm_studio("http://x:1234", "bad-token")
    assert ok is False
    assert "401" in msg


def test_probe_404(setup_module):
    fake = _FakeResponse(404, text="not found")
    with patch.object(setup_module.httpx, "get", return_value=fake):
        ok, msg = setup_module.probe_lm_studio("http://x:1234", "sk-test")
    assert ok is False
    assert "404" in msg


def test_probe_connect_error(setup_module):
    import httpx as _httpx

    def _raise(*_a, **_kw):
        raise _httpx.ConnectError("connection refused")

    with patch.object(setup_module.httpx, "get", side_effect=_raise):
        ok, msg = setup_module.probe_lm_studio("http://x:1234", "sk-test")
    assert ok is False
    assert "connection error" in msg


def test_cmd_setup_probe_success_writes_env(setup_module, tmp_path):
    env = tmp_path / ".env"
    env.write_text("FOO=bar\n", encoding="utf-8")
    fake = _FakeResponse(200, {"data": []})
    with patch.object(setup_module.httpx, "get", return_value=fake):
        rc = setup_module.cmd_setup("sk-test-OK1234", env_path=env)
    assert rc == 0
    assert 'LM_API_TOKEN="sk-test-OK1234"' in env.read_text(encoding="utf-8")


def test_cmd_setup_probe_401_does_not_modify_env(setup_module, tmp_path):
    env = tmp_path / ".env"
    original = "FOO=bar\nLM_API_TOKEN=old\n"
    env.write_text(original, encoding="utf-8")
    fake = _FakeResponse(401, text="Unauthorized")
    with patch.object(setup_module.httpx, "get", return_value=fake):
        rc = setup_module.cmd_setup("sk-test-FAIL", env_path=env)
    assert rc == 3
    # .env должен остаться нетронутым
    assert env.read_text(encoding="utf-8") == original


def test_cmd_setup_invalid_token_no_probe(setup_module, tmp_path):
    env = tmp_path / ".env"
    env.write_text("FOO=bar\n", encoding="utf-8")
    # должен fail на validation, не дойдя до probe
    with patch.object(setup_module.httpx, "get") as mock_get:
        rc = setup_module.cmd_setup("", env_path=env)
    assert rc == 2
    assert mock_get.call_count == 0
    assert env.read_text(encoding="utf-8") == "FOO=bar\n"


def test_cmd_check_no_token(setup_module, tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("FOO=bar\n", encoding="utf-8")
    rc = setup_module.cmd_check(env_path=env)
    assert rc == 1
    out = capsys.readouterr().out
    assert "не найден" in out


def test_cmd_check_does_not_modify_env(setup_module, tmp_path):
    env = tmp_path / ".env"
    original = 'FOO=bar\nLM_API_TOKEN="sk-test-good"\n'
    env.write_text(original, encoding="utf-8")
    fake = _FakeResponse(200, {"data": []})
    with patch.object(setup_module.httpx, "get", return_value=fake):
        rc = setup_module.cmd_check(env_path=env)
    assert rc == 0
    # read-only: содержимое не меняется
    assert env.read_text(encoding="utf-8") == original


def test_cmd_check_probe_fails(setup_module, tmp_path):
    env = tmp_path / ".env"
    env.write_text('LM_API_TOKEN="sk-test-bad"\n', encoding="utf-8")
    fake = _FakeResponse(401, text="Unauthorized")
    with patch.object(setup_module.httpx, "get", return_value=fake):
        rc = setup_module.cmd_check(env_path=env)
    assert rc == 1
