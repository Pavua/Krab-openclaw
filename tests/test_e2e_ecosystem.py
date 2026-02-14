
# -*- coding: utf-8 -*-
"""
Cross-Project E2E Ecosystem Test.
Verifies integration between Krab (Bot), Voice Gateway, and Krab Ear (IPC mock).
"""

import pytest
import asyncio
import aiohttp
from src.core.voice_gateway_client import VoiceGatewayClient

# Мы предполагаем, что Voice Gateway запущен на 8090 для этого теста,
# либо мы мокаем его, если он недоступен (но для E2E лучше настоящий).

@pytest.mark.asyncio
async def test_voice_gateway_integration_e2e():
    """Проверяет сквозной сценарий общения с Voice Gateway."""
    client = VoiceGatewayClient(base_url="http://127.0.0.1:8090")
    
    # 1. Проверка доступности
    is_up = await client.health_check()
    if not is_up:
        pytest.skip("Voice Gateway is not running on 8090. Skipping E2E.")
    
    # 2. Создание сессии
    session = await client.start_session(source="e2e_test")
    assert session.get("ok") is True
    session_id = session.get("result", {}).get("id")
    assert session_id.startswith("vs_")
    
    # 3. Тюнинг параметров
    tuned = await client.tune_runtime(session_id, buffering_mode="low_latency", target_latency_ms=300)
    assert tuned.get("ok") is True
    assert tuned.get("result", {}).get("runtime", {}).get("buffering_mode") == "low_latency"
    
    # 4. Проверка диагностики (пустой)
    await client._request("PATCH", f"/v1/sessions/{session_id}", payload={"status": "running"})
    diag = await client.get_diagnostics(session_id)
    assert diag.get("ok") is True
    assert diag.get("result", {}).get("status") == "running"
    
    # 5. Остановка сессии
    stopped = await client.stop_session(session_id)
    assert stopped.get("ok") is True
    
    # 6. Проверка удаления
    missing = await client.get_session(session_id)
    assert missing.get("ok") is False
    assert missing.get("error") == "http_404"

@pytest.mark.asyncio
async def test_voice_gateway_ws_stream_e2e():
    """Проверяет получение событий через WebSocket."""
    client = VoiceGatewayClient(base_url="http://127.0.0.1:8090")

    # 1. Проверка доступности
    is_up = await client.health_check()
    if not is_up:
        pytest.skip("Voice Gateway is not running on 8090. Skipping WS E2E.")

    # 2. Создание сессии
    session = await client.start_session(source="ws_e2e_test")
    assert session.get("ok") is True
    session_id = session.get("result", {}).get("id")
    assert session_id, "session_id must be present for WS E2E"

    # 3. Подключение по WS
    # Мы используем aiohttp для WS
    ws_url = f"ws://127.0.0.1:8090/v1/sessions/{session_id}/stream"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url) as ws:
                # Ждем приветственное событие call.state
                msg = await ws.receive_json()
                assert msg["type"] == "call.state"
                assert msg["data"]["status"] == "created"
                
                # Пушим событие через HTTP
                await client._request("POST", f"/v1/sessions/{session_id}/events", payload={"type": "test_event", "data": {"val": 1}})
                
                # Ждем его в WS
                msg2 = await ws.receive_json()
                assert msg2["type"] == "test_event"
                assert msg2["data"]["val"] == 1
    except Exception as e:
        pytest.fail(f"WS connection failed: {e}")
    finally:
        await client.stop_session(session_id)

@pytest.mark.asyncio
async def test_krab_ear_ipc_stubs():
    """Проверяет, что Krab Ear IPC методы доступны в кодовой базе (mock-level check)."""
    # Этот тест проверяет наличие методов в BackendService (через статический анализ или импорт)
    from KrabEar.backend.service import BackendService
    from KrabEar.backend.state_store import StateStore
    
    store = StateStore(db_path=":memory:")
    service = BackendService(store=store)
    
    # Проверяем, что handle_request понимает start_call_assist
    resp = await service.handle_request({
        "method": "start_call_assist",
        "params": {"voice_gateway_url": "http://localhost:8090"}
    })
    # Ожидаем ошибку или успех, но НЕ "method_not_found"
    assert resp.get("error", {}).get("code") != -32601 # Method not found
