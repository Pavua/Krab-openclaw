# -*- coding: utf-8 -*-
"""
AI Guardian Client.

Интеграция с локальным сервисом AI Guardian для продвинутого анализа контента
(доксинг, спам, токсичность) и ответов на FAQ.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class AIGuardianClient:
    """Клиент для AI Guardian API."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Content-Type": "application/json",
        }

    async def _request_json(
        self,
        method: str,
        path: str,
        payload: Optional[dict[str, Any]] = None,
        timeout: int = 10,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method.upper(), url, json=payload, timeout=timeout, headers=self.headers
                ) as resp:
                    if resp.status != 200:
                        return {"ok": False, "error": f"HTTP {resp.status}", "raw": await resp.text()}
                    data = await resp.json()
                    return {"ok": True, "data": data}
        except Exception as exc:
            logger.warning("AI Guardian request failed url=%s error=%s", url, exc)
            return {"ok": False, "error": str(exc)}

    async def analyze_text(self, text: str) -> dict[str, Any]:
        """
        Отправляет текст на проверку безопасности.
        Возвращает: {safe: bool, reason: str, score: float}
        """
        result = await self._request_json("POST", "/analyze", {"text": text})
        if not result.get("ok"):
            return {"safe": True, "reason": "error_fallback", "error": result.get("error")}
        
        data = result.get("data", {})
        return {
            "safe": bool(data.get("safe", True)),
            "reason": str(data.get("reason", "")),
            "score": float(data.get("score", 0.0)),
        }

    async def get_chat_response(self, message: str) -> dict[str, Any]:
        """
        Запрашивает ответ у Support Bot (RAG Lite).
        Возвращает: {response: str, source: str}
        """
        result = await self._request_json("POST", "/chat", {"message": message})
        if not result.get("ok"):
            return {"response": "", "source": "error"}
        
        return result.get("data", {})

    async def health_check(self) -> bool:
        """Проверяет доступность сервиса."""
        result = await self._request_json("GET", "/")
        return bool(result.get("ok"))
