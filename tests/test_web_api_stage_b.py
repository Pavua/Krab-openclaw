# -*- coding: utf-8 -*-
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from src.modules.web_app import WebApp

@pytest.fixture
def mock_deps():
    router = MagicMock()
    router.local_engine = "lm-studio"
    router.active_local_model = "test-model"
    router.is_local_available = True
    
    # Mock async methods
    router.load_local_model = AsyncMock(return_value=True)
    router.unload_model_manual = AsyncMock(return_value=True)
    router.unload_models_manual = AsyncMock()
    
    health_service = MagicMock()
    health_service.collect = AsyncMock(return_value={
        "resources": {"cpu": 10, "ram": 80},
        "budget": {"spent": 5, "limit": 10}
    })
    
    return {
        "router": router,
        "health_service": health_service,
        "watchdog": MagicMock()
    }

@pytest.fixture
def client(mock_deps):
    app_inst = WebApp(deps=mock_deps)
    # Прямое мокирование метода получения ключа для надежности в тестах
    app_inst._web_api_key = lambda: "test-secret"
    return TestClient(app_inst.app)

def test_ops_diagnostics(client):
    """Проверка GET /api/ops/diagnostics"""
    response = client.get("/api/ops/diagnostics")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "resources" in data
    assert "local_ai" in data
    assert data["local_ai"]["model"] == "test-model"

def test_ops_models_load(client, mock_deps):
    """Проверка POST /api/ops/models action=load"""
    payload = {"action": "load", "model": "new-model"}
    response = client.post(
        "/api/ops/models",
        json=payload,
        headers={"X-Krab-Web-Key": "test-secret"}
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    mock_deps["router"].load_local_model.assert_awaited_once_with("new-model")

def test_ops_models_unload(client, mock_deps):
    """Проверка POST /api/ops/models action=unload"""
    payload = {"action": "unload", "model": "old-model"}
    response = client.post(
        "/api/ops/models",
        json=payload,
        headers={"X-Krab-Web-Key": "test-secret"}
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    mock_deps["router"].unload_model_manual.assert_awaited_once_with("old-model")

def test_ops_models_unload_all(client, mock_deps):
    """Проверка POST /api/ops/models action=unload_all"""
    payload = {"action": "unload_all"}
    response = client.post(
        "/api/ops/models",
        json=payload,
        headers={"X-Krab-Web-Key": "test-secret"}
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    mock_deps["router"].unload_models_manual.assert_awaited_once()

def test_ops_models_unauthorized(client):
    """Проверка POST /api/ops/models без ключа"""
    payload = {"action": "unload_all"}
    response = client.post("/api/ops/models", json=payload)
    assert response.status_code == 403

if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__]))
