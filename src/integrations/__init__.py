# -*- coding: utf-8 -*-
"""
Интеграционные клиенты Krab-экосистемы.

Зачем нужен модуль:
- держать thin-clients к соседним сервисам (Krab Voice Gateway, Krab Ear);
- давать единый интерфейс `health_check()` для web-панели и health-агрегатора;
- не смешивать orchestration/runtime-логику с HTTP-деталями внешних сервисов.
"""

from .krab_ear_client import KrabEarClient
from .macos_automation import MacOSAutomationError, MacOSAutomationService, macos_automation
from .voice_gateway_client import VoiceGatewayClient

__all__ = [
    "KrabEarClient",
    "MacOSAutomationError",
    "MacOSAutomationService",
    "VoiceGatewayClient",
    "macos_automation",
]
