# -*- coding: utf-8 -*-
"""
Тесты для обновлённых API endpoints команд:
  GET  /api/commands        — полный реестр (новый формат с метаданными)
  GET  /api/commands/{name} — детальная информация о команде
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.modules.web_app import WebApp


# ---------------------------------------------------------------------------
# Заглушки (минимальный набор для запуска WebApp)
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {"channel": "cloud", "provider": "google", "model": "test", "status": "ok", "error_code": None}

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free", "last_error_code": None}

    async def health_check(self) -> bool:
        return True


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake"}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}


class _DummyRouter:
    pass


class _FakeKraab:
    pass


def _client() -> TestClient:
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(),
        "krab_ear_client": _FakeHealthClient(),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
        "kraab_userbot": _FakeKraab(),
    }
    app = WebApp(deps, port=18092, host="127.0.0.1")
    return TestClient(app.app)


# ---------------------------------------------------------------------------
# GET /api/commands — новый формат реестра
# ---------------------------------------------------------------------------


class TestApiCommandsList:
    def test_status_200(self) -> None:
        resp = _client().get("/api/commands")
        assert resp.status_code == 200

    def test_ok_field_true(self) -> None:
        data = _client().get("/api/commands").json()
        assert data["ok"] is True

    def test_has_total_field(self) -> None:
        data = _client().get("/api/commands").json()
        assert "total" in data
        assert isinstance(data["total"], int)

    def test_has_commands_list(self) -> None:
        data = _client().get("/api/commands").json()
        assert "commands" in data
        assert isinstance(data["commands"], list)

    def test_has_categories_list(self) -> None:
        data = _client().get("/api/commands").json()
        assert "categories" in data
        assert isinstance(data["categories"], list)

    def test_total_matches_commands_len(self) -> None:
        data = _client().get("/api/commands").json()
        assert data["total"] == len(data["commands"])

    def test_at_least_50_commands(self) -> None:
        data = _client().get("/api/commands").json()
        assert data["total"] >= 50

    def test_command_has_name_field(self) -> None:
        commands = _client().get("/api/commands").json()["commands"]
        for cmd in commands:
            assert "name" in cmd, f"нет поля name: {cmd}"

    def test_command_has_category_field(self) -> None:
        commands = _client().get("/api/commands").json()["commands"]
        for cmd in commands:
            assert "category" in cmd, f"нет поля category: {cmd}"

    def test_command_has_description_field(self) -> None:
        commands = _client().get("/api/commands").json()["commands"]
        for cmd in commands:
            assert "description" in cmd, f"нет поля description: {cmd}"

    def test_command_has_owner_only_field(self) -> None:
        commands = _client().get("/api/commands").json()["commands"]
        for cmd in commands:
            assert "owner_only" in cmd, f"нет поля owner_only: {cmd}"

    def test_command_has_aliases_field(self) -> None:
        commands = _client().get("/api/commands").json()["commands"]
        for cmd in commands:
            assert "aliases" in cmd, f"нет поля aliases: {cmd}"

    def test_command_has_usage_field(self) -> None:
        commands = _client().get("/api/commands").json()["commands"]
        for cmd in commands:
            assert "usage" in cmd, f"нет поля usage: {cmd}"

    def test_owner_only_is_bool(self) -> None:
        commands = _client().get("/api/commands").json()["commands"]
        for cmd in commands:
            assert isinstance(cmd["owner_only"], bool), (
                f"owner_only должен быть bool: {cmd}"
            )

    def test_aliases_is_list(self) -> None:
        commands = _client().get("/api/commands").json()["commands"]
        for cmd in commands:
            assert isinstance(cmd["aliases"], list), (
                f"aliases должен быть list: {cmd}"
            )

    def test_help_command_present(self) -> None:
        commands = _client().get("/api/commands").json()["commands"]
        names = {c["name"] for c in commands}
        assert "help" in names

    def test_swarm_command_present(self) -> None:
        commands = _client().get("/api/commands").json()["commands"]
        names = {c["name"] for c in commands}
        assert "swarm" in names

    def test_translator_command_present(self) -> None:
        commands = _client().get("/api/commands").json()["commands"]
        names = {c["name"] for c in commands}
        assert "translator" in names

    def test_categories_includes_basic(self) -> None:
        data = _client().get("/api/commands").json()
        assert "basic" in data["categories"]

    def test_categories_includes_swarm(self) -> None:
        data = _client().get("/api/commands").json()
        assert "swarm" in data["categories"]

    def test_categories_includes_costs(self) -> None:
        data = _client().get("/api/commands").json()
        assert "costs" in data["categories"]

    def test_categories_includes_dev(self) -> None:
        data = _client().get("/api/commands").json()
        assert "dev" in data["categories"]

    def test_help_owner_only_false(self) -> None:
        commands = _client().get("/api/commands").json()["commands"]
        help_cmd = next((c for c in commands if c["name"] == "help"), None)
        assert help_cmd is not None
        assert help_cmd["owner_only"] is False

    def test_model_owner_only_true(self) -> None:
        commands = _client().get("/api/commands").json()["commands"]
        model_cmd = next((c for c in commands if c["name"] == "model"), None)
        assert model_cmd is not None
        assert model_cmd["owner_only"] is True

    def test_bookmark_has_bm_alias(self) -> None:
        commands = _client().get("/api/commands").json()["commands"]
        bookmark = next((c for c in commands if c["name"] == "bookmark"), None)
        assert bookmark is not None
        assert "bm" in bookmark["aliases"]


# ---------------------------------------------------------------------------
# GET /api/commands/{name}
# ---------------------------------------------------------------------------


class TestApiCommandsGetByName:
    def test_known_command_returns_200(self) -> None:
        resp = _client().get("/api/commands/help")
        assert resp.status_code == 200

    def test_known_command_ok_true(self) -> None:
        data = _client().get("/api/commands/help").json()
        assert data["ok"] is True

    def test_known_command_has_command_key(self) -> None:
        data = _client().get("/api/commands/help").json()
        assert "command" in data

    def test_known_command_name_matches(self) -> None:
        data = _client().get("/api/commands/help").json()
        assert data["command"]["name"] == "help"

    def test_known_command_full_schema(self) -> None:
        data = _client().get("/api/commands/swarm").json()
        cmd = data["command"]
        for field in ("name", "category", "description", "owner_only", "aliases", "usage"):
            assert field in cmd, f"отсутствует поле {field}"

    def test_alias_lookup_bm_resolves_to_bookmark(self) -> None:
        data = _client().get("/api/commands/bm").json()
        assert data["ok"] is True
        assert data["command"]["name"] == "bookmark"

    def test_alias_lookup_h_resolves_to_help(self) -> None:
        data = _client().get("/api/commands/h").json()
        assert data["ok"] is True
        assert data["command"]["name"] == "help"

    def test_unknown_command_returns_404(self) -> None:
        resp = _client().get("/api/commands/nonexistent_xyz_command")
        assert resp.status_code == 404

    def test_unknown_command_has_detail(self) -> None:
        data = _client().get("/api/commands/nonexistent_xyz_command").json()
        assert "detail" in data

    def test_swarm_is_owner_only(self) -> None:
        data = _client().get("/api/commands/swarm").json()
        assert data["command"]["owner_only"] is True

    def test_search_is_not_owner_only(self) -> None:
        data = _client().get("/api/commands/search").json()
        assert data["command"]["owner_only"] is False

    def test_model_category_is_models(self) -> None:
        data = _client().get("/api/commands/model").json()
        assert data["command"]["category"] == "models"

    def test_remind_category_is_scheduler(self) -> None:
        data = _client().get("/api/commands/remind").json()
        assert data["command"]["category"] == "scheduler"

    def test_voice_category_is_modes(self) -> None:
        data = _client().get("/api/commands/voice").json()
        assert data["command"]["category"] == "modes"

    def test_sysinfo_category_is_system(self) -> None:
        data = _client().get("/api/commands/sysinfo").json()
        assert data["command"]["category"] == "system"

    def test_agent_category_is_dev(self) -> None:
        data = _client().get("/api/commands/agent").json()
        assert data["command"]["category"] == "dev"
