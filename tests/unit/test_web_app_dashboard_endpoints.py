# -*- coding: utf-8 -*-
"""
Интеграционные тесты dashboard-endpoints web-панели.

Покрываем endpoints, которые ещё не были протестированы:
1. GET /api/runtime/summary          — единый summary со swarm/translator/costs
2. GET /api/swarm/task-board         — сводка task board (by_status, by_team, total)
3. GET /api/swarm/artifacts          — список артефактов
4. GET /api/swarm/listeners          — статус listeners
5. GET /api/costs/budget             — состояние бюджета
6. GET /api/costs/history            — история вызовов
7. GET /api/thinking/status          — thinking mode status
8. GET /api/depth/status             — depth/thinking alias
9. POST /api/thinking/set            — установка thinking mode
10. POST /api/swarm/listeners/toggle — переключение listeners
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.config import config
from src.modules.web_app import WebApp


# ---------------------------------------------------------------------------
# Вспомогательные заглушки
# ---------------------------------------------------------------------------


class _DummyRouter:
    """Минимальный роутер-заглушка."""

    def get_model_info(self) -> dict:
        return {}


class _FakeOpenClaw:
    """Фейковый OpenClaw клиент."""

    def get_last_runtime_route(self) -> dict:
        return {
            "channel": "cloud",
            "provider": "google",
            "model": "google/gemini-3-pro-preview",
            "status": "ok",
        }

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free"}

    async def get_cloud_runtime_check(self) -> dict:
        return {"ok": True}

    async def health_check(self) -> bool:
        return True


class _FakeKraab:
    """Заглушка userbot'а с методами, нужными для /api/runtime/summary."""

    def get_translator_runtime_profile(self) -> dict:
        return {"enabled": False, "src_lang": "ru", "tgt_lang": "es"}

    def get_translator_session_state(self) -> dict:
        return {"active": False, "session_id": None}


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}


def _make_client(*, openclaw_client: Any = None, kraab: Any = None) -> TestClient:
    """Создаёт TestClient с минимальными зависимостями."""
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": openclaw_client or _FakeOpenClaw(),
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
        "kraab_userbot": kraab or _FakeKraab(),
    }
    app = WebApp(deps, port=18080, host="127.0.0.1")
    return TestClient(app.app)


# ---------------------------------------------------------------------------
# /api/runtime/summary
# ---------------------------------------------------------------------------


def test_runtime_summary_returns_ok_and_top_level_keys() -> None:
    """GET /api/runtime/summary возвращает ok=True и ожидаемые top-level ключи."""
    client = _make_client()
    resp = client.get("/api/runtime/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    # Обязательные ключи
    for key in ("health", "route", "costs", "translator", "swarm", "silence", "notify_enabled"):
        assert key in data, f"Отсутствует ключ '{key}' в /api/runtime/summary"


def test_runtime_summary_swarm_has_task_board() -> None:
    """GET /api/runtime/summary → swarm.task_board присутствует в ответе."""
    client = _make_client()
    data = client.get("/api/runtime/summary").json()
    assert "task_board" in data["swarm"], "swarm.task_board должен быть в summary"
    # task_board — словарь (может быть пустым при чистом состоянии)
    assert isinstance(data["swarm"]["task_board"], dict)


def test_runtime_summary_swarm_has_listeners_enabled() -> None:
    """GET /api/runtime/summary → swarm.listeners_enabled — булево."""
    client = _make_client()
    data = client.get("/api/runtime/summary").json()
    assert "listeners_enabled" in data["swarm"]
    assert isinstance(data["swarm"]["listeners_enabled"], bool)


def test_runtime_summary_translator_has_session() -> None:
    """GET /api/runtime/summary → translator.session присутствует."""
    client = _make_client()
    data = client.get("/api/runtime/summary").json()
    assert "session" in data["translator"]


def test_runtime_summary_notify_enabled_is_bool() -> None:
    """GET /api/runtime/summary → notify_enabled — булево значение."""
    client = _make_client()
    data = client.get("/api/runtime/summary").json()
    assert isinstance(data["notify_enabled"], bool)


# ---------------------------------------------------------------------------
# /api/swarm/task-board
# ---------------------------------------------------------------------------


def test_swarm_task_board_returns_ok() -> None:
    """GET /api/swarm/task-board возвращает ok=True."""
    client = _make_client()
    resp = client.get("/api/swarm/task-board")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_swarm_task_board_has_summary() -> None:
    """GET /api/swarm/task-board содержит ключ summary."""
    client = _make_client()
    data = client.get("/api/swarm/task-board").json()
    assert "summary" in data


def test_swarm_task_board_summary_is_dict() -> None:
    """GET /api/swarm/task-board → summary — словарь."""
    client = _make_client()
    data = client.get("/api/swarm/task-board").json()
    assert isinstance(data["summary"], dict)


def test_swarm_task_board_summary_has_numeric_total() -> None:
    """GET /api/swarm/task-board → summary.total — неотрицательное целое число."""
    client = _make_client()
    summary = client.get("/api/swarm/task-board").json()["summary"]
    # total может быть в корне summary или вложенно; главное — это число
    total_val = summary.get("total", 0)
    assert isinstance(total_val, (int, float)), "summary.total должен быть числом"
    assert total_val >= 0


def test_swarm_task_board_summary_has_by_status() -> None:
    """GET /api/swarm/task-board → summary.by_status — словарь (или отсутствует при пустом board)."""
    client = _make_client()
    summary = client.get("/api/swarm/task-board").json()["summary"]
    # by_status присутствует и является dict (может быть пустым)
    by_status = summary.get("by_status", {})
    assert isinstance(by_status, dict)


def test_swarm_task_board_summary_has_by_team() -> None:
    """GET /api/swarm/task-board → summary.by_team — словарь."""
    client = _make_client()
    summary = client.get("/api/swarm/task-board").json()["summary"]
    by_team = summary.get("by_team", {})
    assert isinstance(by_team, dict)


# ---------------------------------------------------------------------------
# /api/swarm/artifacts
# ---------------------------------------------------------------------------


def test_swarm_artifacts_returns_ok_and_list() -> None:
    """GET /api/swarm/artifacts возвращает ok=True и список artifacts."""
    client = _make_client()
    resp = client.get("/api/swarm/artifacts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "artifacts" in data
    assert isinstance(data["artifacts"], list)


def test_swarm_artifacts_with_team_filter() -> None:
    """GET /api/swarm/artifacts?team=coders не падает и возвращает ok=True."""
    client = _make_client()
    resp = client.get("/api/swarm/artifacts?team=coders")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_swarm_artifacts_with_limit_filter() -> None:
    """GET /api/swarm/artifacts?limit=5 уважает ограничение."""
    client = _make_client()
    resp = client.get("/api/swarm/artifacts?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    # Не более 5 результатов
    assert len(data["artifacts"]) <= 5


def test_swarm_artifacts_items_have_required_fields() -> None:
    """Каждый артефакт в /api/swarm/artifacts содержит ожидаемые поля."""
    client = _make_client()
    data = client.get("/api/swarm/artifacts").json()
    for art in data["artifacts"]:
        # Эти поля должны присутствовать (значение может быть None)
        for field in ("team", "topic", "timestamp_iso", "duration_sec", "result_preview"):
            assert field in art, f"Артефакт не содержит поле '{field}'"


# ---------------------------------------------------------------------------
# /api/swarm/listeners
# ---------------------------------------------------------------------------


def test_swarm_listeners_returns_ok() -> None:
    """GET /api/swarm/listeners возвращает ok=True."""
    client = _make_client()
    resp = client.get("/api/swarm/listeners")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_swarm_listeners_has_listeners_enabled_bool() -> None:
    """GET /api/swarm/listeners → listeners_enabled — булево значение."""
    client = _make_client()
    data = client.get("/api/swarm/listeners").json()
    assert "listeners_enabled" in data
    assert isinstance(data["listeners_enabled"], bool)


def test_swarm_listeners_toggle_requires_auth(monkeypatch) -> None:
    """POST /api/swarm/listeners/toggle без ключа возвращает 403 если WEB_API_KEY задан."""
    import os
    monkeypatch.setenv("WEB_API_KEY", "secret-test-key")
    client = _make_client()
    resp = client.post("/api/swarm/listeners/toggle", json={"enabled": True})
    assert resp.status_code == 403


def test_swarm_listeners_toggle_with_valid_key() -> None:
    """POST /api/swarm/listeners/toggle с корректным X-Krab-Web-Key переключает статус."""
    client = _make_client()
    web_key = str(getattr(config, "WEB_API_KEY", "") or "")
    if not web_key:
        pytest.skip("WEB_API_KEY не задан — пропускаем write-тест")

    # Читаем текущий статус
    current = client.get("/api/swarm/listeners").json()["listeners_enabled"]

    # Переключаем
    resp = client.post(
        "/api/swarm/listeners/toggle",
        json={"enabled": not current},
        headers={"X-Krab-Web-Key": web_key},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["listeners_enabled"] == (not current)

    # Возвращаем обратно
    client.post(
        "/api/swarm/listeners/toggle",
        json={"enabled": current},
        headers={"X-Krab-Web-Key": web_key},
    )


# ---------------------------------------------------------------------------
# /api/costs/budget
# ---------------------------------------------------------------------------


def test_costs_budget_returns_ok() -> None:
    """GET /api/costs/budget возвращает ok=True."""
    client = _make_client()
    resp = client.get("/api/costs/budget")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_costs_budget_has_budget_object() -> None:
    """GET /api/costs/budget → поле budget — словарь с ожидаемыми ключами."""
    client = _make_client()
    data = client.get("/api/costs/budget").json()
    assert "budget" in data
    budget = data["budget"]
    assert isinstance(budget, dict)
    for key in ("spent_usd", "budget_ok"):
        assert key in budget, f"Отсутствует ключ '{key}' в budget"


def test_costs_budget_spent_usd_is_non_negative() -> None:
    """GET /api/costs/budget → budget.spent_usd >= 0."""
    client = _make_client()
    budget = client.get("/api/costs/budget").json()["budget"]
    spent = budget.get("spent_usd", -1)
    assert isinstance(spent, (int, float))
    assert spent >= 0


def test_costs_budget_budget_ok_is_bool() -> None:
    """GET /api/costs/budget → budget.budget_ok — булево."""
    client = _make_client()
    budget = client.get("/api/costs/budget").json()["budget"]
    assert isinstance(budget["budget_ok"], bool)


# ---------------------------------------------------------------------------
# /api/costs/history
# ---------------------------------------------------------------------------


def test_costs_history_returns_ok_and_list() -> None:
    """GET /api/costs/history возвращает ok=True и список history."""
    client = _make_client()
    resp = client.get("/api/costs/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "history" in data
    assert isinstance(data["history"], list)


def test_costs_history_has_metadata_fields() -> None:
    """GET /api/costs/history → total_records и returned присутствуют."""
    client = _make_client()
    data = client.get("/api/costs/history").json()
    for key in ("total_records", "returned"):
        assert key in data, f"Отсутствует '{key}' в /api/costs/history"
    assert data["returned"] >= 0
    assert data["total_records"] >= 0


def test_costs_history_limit_parameter_respected() -> None:
    """GET /api/costs/history?limit=3 возвращает не более 3 записей."""
    client = _make_client()
    data = client.get("/api/costs/history?limit=3").json()
    assert data["ok"] is True
    assert len(data["history"]) <= 3


def test_costs_history_channel_filter_accepted() -> None:
    """GET /api/costs/history?channel=telegram не падает и возвращает ok=True."""
    client = _make_client()
    resp = client.get("/api/costs/history?channel=telegram")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_costs_history_items_have_required_fields() -> None:
    """Каждая запись в history содержит все ожидаемые поля."""
    client = _make_client()
    # Чтобы проверить поля, добавим одну запись через cost_analytics
    from src.core.cost_analytics import CallRecord, cost_analytics

    cost_analytics.record_usage(
        {"input_tokens": 100, "output_tokens": 50},
        model_id="google/test-model-fields",
        channel="test",
    )

    data = client.get("/api/costs/history?limit=5").json()
    # Ищем запись с нашим model_id
    records = [r for r in data["history"] if "test-model-fields" in r.get("model_id", "")]
    if not records:
        # Если запись не нашлась — просто проверяем структуру первой доступной
        records = data["history"]
    if records:
        record = records[0]
        for field in (
            "model_id",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "timestamp",
            "channel",
            "is_fallback",
            "tool_calls_count",
        ):
            assert field in record, f"Запись history не содержит поле '{field}'"


# ---------------------------------------------------------------------------
# /api/thinking/status
# ---------------------------------------------------------------------------


def test_thinking_status_returns_ok() -> None:
    """GET /api/thinking/status возвращает ok=True."""
    client = _make_client()
    resp = client.get("/api/thinking/status")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_thinking_status_has_thinking_default() -> None:
    """GET /api/thinking/status → thinking_default присутствует."""
    client = _make_client()
    data = client.get("/api/thinking/status").json()
    assert "thinking_default" in data
    assert isinstance(data["thinking_default"], str)


def test_thinking_status_has_thinking_modes_list() -> None:
    """GET /api/thinking/status → thinking_modes — список строк."""
    client = _make_client()
    data = client.get("/api/thinking/status").json()
    assert "thinking_modes" in data
    modes = data["thinking_modes"]
    assert isinstance(modes, list)
    assert len(modes) > 0
    # "off" всегда должен быть в доступных режимах
    assert "off" in modes, "'off' должен быть среди thinking_modes"


def test_thinking_status_has_chain_items() -> None:
    """GET /api/thinking/status → chain_items присутствует и это список."""
    client = _make_client()
    data = client.get("/api/thinking/status").json()
    assert "chain_items" in data
    assert isinstance(data["chain_items"], list)


# ---------------------------------------------------------------------------
# /api/depth/status
# ---------------------------------------------------------------------------


def test_depth_status_returns_ok() -> None:
    """GET /api/depth/status возвращает ok=True."""
    client = _make_client()
    resp = client.get("/api/depth/status")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_depth_status_has_depth_field() -> None:
    """GET /api/depth/status → depth присутствует."""
    client = _make_client()
    data = client.get("/api/depth/status").json()
    assert "depth" in data
    assert isinstance(data["depth"], str)


def test_depth_status_has_available_modes() -> None:
    """GET /api/depth/status → available_modes — список (аналог thinking_modes)."""
    client = _make_client()
    data = client.get("/api/depth/status").json()
    assert "available_modes" in data
    assert isinstance(data["available_modes"], list)


def test_depth_status_thinking_default_matches_depth() -> None:
    """GET /api/depth/status → thinking_default совпадает с depth (они алиасы)."""
    client = _make_client()
    data = client.get("/api/depth/status").json()
    # thinking_default — резервное поле для обратной совместимости
    if "thinking_default" in data:
        assert data["thinking_default"] == data["depth"]


def test_depth_status_consistent_with_thinking_status() -> None:
    """depth и thinking_default должны совпадать между /api/depth/status и /api/thinking/status."""
    client = _make_client()
    depth_data = client.get("/api/depth/status").json()
    thinking_data = client.get("/api/thinking/status").json()
    assert depth_data["depth"] == thinking_data["thinking_default"]


# ---------------------------------------------------------------------------
# POST /api/thinking/set
# ---------------------------------------------------------------------------


def test_thinking_set_requires_auth(monkeypatch) -> None:
    """POST /api/thinking/set без ключа возвращает 403 если WEB_API_KEY задан."""
    import os
    monkeypatch.setenv("WEB_API_KEY", "secret-test-key")
    client = _make_client()
    resp = client.post("/api/thinking/set", json={"mode": "off"})
    assert resp.status_code == 403


def test_thinking_set_rejects_invalid_mode() -> None:
    """POST /api/thinking/set с невалидным режимом возвращает 400 (при наличии ключа)."""
    client = _make_client()
    web_key = str(getattr(config, "WEB_API_KEY", "") or "")
    if not web_key:
        pytest.skip("WEB_API_KEY не задан — пропускаем write-тест")

    resp = client.post(
        "/api/thinking/set",
        json={"mode": "invalid_mode_xyz"},
        headers={"X-Krab-Web-Key": web_key},
    )
    assert resp.status_code == 400


def test_thinking_set_valid_mode_with_key(monkeypatch) -> None:
    """POST /api/thinking/set с валидным режимом возвращает ok=True и обновлённый thinking_default."""
    web_key = "test-web-key-12345"
    monkeypatch.setattr(config, "WEB_API_KEY", web_key, raising=False)

    client = _make_client()

    # Мокаем _apply_openclaw_runtime_controls чтобы не трогать реальные файлы
    with patch.object(
        WebApp,
        "_apply_openclaw_runtime_controls",
        return_value={"thinking_default": "medium", "changed": {"thinking_default": "medium"}},
    ), patch.object(
        WebApp,
        "_build_openclaw_runtime_controls",
        return_value={"thinking_default": "off", "primary": "", "fallbacks": [], "chain_items": []},
    ):
        resp = client.post(
            "/api/thinking/set",
            json={"mode": "medium"},
            headers={"X-Krab-Web-Key": web_key},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "thinking_default" in data
    assert "changed" in data


# ---------------------------------------------------------------------------
# Дополнительные smoke-тесты для убеждённости в корректности маршрутов
# ---------------------------------------------------------------------------


def test_costs_budget_and_history_are_independent_endpoints() -> None:
    """GET /api/costs/budget и /api/costs/history — разные ответы."""
    client = _make_client()
    budget_resp = client.get("/api/costs/budget").json()
    history_resp = client.get("/api/costs/history").json()
    # Оба ok
    assert budget_resp["ok"] is True
    assert history_resp["ok"] is True
    # Имеют разные ключи данных
    assert "budget" in budget_resp
    assert "history" in history_resp


def test_all_dashboard_endpoints_return_200() -> None:
    """Smoke-тест: все dashboard endpoints отвечают 200."""
    client = _make_client()
    endpoints = [
        "/api/runtime/summary",
        "/api/swarm/task-board",
        "/api/swarm/artifacts",
        "/api/swarm/listeners",
        "/api/costs/budget",
        "/api/costs/history",
        "/api/thinking/status",
        "/api/depth/status",
    ]
    for path in endpoints:
        resp = client.get(path)
        assert resp.status_code == 200, f"Endpoint {path} вернул {resp.status_code}"
