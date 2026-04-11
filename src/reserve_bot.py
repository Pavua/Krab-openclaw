# -*- coding: utf-8 -*-
"""
reserve_bot.py — резервный Telegram бот для fallback-канала владельца.

Phase 2.1 Master Plan v3:
- Запускается в bot-mode (pyrofork) параллельно с userbot.
- Используется когда userbot оффлайн: proactive_watch alerts, critical traces.
- Отвечает на /status пингуя Owner Panel (/api/health/lite).
- Не хранит сессию на диске (in_memory=True).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .config import config
from .core.logger import get_logger

logger = get_logger(__name__)

_OPENCLAW_JSON = Path.home() / ".openclaw" / "openclaw.json"
_MSG_CHUNK = 4096


def _resolve_bot_token() -> str:
    """Токен бота: config.TELEGRAM_BOT_TOKEN → openclaw.json channels.telegram.botToken."""
    tok = str(getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    if tok:
        return tok
    try:
        raw = _OPENCLAW_JSON.read_text(encoding="utf-8")
        data = json.loads(raw)
        return str(data.get("channels", {}).get("telegram", {}).get("botToken", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _resolve_owner_ids() -> list[int]:
    """Owner IDs: объединение config.OWNER_USER_IDS и openclaw.json allowFrom."""
    ids: set[int] = set()
    for uid in getattr(config, "OWNER_USER_IDS", []) or []:
        try:
            ids.add(int(uid))
        except (ValueError, TypeError):
            pass
    try:
        raw = _OPENCLAW_JSON.read_text(encoding="utf-8")
        data = json.loads(raw)
        for uid in data.get("channels", {}).get("telegram", {}).get("allowFrom", []) or []:
            try:
                ids.add(int(uid))
            except (ValueError, TypeError):
                pass
    except Exception:  # noqa: BLE001
        pass
    return list(ids)


class ReserveBotBridge:
    """Резервный Telegram бот — fallback канал для owner-уведомлений."""

    def __init__(self) -> None:
        self._token: str = _resolve_bot_token()
        self._owner_ids: list[int] = _resolve_owner_ids()
        self._client: Any = None
        self._running: bool = False

    @property
    def is_configured(self) -> bool:
        return bool(self._token and self._owner_ids)

    @property
    def is_running(self) -> bool:
        return self._running and self._client is not None

    async def start(self) -> bool:
        """
        Запускает резервный бот.

        Возвращает True при успехе, False если не сконфигурирован или ошибка.
        При ошибке — логирует и не бросает исключение.
        """
        if not self.is_configured:
            logger.debug("reserve_bot_not_configured")
            return False
        if self._running:
            return True
        try:
            from pyrogram import Client, filters
            from pyrogram.types import Message

            client = Client(
                name="reserve_bot",
                api_id=int(getattr(config, "TELEGRAM_API_ID", 0) or 0),
                api_hash=str(getattr(config, "TELEGRAM_API_HASH", "") or ""),
                bot_token=self._token,
                in_memory=True,
            )

            owner_ids = self._owner_ids

            @client.on_message(filters.command("status") & filters.user(owner_ids))
            async def _handle_status(_, message: Message) -> None:  # type: ignore[misc]
                await self._handle_status_cmd(message)

            @client.on_message(filters.command("silence") & filters.user(owner_ids))
            async def _handle_silence(_, message: Message) -> None:  # type: ignore[misc]
                await self._handle_api_toggle(message, "/api/silence/toggle", "/api/silence/status", "Silence")

            @client.on_message(filters.command("notify") & filters.user(owner_ids))
            async def _handle_notify(_, message: Message) -> None:  # type: ignore[misc]
                await self._handle_api_toggle(message, "/api/notify/toggle", "/api/notify/status", "Notify")

            @client.on_message(filters.command("voice") & filters.user(owner_ids))
            async def _handle_voice(_, message: Message) -> None:  # type: ignore[misc]
                await self._handle_api_get(message, "/api/voice/profile", "Voice Profile")

            @client.on_message(filters.command("tasks") & filters.user(owner_ids))
            async def _handle_tasks(_, message: Message) -> None:  # type: ignore[misc]
                await self._handle_api_get(message, "/api/swarm/task-board", "Task Board")

            @client.on_message(filters.text & filters.user(owner_ids))
            async def _handle_text(_, message: Message) -> None:  # type: ignore[misc]
                await message.reply_text(
                    "⚡ Reserve bot активен.\n"
                    "Команды: /status /silence /notify /voice /tasks"
                )

            await client.start()
            self._client = client
            self._running = True
            logger.info("reserve_bot_started", owner_ids=owner_ids)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("reserve_bot_start_failed", error=str(exc))
            self._running = False
            self._client = None
            return False

    async def stop(self) -> None:
        """Останавливает резервный бот. Не бросает исключений."""
        if not self._running or self._client is None:
            return
        try:
            await self._client.stop()
            logger.info("reserve_bot_stopped")
        except Exception as exc:  # noqa: BLE001
            logger.warning("reserve_bot_stop_failed", error=str(exc))
        finally:
            self._running = False
            self._client = None

    async def send_to_owner(self, text: str) -> bool:
        """
        Отправляет сообщение всем owner IDs.

        Возвращает True если хотя бы одна отправка успешна.
        """
        if not self.is_running:
            return False
        clean = str(text or "").strip()
        if not clean:
            return False
        sent = False
        for uid in self._owner_ids:
            try:
                for chunk in _split_text(clean):
                    await self._client.send_message(uid, chunk)
                sent = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("reserve_bot_send_failed", uid=uid, error=str(exc))
        return sent

    async def _handle_status_cmd(self, message: Any) -> None:
        """Отвечает на /status — пингует Owner Panel."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as http:
                resp = await http.get("http://127.0.0.1:8080/api/health/lite")
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            await message.reply_text(f"✅ Краб online\n```json\n{json.dumps(body, ensure_ascii=False, indent=2)}\n```")
        except Exception as exc:  # noqa: BLE001
            await message.reply_text(f"⚠️ Краб недоступен: {exc}")

    async def _handle_api_get(self, message: Any, endpoint: str, label: str) -> None:
        """GET endpoint и показать результат."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as http:
                resp = await http.get(f"http://127.0.0.1:8080{endpoint}")
                body = resp.json()
            text = json.dumps(body, ensure_ascii=False, indent=2)
            await message.reply_text(f"📊 {label}\n```json\n{text[:3500]}\n```")
        except Exception as exc:  # noqa: BLE001
            await message.reply_text(f"⚠️ {label} недоступен: {exc}")

    async def _handle_api_toggle(self, message: Any, toggle_ep: str, status_ep: str, label: str) -> None:
        """Toggle через POST, показать status через GET."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as http:
                # Сначала toggle
                resp = await http.post(f"http://127.0.0.1:8080{toggle_ep}", json={})
                result = resp.json()
            action = result.get("action", "toggled")
            await message.reply_text(f"🔄 {label}: **{action}**")
        except Exception as exc:  # noqa: BLE001
            await message.reply_text(f"⚠️ {label} toggle failed: {exc}")


def _split_text(text: str, limit: int = _MSG_CHUNK) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts = []
    while text:
        parts.append(text[:limit])
        text = text[limit:]
    return parts


reserve_bot = ReserveBotBridge()

__all__ = ["ReserveBotBridge", "reserve_bot"]
