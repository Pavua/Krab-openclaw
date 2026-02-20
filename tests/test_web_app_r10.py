# -*- coding: utf-8 -*-
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from src.modules.web_app import WebApp
from src.core.model_manager import ModelRouter

@pytest.fixture
def mock_deps():
    router = MagicMock(spec=ModelRouter)
    router.get_model_info.return_value = {"local": {"active": "test-model"}}
    router.force_mode = "auto"
    router.is_local_available = True
    router.active_local_model = "test-model"
    router.local_engine = "lm-studio"
    router.lm_studio_url = "http://localhost:1234/v1"
    
    # Mocking async methods
    router._smart_load = AsyncMock(return_value=True)
    router.unload_local_model = AsyncMock(return_value=True)
    router._evict_idle_models = AsyncMock(return_value=4.2)
    
    return {
        "router": router,
        "black_box": MagicMock(),
    }

@pytest.fixture
def client(mock_deps):
    # Не патчим здесь WEB_API_KEY, чтобы проверить поведение по умолчанию
    app_inst = WebApp(deps=mock_deps)
    return TestClient(app_inst.app)

def test_model_local_status(client, mock_deps):
    """Проверка GET /api/model/local/status"""
    response = client.get("/api/model/local/status")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["status"]["engine"] == "lm-studio"
    assert data["status"]["active_model"] == "test-model"
    assert data["status"]["is_loaded"] is True

def test_model_local_load_default_unauthorized(client):
    """Проверка POST /api/model/local/load-default с неверным ключом"""
    # Если в окружении нет ключа, тест может вернуть 200. Принудительно ставим ключ.
    with patch.dict("os.environ", {"WEB_API_KEY": "mandatory-secret"}):
        response = client.post(
            "/api/model/local/load-default",
            headers={"X-Krab-Web-Key": "wrong-key"}
        )
    assert response.status_code == 403

def test_model_local_load_default_success(client, mock_deps):
    """Проверка POST /api/model/local/load-default с ключом"""
    mock_deps["router"].local_preferred_model = "pref-model"
    with patch.dict("os.environ", {"WEB_API_KEY": "test-secret-123"}):
        response = client.post(
            "/api/model/local/load-default",
            headers={"X-Krab-Web-Key": "test-secret-123"}
        )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["model"] == "pref-model"
    mock_deps["router"]._smart_load.assert_awaited_once()

def test_model_local_unload_success(client, mock_deps):
    """Проверка POST /api/model/local/unload"""
    with patch.dict("os.environ", {"WEB_API_KEY": "test-secret-123"}):
        response = client.post(
            "/api/model/local/unload",
            headers={"X-Krab-Web-Key": "test-secret-123"}
        )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    # В нашем моке active_model установлена, поэтому должен вызваться unload_local_model
    mock_deps["router"].unload_local_model.assert_awaited_once_with("test-model")

@pytest.mark.asyncio
async def test_model_router_fallback_logic():
    """Unit-тест логики fallback в ModelRouter (симуляция ошибки рантайма)"""
    config = {
        "LM_STUDIO_URL": "http://localhost:1234",
        "LOCAL_PREFERRED_MODEL": "local-model",
        "CLOUD_PRIORITY_LIST": "google/gemini-2.0-flash"
    }
    
    with patch("src.core.model_manager.OpenClawClient"), \
         patch("src.core.model_manager.OpenClawStreamClient"):
        router = ModelRouter(config)
        router.is_local_available = True
        router.active_local_model = "local-model"
        
        # Симулируем ошибку рантайма локальной модели
        # "400 Model not loaded"
        router._call_local_llm = AsyncMock(return_value="400 No models loaded")
        
        # Мокаем успешный ответ облака
        router._call_gemini = AsyncMock(return_value="Hello from Cloud")
        
        # Имитируем, что проверка здоровья прошла успешно и модель "как бы" на месте
        router.check_local_health = AsyncMock()
        
        # Запускаем роутинг.
        response = await router.route_query(
            "Test prompt", 
            task_type="chat"
        )
        
        assert response == "Hello from Cloud"
        # Проверяем причину роутинга в последнем маршруте
        last_route = router.get_last_route()
        assert last_route["route_reason"] == "local_failed_cloud_fallback"
        assert "no models loaded" in last_route["route_detail"].lower()
