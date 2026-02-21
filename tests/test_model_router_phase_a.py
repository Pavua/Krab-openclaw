import asyncio
import sys
import os
from typing import Dict, Any

# Добавляем корень проекта в путь
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.model_manager import ModelRouter

async def test_router_resilience():
    print("🚀 Начинаю верификацию Router Resilience (Phase A)...")
    
    config = {
        "LM_STUDIO_URL": "http://localhost:1234/v1",
        "OLLAMA_URL": "http://localhost:11434/api",
        "GEMINI_API_KEY": "dummy_key",
        "ROUTING_POLICY": "auto"
    }
    
    router = ModelRouter(config)
    
    # 1. Тест детектора ошибок
    print("\n--- [1] Тестирование _is_runtime_error_message ---")
    error_cases = [
        "❌ Something went wrong",
        "Error: Connection refused",
        "Billing error: out of credits",
        "{\"error\": \"not_found\"}",
        "Safety filter blocked this",
        "Empty response",
        "502 Bad Gateway"
    ]
    for case in error_cases:
        is_err = router._is_runtime_error_message(case)
        print(f"Input: '{case[:30]}...' -> Detected Error: {is_err}")
        assert is_err is True

    valid_cases = [
        "Привет, как дела?",
        "Answer: The sky is blue.",
        "Код успешно скомпилирован."
    ]
    for case in valid_cases:
        is_err = router._is_runtime_error_message(case)
        print(f"Input: '{case[:30]}...' -> Detected Error: {is_err}")
        assert is_err is False

    # 2. Мокаем вызовы для проверки фолбэков
    print("\n--- [2] Тестирование Fallback Logic (Local -> Cloud) ---")

    # [R12] Mock health check to prevent it from resetting is_local_available
    async def mock_health(): pass
    router.check_local_health = mock_health
    
    # Мокаем _call_local_llm чтобы он вернул ошибку
    router._call_local_llm = lambda *args, **kwargs: asyncio.Future()
    router._call_local_llm.__setattr__('_is_coroutine', True)
    async def mock_local_error(*args, **kwargs):
        return "❌ Local LLM Runtime Error: Connection refused"
    router._call_local_llm = mock_local_error
    
    # Мокаем _call_gemini чтобы он вернул успех
    async def mock_cloud_success(*args, **kwargs):
        return "Cloud fallback response"
    router._call_gemini = mock_cloud_success
    
    # Убеждаемся что локалка "доступна" для теста
    router.is_local_available = True
    router.active_local_model = "test-local-model"
    
    resp = await router.route_query("Test prompt", task_type="chat")
    print(f"Response with Local Error: {resp}")
    assert resp == "Cloud fallback response"
    
    # Проверяем телеметрию
    last_route = router.get_last_route()
    print(f"Last Route Telemetry: {last_route.get('route_reason')} | {last_route.get('route_detail')}")
    assert last_route.get('route_reason') == "local_fallback_cloud"

    # 3. Тест на Loop Protection (Cloud -> Local fallback)
    print("\n--- [3] Тестирование Cloud -> Local Fallback ---")
    
    # Режим: Cloud предпочитается (например, reasoning или critical)
    # Мокаем облако на ошибку
    async def mock_cloud_error(*args, **kwargs):
        return "⚠️ Cloud Quota Exceeded"
    router._call_gemini = mock_cloud_error
    
    # Мокаем локалку на успех
    async def mock_local_success(*args, **kwargs):
        return "Local recovery response"
    router._call_local_llm = mock_local_success
    
    # Сбросим состояние
    router.cloud_soft_cap_reached = False
    
    resp = await router.route_query("Critical task", task_type="reasoning")
    print(f"Response with Cloud Error: {resp}")
    assert resp == "Local recovery response"
    
    last_route = router.get_last_route()
    print(f"Last Route Telemetry: {last_route.get('route_reason')}")
    assert last_route.get('route_reason') == "cloud_fallback_local"

    print("\n✅ Все тесты Router Resilience пройдены!")

if __name__ == "__main__":
    asyncio.run(test_router_resilience())
