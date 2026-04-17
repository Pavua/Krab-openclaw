# -*- coding: utf-8 -*-
"""
TypingKeepalive — async context manager для корректного жизненного цикла
Telegram `typing…` indicator во время long-running LLM/tool-flow.

Мотивация (Session 11, фидбек после outage 17.04):
- Telegram auto-clears `chat_action` через ~5 секунд, поэтому long-running
  задачи (ask/ans с rerank/tool-loop) должны периодически re-send action;
- однако существующий `_keep_typing_alive` не отправляет явный
  `ChatAction.CANCEL` при завершении — если LLM падает в exception ДО того
  как запланирован cleanup, finalizer может не сработать, и пользователи
  в групповом чате видят «печатает...» часами (observed: «eNULL и
  Yung Nagato печатают» часами во время outage 17.04);
- context-manager даёт гарантию: `__aexit__` всегда вызывается при выходе
  из `async with`, даже если внутри бросили `asyncio.CancelledError`
  (включая Wave-2 stagnation-cancel) или произвольное исключение.

Контракт:
- `__aenter__` сразу запускает фоновую keep-alive задачу (интервал 4 сек);
- `__aexit__` отменяет keep-alive, ждёт его graceful shutdown, затем
  отправляет explicit `ChatAction.CANCEL` — Telegram мгновенно убирает
  indicator на клиентах. Все ошибки сети подавляются (best-effort).

Адаптация: можно оборачивать LLM-вызовы без изменения имеющегося
stop_event + task паттерна; opt-in для новых call sites.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pyrogram import enums

from ..core.logger import get_logger

logger = get_logger(__name__)


# Интервал keep-alive: Telegram typing-action живёт ~5 сек, шлём каждые 4
# чтобы был запас на jitter транспорта.
_TYPING_KEEPALIVE_INTERVAL_SEC = 4.0


class TypingKeepalive:
    """Async context manager: поддерживает typing-indicator и гарантированно гасит его на выходе.

    Пример:
        async with TypingKeepalive(client, chat_id):
            response = await llm_call(...)
        # На выходе typing сразу очищен (explicit CANCEL) даже при exception.

    Параметры:
        client: Pyrogram Client (или mock с `send_chat_action`).
        chat_id: int/str chat identifier для Telegram.
        action: optional override (по умолчанию `ChatAction.TYPING`). Удобно
                для `RECORD_AUDIO` в voice-mode или `UPLOAD_PHOTO` при OCR.
        interval_sec: период keep-alive, по умолчанию 4.0 сек.
    """

    def __init__(
        self,
        client: Any,
        chat_id: int | str,
        *,
        action: Any = None,
        interval_sec: float = _TYPING_KEEPALIVE_INTERVAL_SEC,
    ) -> None:
        self._client = client
        self._chat_id = chat_id
        # Pyrogram принимает как enums.ChatAction так и строку ("typing"/"cancel").
        self._action = action if action is not None else enums.ChatAction.TYPING
        self._interval_sec = max(1.0, float(interval_sec))
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> "TypingKeepalive":
        # Первый action отсылаем СРАЗУ — без этого клиент первые ~4 сек
        # видит «тишину», что делает UX хуже чем до фикса.
        try:
            await self._client.send_chat_action(self._chat_id, self._action)
        except Exception:  # noqa: BLE001
            # Сетевые ошибки не должны ломать основной flow — тихо игнорим.
            pass
        self._task = asyncio.create_task(self._keepalive_loop())
        return self

    async def _keepalive_loop(self) -> None:
        """Периодически подтверждает typing-action до cancel()."""
        try:
            while True:
                try:
                    await asyncio.sleep(self._interval_sec)
                except asyncio.CancelledError:
                    return
                try:
                    await self._client.send_chat_action(self._chat_id, self._action)
                except asyncio.CancelledError:
                    return
                except Exception:  # noqa: BLE001
                    # Любой транспортный сбой (flood-wait, disconnect) — не раним
                    # main flow; keep-alive вернётся при следующей итерации.
                    continue
        except asyncio.CancelledError:
            return

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        # Шаг 1: глушим периодический keep-alive.
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                # Мы только что сами отменили задачу — CancelledError ожидаем.
                pass
            self._task = None
        # Шаг 2: explicit CANCEL — Telegram мгновенно убирает typing на клиентах.
        # Критично при exception/cancel: без этого indicator «завис» на ~5 сек
        # либо навсегда (если сервер пропустил auto-expire тик из-за outage).
        try:
            await self._client.send_chat_action(self._chat_id, enums.ChatAction.CANCEL)
        except Exception as cancel_exc:  # noqa: BLE001
            # Финальная очистка best-effort: даже если сеть легла, мы уже
            # вернули контроль caller-у — не переопределяем их exception.
            logger.debug(
                "typing_keepalive_cancel_failed",
                chat_id=self._chat_id,
                error=str(cancel_exc),
            )
        # Важно: exception из `async with`-блока НЕ подавляется (return None).
