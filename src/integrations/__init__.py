# -*- coding: utf-8 -*-
"""
Интеграционные клиенты Krab-экосистемы.

Зачем нужен модуль:
- держать thin-clients к соседним сервисам (Krab Voice Gateway, Krab Ear);
- давать единый интерфейс `health_check()` для web-панели и health-агрегатора;
- не смешивать orchestration/runtime-логику с HTTP-деталями внешних сервисов.
- browser_ai_provider: взаимодействие с Gemini/ChatGPT через CDP (платные подписки).
"""

