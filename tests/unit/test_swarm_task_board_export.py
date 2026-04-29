# -*- coding: utf-8 -*-
"""
tests/unit/test_swarm_task_board_export.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Tests для export endpoint swarm task board (CSV/JSON).
"""

import csv
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.core.swarm_task_board import SwarmTask
from src.modules.web_app import WebApp


class _DummyRouter:
    """Минимальный роутер-заглушка для инициализации WebApp."""

    def get_model_info(self):
        return {}


def _make_client() -> TestClient:
    """Создаёт TestClient с минимальным набором зависимостей."""
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": None,
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": None,
        "krab_ear_client": None,
        "perceptor": None,
        "watchdog": None,
        "queue": None,
    }
    app = WebApp(deps, port=18080, host="127.0.0.1")
    return TestClient(app.app)


@pytest.fixture
def sample_tasks():
    """Sample tasks для тестирования."""
    return [
        SwarmTask(
            task_id="coders_abc12345_1234567890",
            team="coders",
            title="Implement export endpoint",
            description="CSV and JSON export",
            status="in_progress",
            created_by="owner",
            assigned_to="coders",
            priority="high",
            created_at="2026-04-17T10:00:00+00:00",
            updated_at="2026-04-17T11:00:00+00:00",
            result="",
            artifacts=["artifact1.md"],
            parent_task_id="",
        ),
        SwarmTask(
            task_id="traders_def67890_1234567891",
            team="traders",
            title="Analyze market data",
            description="Weekly analysis",
            status="done",
            created_by="owner",
            assigned_to="traders",
            priority="medium",
            created_at="2026-04-16T10:00:00+00:00",
            updated_at="2026-04-17T09:00:00+00:00",
            result="Market trend is bullish",
            artifacts=[],
            parent_task_id="",
        ),
    ]


def test_export_json_format(sample_tasks):
    """Test JSON export format."""
    with patch("src.core.swarm_task_board.swarm_task_board") as mock_board:
        mock_board.list_tasks.return_value = sample_tasks

        client = _make_client()
        response = client.get("/api/swarm/task-board/export?format=json")

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert len(data["tasks"]) == 2
        assert data["tasks"][0]["task_id"] == "coders_abc12345_1234567890"
        assert data["tasks"][0]["team"] == "coders"
        assert data["tasks"][0]["status"] == "in_progress"


def test_export_csv_format(sample_tasks):
    """Test CSV export format with proper headers."""
    with patch("src.core.swarm_task_board.swarm_task_board") as mock_board:
        mock_board.list_tasks.return_value = sample_tasks

        client = _make_client()
        response = client.get("/api/swarm/task-board/export?format=csv")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/csv; charset=utf-8"
        assert "attachment" in response.headers["content-disposition"]

        # Parse CSV
        lines = response.text.strip().split("\n")
        reader = csv.reader(lines)
        headers = next(reader)

        assert headers == [
            "task_id",
            "team",
            "title",
            "status",
            "priority",
            "created_by",
            "assigned_to",
            "created_at",
            "updated_at",
        ]

        rows = list(reader)
        assert len(rows) == 2
        assert rows[0][0] == "coders_abc12345_1234567890"
        assert rows[0][1] == "coders"
        assert rows[1][1] == "traders"


def test_export_empty_board():
    """Test export with empty board."""
    with patch("src.core.swarm_task_board.swarm_task_board") as mock_board:
        mock_board.list_tasks.return_value = []

        client = _make_client()

        # JSON
        response = client.get("/api/swarm/task-board/export?format=json")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert len(data["tasks"]) == 0

        # CSV (headers only)
        response = client.get("/api/swarm/task-board/export?format=csv")
        assert response.status_code == 200
        lines = response.text.strip().split("\n")
        assert len(lines) == 1  # Only headers


def test_export_default_format_is_csv():
    """Test that default format is CSV when not specified."""
    with patch("src.core.swarm_task_board.swarm_task_board") as mock_board:
        mock_board.list_tasks.return_value = []

        client = _make_client()
        response = client.get("/api/swarm/task-board/export")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/csv; charset=utf-8"
