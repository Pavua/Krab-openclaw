# -*- coding: utf-8 -*-
"""
Hammerspoon bridge для Краба.

Зачем нужен этот модуль:
- даёт owner-команде `!hs` единую точку управления окнами macOS через
  Hammerspoon HTTP API (localhost:10101);
- отделяет transport-логику (HTTP-клиент, retry, graceful unavailable) от
  команды-хендлера;
- при отсутствии Hammerspoon все методы возвращают `is_available() == False`
  без броска исключений.

Требования:
- Hammerspoon должен быть запущен и ~/.hammerspoon/init.lua должен содержать
  krab-hs HTTP server (см. hammerspoon/init.lua в репозитории).
- По умолчанию сервер работает на localhost:10101.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_BASE_URL = "http://localhost:10101"
_DEFAULT_TIMEOUT  = 5.0   # секунд


class HammerspoonBridgeError(Exception):
    """Ошибка взаимодействия с Hammerspoon."""


class HammerspoonBridge:
    """
    Тонкий HTTP-клиент поверх Hammerspoon krab-hs server.

    Архитектурные решения:
    - Мы используем `httpx.AsyncClient` напрямую, а не синглтон httpx, чтобы
      bridge можно было легко замокать в тестах.
    - Все публичные методы `async`; is_available() — sync (проверяет только порт,
      не делает HTTP).
    - При любой ошибке соединения бросаем `HammerspoonBridgeError`, не OSError/
      httpx-специфику — это защищает хендлер от деталей транспорта.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        pass_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout
        self._headers: dict[str, str] = {}
        if pass_key:
            self._headers["X-Krab-Pass"] = pass_key

    # ─── Доступность ──────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """
        Быстрая sync-проверка: HTTP-сервер Hammerspoon слушает нужный порт.

        Не делаем реального HTTP-запроса; лишь проверяем, что локальный TCP-порт
        принимает соединения. Это работает быстро (~1 ms) и подходит для guard
        на уровне команды без async overhead.
        """
        import socket

        port = int(self.base_url.split(":")[-1]) if ":" in self.base_url else 10101
        try:
            with socket.create_connection(("localhost", port), timeout=0.3):
                return True
        except OSError:
            return False

    # ─── Internal HTTP helpers ────────────────────────────────────────────────

    async def _get(self, path: str) -> dict[str, Any]:
        url = self.base_url + path
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, headers=self._headers)
            return self._parse_response(resp)
        except httpx.TransportError as exc:
            raise HammerspoonBridgeError(f"connection_failed: {exc}") from exc

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = self.base_url + path
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload, headers=self._headers)
            return self._parse_response(resp)
        except httpx.TransportError as exc:
            raise HammerspoonBridgeError(f"connection_failed: {exc}") from exc

    @staticmethod
    def _parse_response(resp: httpx.Response) -> dict[str, Any]:
        try:
            data: dict[str, Any] = resp.json()
        except Exception as exc:
            raise HammerspoonBridgeError(
                f"invalid_json (HTTP {resp.status_code}): {resp.text[:200]}"
            ) from exc
        if resp.status_code >= 400:
            raise HammerspoonBridgeError(
                data.get("error") or f"http_error_{resp.status_code}"
            )
        return data

    # ─── Публичный API ────────────────────────────────────────────────────────

    async def status(self) -> dict[str, Any]:
        """Возвращает версию Hammerspoon и количество экранов."""
        return await self._get("/status")

    async def list_windows(self) -> list[dict[str, Any]]:
        """Перечислить все видимые окна (id, title, app)."""
        data = await self._get("/windows")
        return data.get("windows", [])

    async def focus(self, app: str) -> dict[str, Any]:
        """Сфокусировать главное окно приложения по имени."""
        return await self._post("/window", {"action": "focus", "app": app})

    async def move(
        self,
        *,
        app: str = "",
        x: float = 0,
        y: float = 0,
        w: float = 1.0,
        h: float = 1.0,
    ) -> dict[str, Any]:
        """
        Переместить/изменить размер окна.

        Координаты принимаются как доля экрана (0..1) или абсолютные пиксели
        (> 2). Lua-код в init.lua сам разрешает режим.
        """
        return await self._post(
            "/window",
            {"action": "move", "app": app, "x": x, "y": y, "w": w, "h": h},
        )

    async def tile(self, preset: str = "left", app: str = "") -> dict[str, Any]:
        """
        Применить preset-раскладку: left, right, top, bottom, full.

        Hammerspoon использует `moveToUnit()` — это надёжнее, чем пиксельное
        позиционирование при нескольких мониторах разного DPI.
        """
        return await self._post(
            "/window", {"action": "tile", "preset": preset, "app": app}
        )


# Глобальный синглтон
hammerspoon = HammerspoonBridge()
