# -*- coding: utf-8 -*-
"""Тесты поля stage в CommandInfo."""
from __future__ import annotations

import pytest

from src.core.command_registry import CommandInfo


def _make(**kwargs):
    defaults = dict(name="test", category="basic", description="desc", usage="!test")
    defaults.update(kwargs)
    return CommandInfo(**defaults)


class TestCommandInfoStageDefault:
    def test_default_stage_is_production(self):
        cmd = _make()
        assert cmd.stage == "production"

    def test_explicit_production(self):
        cmd = _make(stage="production")
        assert cmd.stage == "production"

    def test_stage_beta(self):
        cmd = _make(stage="beta")
        assert cmd.stage == "beta"

    def test_stage_experimental(self):
        cmd = _make(stage="experimental")
        assert cmd.stage == "experimental"


class TestCommandInfoStageInvalid:
    def test_invalid_stage_raises(self):
        with pytest.raises(ValueError, match="Invalid stage"):
            _make(stage="nightly")

    def test_empty_stage_raises(self):
        with pytest.raises(ValueError, match="Invalid stage"):
            _make(stage="")

    def test_random_string_raises(self):
        with pytest.raises(ValueError, match="Invalid stage"):
            _make(stage="alpha")


class TestCommandInfoApiResponse:
    def test_api_response_includes_stage_production(self):
        cmd = _make(stage="production")
        resp = cmd.api_response()
        assert resp["stage"] == "production"

    def test_api_response_includes_stage_experimental(self):
        cmd = _make(stage="experimental")
        resp = cmd.api_response()
        assert resp["stage"] == "experimental"

    def test_api_response_includes_stage_beta(self):
        cmd = _make(stage="beta")
        resp = cmd.api_response()
        assert resp["stage"] == "beta"

    def test_to_dict_includes_stage(self):
        cmd = _make(stage="beta")
        d = cmd.to_dict()
        assert "stage" in d
        assert d["stage"] == "beta"

    def test_api_response_has_all_fields(self):
        cmd = _make(stage="experimental")
        resp = cmd.api_response()
        for key in ("name", "category", "description", "usage", "owner_only", "aliases", "stage"):
            assert key in resp, f"Missing key: {key}"
