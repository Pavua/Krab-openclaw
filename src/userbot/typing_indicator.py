# -*- coding: utf-8 -*-
"""
Async context manager `TypingIndicator` — "human-like presence" слой для Telegram.

Wave 173 (Session 48). Делает Краба похожим на живого собеседника: пока LLM
генерирует ответ, в Telegram идёт «Краб печатает...» (или «записывает голосовое»,
«загружает фото» — в зависимости от типа ответа).

Зачем отдельный module, если в `background_tasks.py` уже есть
`_keep_typing_alive`?
- `_keep_typing_alive` — staticmethod mixin'а, требует ручного управления
  `asyncio.Event` + `asyncio.create_task` + cleanup. Уже используется в трёх
  местах (userbot_bridge, llm_flow, swarm_team_listener), везде дублируется
  одинаковый boilerplate (start task → cancel → gather).
- Этот module предоставляет чистый `async with TypingIndicator(client, chat_id):`
  API. Старт/cancel/CANCEL action — внутри. Ошибки SendChatAction
  (FloodWait, network) глушатся, чтобы не ломать reply flow.
- Env gate `KRAB_TYPING_INDICATOR_ENABLED` + per-chat blocklist
  `KRAB_TYPING_INDICATOR_BLOCKED_CHATS` (стиль `VOICE_REPLY_BLOCKED_CHATS`).
  При disabled — context manager no-op (ничего не шлёт, не создаёт task).

Использование:
    ```python
    from pyrogram.enums import ChatAction
    from src.userbot.typing_indicator import TypingIndicator

    async with TypingIndicator(self.client, chat_id, action=ChatAction.TYPING):
        response = await llm_call()  # пока эта строка работает — юзер видит "печатает..."
    # на __aexit__ — отправляется ChatAction.CANCEL, indicator пропадает.
    ```

Action variation:
- text reply         → ChatAction.TYPING (default)
- voice generation   → ChatAction.RECORD_AUDIO (или UPLOAD_AUDIO в финале)
- photo generation   → ChatAction.UPLOAD_PHOTO
- document upload    → ChatAction.UPLOAD_DOCUMENT

Telegram chat action expires ~5 секунд, поэтому keep-alive loop пере-шлёт
action каждые 4 секунды до выхода из `with` блока.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Any

from ..core.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Конфигурация (env-driven)
# ---------------------------------------------------------------------------

# Re-send action каждые N секунд. Telegram indicator expires ~5s,
# 4s даёт margin на сетевой jitter.
_KEEP_ALIVE_INTERVAL_SEC: float = 4.0


def _is_globally_enabled() -> bool:
    """Читает `KRAB_TYPING_INDICATOR_ENABLED`. Default ON (=1)."""
    return os.getenv("KRAB_TYPING_INDICATOR_ENABLED", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _blocked_chat_ids() -> set[str]:
    """Per-chat opt-out blocklist в стиле `VOICE_REPLY_BLOCKED_CHATS`.

    Формат: comma-separated chat_id в `KRAB_TYPING_INDICATOR_BLOCKED_CHATS`.
    Пример: `-1001587432709,123456789` — отключить indicator в этих чатах.
    Default — пустой (indicator работает во всех чатах).
    """
    raw = os.getenv("KRAB_TYPING_INDICATOR_BLOCKED_CHATS", "")
    return {s.strip() for s in raw.split(",") if s.strip()}


def is_enabled_for_chat(chat_id: int | str | None) -> bool:
    """`True`, если для данного чата typing indicator активен.

    Учитывает global gate + per-chat blocklist.
    """
    if not _is_globally_enabled():
        return False
    if chat_id is None:
        return True
    return str(chat_id) not in _blocked_chat_ids()


# ---------------------------------------------------------------------------
# Внутренний keep-alive loop
# ---------------------------------------------------------------------------


async def _keep_alive_loop(
    client: Any,
    chat_id: int | str,
    action: Any,
    stop_event: asyncio.Event,
    *,
    interval_sec: float = _KEEP_ALIVE_INTERVAL_SEC,
) -> None:
    """Шлёт `send_chat_action` каждые `interval_sec` пока не задан `stop_event`.

    Любые ошибки (FloodWait, network) — лог warning, продолжаем (не падаем,
    не ломаем reply flow). На выходе (`stop_event` или CancelledError) шлём
    `ChatAction.CANCEL` чтобы Telegram мгновенно убрал indicator у клиентов.
    """
    try:
        while not stop_event.is_set():
            try:
                await client.send_chat_action(chat_id, action)
            except Exception as exc:  # noqa: BLE001
                # FloodWait / network — best-effort: лог и продолжаем.
                logger.warning(
                    "typing_indicator_send_failed",
                    chat_id=str(chat_id),
                    error=str(exc),
                )
            try:
                await asyncio.wait_for(
                    asyncio.shield(stop_event.wait()),
                    timeout=interval_sec,
                )
            except asyncio.TimeoutError:
                # Норма: интервал истёк, шлём action ещё раз.
                pass
    finally:
        # Explicit CANCEL — без него indicator висит до auto-expire (~5s),
        # а при пропущенном финальном тике (server-side flake) — навсегда.
        # Выполняется даже при CancelledError благодаря finally.
        try:
            from pyrogram import enums as _pg_enums  # noqa: PLC0415

            await client.send_chat_action(chat_id, _pg_enums.ChatAction.CANCEL)
        except Exception:  # noqa: BLE001
            # Сеть могла лечь — Telegram сам auto-expire'нет через ~5s.
            pass


# ---------------------------------------------------------------------------
# Public API: TypingIndicator async context manager
# ---------------------------------------------------------------------------


class TypingIndicator:
    """Async context manager: «Краб печатает...» на время блока кода.

    Параметры:
    - `client` — Pyrogram Client (обычно `self.client` / `self.app` бота).
    - `chat_id` — Telegram chat id (int или str).
    - `action` — `pyrogram.enums.ChatAction` (default TYPING). Удобные
      алиасы: см. helper'ы `text_typing()`, `recording_voice()` ниже.
    - `interval_sec` — каждые N секунд пере-шлём action (default 4.0).
    - `enabled` — `None` → читать env gate, `False/True` → форсировать.

    Эксепшены внутри блока пробрасываются наружу (контекст-менеджер не глушит).
    """

    def __init__(
        self,
        client: Any,
        chat_id: int | str,
        *,
        action: Any = None,
        interval_sec: float = _KEEP_ALIVE_INTERVAL_SEC,
        enabled: bool | None = None,
    ) -> None:
        self._client = client
        self._chat_id = chat_id
        self._interval_sec = interval_sec
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

        # Effective enabled: явный override > env gate > blocklist.
        if enabled is None:
            self._enabled = is_enabled_for_chat(chat_id)
        else:
            self._enabled = bool(enabled)

        # Lazy-resolve action чтобы не падать в no-op режиме без pyrogram.
        self._action = action
        if self._action is None and self._enabled:
            try:
                from pyrogram.enums import ChatAction  # noqa: PLC0415

                self._action = ChatAction.TYPING
            except Exception:  # noqa: BLE001
                # Pyrogram недоступен — отключаемся, не падаем.
                self._enabled = False
                logger.debug(
                    "typing_indicator_pyrogram_unavailable",
                    chat_id=str(chat_id),
                )

    async def __aenter__(self) -> TypingIndicator:
        if not self._enabled:
            return self
        if self._client is None:
            logger.debug(
                "typing_indicator_no_client",
                chat_id=str(self._chat_id),
            )
            return self
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(
            _keep_alive_loop(
                self._client,
                self._chat_id,
                self._action,
                self._stop_event,
                interval_sec=self._interval_sec,
            )
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Останавливаем loop. Любые exceptions в самом loop'е (включая CANCEL
        # send в finally) глушим — не маскируем ошибку body'я блока.
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        # Не возвращаем True — exceptions из body пробрасываем.


# ---------------------------------------------------------------------------
# Helpers: типизированные shortcut'ы для разных типов ответа
# ---------------------------------------------------------------------------


def text_typing(client: Any, chat_id: int | str, **kwargs: Any) -> TypingIndicator:
    """`async with text_typing(client, chat_id):` — «печатает...»."""
    try:
        from pyrogram.enums import ChatAction  # noqa: PLC0415

        action = ChatAction.TYPING
    except Exception:  # noqa: BLE001
        action = None
    return TypingIndicator(client, chat_id, action=action, **kwargs)


def recording_voice(client: Any, chat_id: int | str, **kwargs: Any) -> TypingIndicator:
    """`async with recording_voice(client, chat_id):` — «записывает голосовое»."""
    try:
        from pyrogram.enums import ChatAction  # noqa: PLC0415

        action = ChatAction.RECORD_AUDIO
    except Exception:  # noqa: BLE001
        action = None
    return TypingIndicator(client, chat_id, action=action, **kwargs)


def uploading_photo(client: Any, chat_id: int | str, **kwargs: Any) -> TypingIndicator:
    """`async with uploading_photo(client, chat_id):` — «загружает фото»."""
    try:
        from pyrogram.enums import ChatAction  # noqa: PLC0415

        action = ChatAction.UPLOAD_PHOTO
    except Exception:  # noqa: BLE001
        action = None
    return TypingIndicator(client, chat_id, action=action, **kwargs)


def uploading_document(client: Any, chat_id: int | str, **kwargs: Any) -> TypingIndicator:
    """`async with uploading_document(client, chat_id):` — «загружает файл»."""
    try:
        from pyrogram.enums import ChatAction  # noqa: PLC0415

        action = ChatAction.UPLOAD_DOCUMENT
    except Exception:  # noqa: BLE001
        action = None
    return TypingIndicator(client, chat_id, action=action, **kwargs)


__all__ = [
    "TypingIndicator",
    "is_enabled_for_chat",
    "recording_voice",
    "text_typing",
    "uploading_document",
    "uploading_photo",
]
