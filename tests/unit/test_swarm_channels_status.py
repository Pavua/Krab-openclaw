# -*- coding: utf-8 -*-
"""
tests/unit/test_swarm_channels_status.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Тесты для SwarmChannels.get_channels_status() и эндпоинта
GET /api/swarm/channels/status.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Юнит-тесты SwarmChannels.get_channels_status()
# ---------------------------------------------------------------------------


class TestGetChannelsStatus:
    """Тесты метода get_channels_status без сети/Telegram."""

    def _make_channels(self, forum_chat_id=None, team_topics=None):
        """Создаёт SwarmChannels с заданным состоянием без _load()."""
        from src.core.swarm_channels import SwarmChannels

        sc = SwarmChannels.__new__(SwarmChannels)
        sc._forum_chat_id = forum_chat_id
        sc._team_topics = team_topics or {}
        sc._team_chats = {}
        sc._client = None
        sc._team_clients = {}
        sc._owner_id = 0
        sc._interventions = {}
        sc._active_rounds = {}
        return sc

    def test_returns_expected_keys(self):
        """get_channels_status возвращает все обязательные ключи."""
        sc = self._make_channels(forum_chat_id=-1003703978531, team_topics={"traders": 42})
        result = sc.get_channels_status()

        assert "forum_chat_id" in result
        assert "forum_title" in result
        assert "is_forum_mode" in result
        assert "topics" in result
        assert "missing_topics" in result
        assert "generated_at" in result

    def test_topics_shape(self):
        """Каждый элемент topics содержит key, topic_id, last_post_at."""
        sc = self._make_channels(
            forum_chat_id=-1003703978531, team_topics={"traders": 42, "coders": 43}
        )
        result = sc.get_channels_status()

        assert len(result["topics"]) == 5  # по _FORUM_TOPICS
        for t in result["topics"]:
            assert "key" in t
            assert "topic_id" in t
            assert "last_post_at" in t

    def test_topic_ids_populated(self):
        """Известные топики имеют правильные topic_id."""
        sc = self._make_channels(
            forum_chat_id=-1003703978531, team_topics={"traders": 42, "coders": 43}
        )
        result = sc.get_channels_status()

        by_key = {t["key"]: t for t in result["topics"]}
        assert by_key["traders"]["topic_id"] == 42
        assert by_key["coders"]["topic_id"] == 43
        assert by_key["analysts"]["topic_id"] is None  # не настроен

    def test_no_topics_configured(self):
        """Если топики не настроены — все topic_id равны None, missing содержит все команды."""
        sc = self._make_channels()
        result = sc.get_channels_status()

        assert result["forum_chat_id"] is None
        assert result["is_forum_mode"] is False
        for t in result["topics"]:
            assert t["topic_id"] is None
        # все 5 ожидаемых ключей в missing
        expected_keys = {"traders", "coders", "analysts", "creative", "crossteam"}
        assert expected_keys == set(result["missing_topics"])

    def test_missing_topics_detection(self):
        """missing_topics содержит только не настроенные команды."""
        sc = self._make_channels(
            forum_chat_id=-1003703978531,
            team_topics={"traders": 1, "coders": 2, "analysts": 3, "creative": 4},
        )
        result = sc.get_channels_status()
        # crossteam не настроен
        assert result["missing_topics"] == ["crossteam"]

    def test_generated_at_iso_format(self):
        """generated_at — строка в формате ISO 8601 с суффиксом Z."""
        sc = self._make_channels()
        result = sc.get_channels_status()
        assert result["generated_at"].endswith("Z")
        # Должна быть допустимой ISO-датой
        from datetime import datetime

        dt = datetime.fromisoformat(result["generated_at"].rstrip("Z"))
        assert dt.year >= 2024

    def test_forum_chat_id_as_string(self):
        """forum_chat_id возвращается как строка (совместимость с JSON)."""
        sc = self._make_channels(forum_chat_id=-1003703978531, team_topics={"traders": 42})
        result = sc.get_channels_status()
        assert result["forum_chat_id"] == "-1003703978531"
        assert isinstance(result["forum_chat_id"], str)


# ---------------------------------------------------------------------------
# Интеграционные тесты эндпоинта /api/swarm/channels/status
# ---------------------------------------------------------------------------


class TestSwarmChannelsStatusEndpoint:
    """Тесты HTTP эндпоинта через FastAPI TestClient."""

    @pytest.fixture()
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.get("/api/swarm/channels/status")
        async def get_swarm_channels_status():
            from fastapi.responses import JSONResponse

            try:
                from src.core.swarm_channels import swarm_channels as _sc

                if not hasattr(_sc, "get_channels_status"):
                    return JSONResponse(
                        status_code=503,
                        content={
                            "ok": False,
                            "error": "SwarmChannels.get_channels_status unavailable",
                        },
                    )
                data = _sc.get_channels_status()
                return {"ok": True, **data}
            except Exception as exc:
                return JSONResponse(
                    status_code=503,
                    content={"ok": False, "error": str(exc)},
                )

        return TestClient(app)

    def test_endpoint_returns_200(self, client):
        """Эндпоинт возвращает HTTP 200."""
        response = client.get("/api/swarm/channels/status")
        assert response.status_code == 200

    def test_endpoint_ok_true(self, client):
        """Ответ содержит ok=True."""
        response = client.get("/api/swarm/channels/status")
        data = response.json()
        assert data.get("ok") is True

    def test_endpoint_has_topics(self, client):
        """Ответ содержит поле topics (список)."""
        response = client.get("/api/swarm/channels/status")
        data = response.json()
        assert "topics" in data
        assert isinstance(data["topics"], list)

    def test_endpoint_has_missing_topics(self, client):
        """Ответ содержит поле missing_topics."""
        response = client.get("/api/swarm/channels/status")
        data = response.json()
        assert "missing_topics" in data

    def test_endpoint_503_if_method_missing(self, monkeypatch):
        """503 возвращается если get_channels_status отсутствует."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import src.core.swarm_channels as sc_mod

        # Скрываем метод через монкейпатч на синглтоне
        original = sc_mod.swarm_channels.get_channels_status
        del sc_mod.swarm_channels.__class__.get_channels_status

        app = FastAPI()

        @app.get("/api/swarm/channels/status")
        async def ep():
            from fastapi.responses import JSONResponse

            try:
                from src.core.swarm_channels import swarm_channels as _sc

                if not hasattr(_sc, "get_channels_status"):
                    return JSONResponse(
                        status_code=503,
                        content={"ok": False, "error": "unavailable"},
                    )
                data = _sc.get_channels_status()
                return {"ok": True, **data}
            except Exception as exc:
                return JSONResponse(status_code=503, content={"ok": False, "error": str(exc)})

        c = TestClient(app)
        try:
            resp = c.get("/api/swarm/channels/status")
            assert resp.status_code == 503
            assert resp.json()["ok"] is False
        finally:
            # Восстанавливаем метод
            sc_mod.swarm_channels.__class__.get_channels_status = original
