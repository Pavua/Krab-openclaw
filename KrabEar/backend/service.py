# -*- coding: utf-8 -*-
"""
IPC shim-сервис KrabEar для тестов интеграции экосистемы.

Поддерживаемые методы:
- start_call_assist
- stop_call_assist
- get_call_assist_state
- list_audio_inputs
"""

from __future__ import annotations

from typing import Any

from .state_store import StateStore


class BackendService:
    """Упрощенная реализация JSON-RPC-подобного обработчика."""

    def __init__(self, store: StateStore):
        self.store = store
        self.store.set("call_assist", {"active": False, "voice_gateway_url": "", "status": "idle"})

    async def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        method = str(request.get("method", "")).strip()
        params = request.get("params", {}) or {}

        if method == "start_call_assist":
            return await self._start_call_assist(params)
        if method == "stop_call_assist":
            return await self._stop_call_assist()
        if method == "get_call_assist_state":
            return await self._get_call_assist_state()
        if method == "list_audio_inputs":
            return await self._list_audio_inputs()

        return {"error": {"code": -32601, "message": "Method not found"}}

    async def _start_call_assist(self, params: dict[str, Any]) -> dict[str, Any]:
        state = self.store.get("call_assist", {})
        state["active"] = True
        state["status"] = "running"
        state["voice_gateway_url"] = str(params.get("voice_gateway_url", "")).strip()
        self.store.set("call_assist", state)
        return {"result": {"ok": True, "state": state}}

    async def _stop_call_assist(self) -> dict[str, Any]:
        state = self.store.get("call_assist", {})
        state["active"] = False
        state["status"] = "stopped"
        self.store.set("call_assist", state)
        return {"result": {"ok": True, "state": state}}

    async def _get_call_assist_state(self) -> dict[str, Any]:
        state = self.store.get("call_assist", {"active": False, "status": "idle"})
        return {"result": {"ok": True, "state": state}}

    async def _list_audio_inputs(self) -> dict[str, Any]:
        # Возвращаем стабильный shim-список для тестов.
        return {
            "result": {
                "ok": True,
                "devices": [
                    {"id": "default_mic", "name": "Default Microphone"},
                    {"id": "system_audio", "name": "System Audio"},
                ],
            }
        }

