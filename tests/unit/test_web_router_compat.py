"""
Проверки WebRouterCompat: модуль должен пробрасывать фактический runtime-маршрут
из OpenClawClient в last_route для web-панели.
"""

from __future__ import annotations

import pytest

from src.modules.web_router_compat import WebRouterCompat


class _FakeModelManager:
    """Минимальный стаб ModelManager для тестов WebRouterCompat."""

    def __init__(self) -> None:
        self._current_model = "nvidia/nemotron-3-nano"
        self._models_cache = {}
        self.cost_analytics = None

    def get_ram_usage(self):
        return {"available_gb": 8.0}


class _FakeOpenClawClient:
    """Стаб OpenClawClient с управляемыми chunk-ответом и route meta."""

    def __init__(self) -> None:
        self.active_tier = "free"
        self._meta = {
            "channel": "local_direct",
            "provider": "nvidia",
            "model": "nvidia/nemotron-3-nano",
            "status": "ok",
            "error_code": None,
            "route_reason": "local_direct_primary",
            "route_detail": "Ответ получен напрямую из LM Studio",
            "active_tier": "free",
            "force_cloud": False,
            "timestamp": 1234567890,
        }

    async def send_message_stream(self, message: str, chat_id: str, force_cloud: bool = False):
        assert message
        assert chat_id == "web_assistant"
        yield "Локальный "
        yield "ответ"

    def get_last_runtime_route(self):
        return dict(self._meta)


@pytest.mark.asyncio
async def test_route_query_exposes_runtime_route_meta():
    """
    После route_query() router.get_last_route() должен содержать runtime-маршрут,
    а не пустой словарь.
    """
    router = WebRouterCompat(_FakeModelManager(), _FakeOpenClawClient())
    reply = await router.route_query("проверка")

    assert reply == "Локальный ответ"
    last_route = router.get_last_route()
    assert last_route["channel"] == "local_direct"
    assert last_route["provider"] == "nvidia"
    assert last_route["model"] == "nvidia/nemotron-3-nano"
    assert last_route["status"] == "ok"
    assert last_route["route_reason"] == "local_direct_primary"
