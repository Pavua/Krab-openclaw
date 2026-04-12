# -*- coding: utf-8 -*-
"""
WebSocket-подписчик на realtime-поток Voice Gateway.

Назначение:
1) Подключается к WS /v1/sessions/{id}/stream
2) Диспатчит stt.final → LLM-анализ контекста → reasoning.suggestion
3) Автоматический reconnect при обрывах связи
4) Запускается/останавливается вместе с translator session

Архитектура:
- Не блокирует основной pipeline перевода (STT→Translate→TTS идёт как обычно)
- Krab Core выступает наблюдателем с правом вмешательства
- Публикует подсказки через POST /v1/sessions/{id}/events
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger("KrabCore.VoiceGatewaySubscriber")

# Типы callback-ов для обработки событий
EventHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


class VoiceGatewayEventSubscriber:
    """Async WebSocket клиент для подписки на realtime-поток сессии Voice Gateway.

    Использование:
        subscriber = VoiceGatewayEventSubscriber(
            base_url="http://127.0.0.1:8090",
            api_key="...",
        )
        subscriber.on_stt_final = my_handler
        await subscriber.start("vs_abc123")
        # ... позже
        await subscriber.stop()
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8090",
        api_key: str = "",
        reconnect_delay_sec: float = 2.0,
        max_reconnect_attempts: int = 10,
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.reconnect_delay_sec = reconnect_delay_sec
        self.max_reconnect_attempts = max_reconnect_attempts

        # Обработчики событий — назначаются внешним кодом
        self.on_stt_final: EventHandler | None = None
        self.on_translation_final: EventHandler | None = None
        self.on_call_state: EventHandler | None = None
        self.on_any_event: EventHandler | None = None

        self._task: asyncio.Task[None] | None = None
        self._session_id: str = ""
        self._stopped = True

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def session_id(self) -> str:
        return self._session_id

    async def start(self, session_id: str) -> None:
        """Запускает подписку на поток указанной сессии."""
        if self.is_running:
            await self.stop()

        self._session_id = session_id
        self._stopped = False
        self._task = asyncio.create_task(
            self._run_loop(session_id),
            name=f"vg_subscriber_{session_id}",
        )
        logger.info("Subscriber запущен для сессии %s", session_id)

    async def stop(self) -> None:
        """Останавливает подписку и ожидает завершения задачи."""
        self._stopped = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Subscriber остановлен для сессии %s", self._session_id)

    def _make_ws_url(self, session_id: str) -> str:
        """Строит WebSocket URL из HTTP base URL."""
        url = self.base_url
        if url.startswith("https://"):
            url = "wss://" + url[8:]
        elif url.startswith("http://"):
            url = "ws://" + url[7:]
        elif not url.startswith(("ws://", "wss://")):
            url = "ws://" + url
        return f"{url}/v1/sessions/{session_id}/stream"

    async def _run_loop(self, session_id: str) -> None:
        """Основной цикл с reconnect логикой."""
        attempt = 0
        while not self._stopped and attempt < self.max_reconnect_attempts:
            try:
                await self._connect_and_listen(session_id)
                attempt = 0  # Успешное соединение сбрасывает счётчик
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt += 1
                if self._stopped:
                    break
                logger.warning(
                    "WS подписка оборвалась (попытка %d/%d): %s",
                    attempt,
                    self.max_reconnect_attempts,
                    exc,
                )
                if attempt < self.max_reconnect_attempts:
                    await asyncio.sleep(self.reconnect_delay_sec * min(attempt, 5))

        if not self._stopped and attempt >= self.max_reconnect_attempts:
            logger.error(
                "Subscriber: исчерпаны попытки reconnect для %s",
                session_id,
            )

    async def _connect_and_listen(self, session_id: str) -> None:
        """Устанавливает WebSocket соединение и слушает события."""
        try:
            import websockets
        except ImportError:
            logger.error("websockets не установлен. Установите: pip install websockets")
            self._stopped = True
            return

        ws_url = self._make_ws_url(session_id)
        extra_headers = {}
        if self.api_key:
            extra_headers["Authorization"] = f"Bearer {self.api_key}"

        logger.info("Подключаюсь к %s", ws_url)
        async with websockets.connect(
            ws_url,
            additional_headers=extra_headers,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            logger.info("WS подключение установлено для %s", session_id)
            async for raw_message in ws:
                if self._stopped:
                    break
                await self._dispatch(raw_message)

    async def _dispatch(self, raw_message: str | bytes) -> None:
        """Парсит JSON-событие и вызывает соответствующий handler."""
        try:
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8")
            event = json.loads(raw_message)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.debug("Невалидное WS сообщение: %s", exc)
            return

        event_type = event.get("type", "")
        data = event.get("data", {})

        # Универсальный handler
        if self.on_any_event:
            try:
                await self.on_any_event(event_type, data)
            except Exception as exc:
                logger.error("on_any_event ошибка: %s", exc)

        # Типизированные handlers
        handler: EventHandler | None = None
        if event_type == "stt.final":
            handler = self.on_stt_final
        elif event_type == "translation.final":
            handler = self.on_translation_final
        elif event_type == "call.state":
            handler = self.on_call_state

        if handler:
            try:
                await handler(event_type, data)
            except Exception as exc:
                logger.error("Handler %s ошибка: %s", event_type, exc)
