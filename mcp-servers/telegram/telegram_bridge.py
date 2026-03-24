# -*- coding: utf-8 -*-
"""
Telegram Bridge — Pyrogram singleton wrapper.

Отвечает исключительно за Telegram API: инициализация клиента, lifecycle,
и все операции с чатами/сообщениями. Не содержит MCP-зависимостей.

Конфигурация через переменные окружения:
  TELEGRAM_API_ID          — числовой App ID (обязательно)
  TELEGRAM_API_HASH        — App Hash (обязательно)
  TELEGRAM_SESSION_NAME    — базовое имя сессии (default: "krab")
  MCP_TELEGRAM_SESSION_DIR — директория для хранения .session файла
                             (default: ~/.krab_mcp_sessions/)
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from pyrogram import Client
from pyrogram.types import Message

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _session_dir() -> Path:
    """Директория для хранения Telegram session-файла MCP."""
    custom = os.getenv("MCP_TELEGRAM_SESSION_DIR", "").strip()
    if custom:
        p = Path(custom).expanduser()
    else:
        p = Path.home() / ".krab_mcp_sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _session_name() -> str:
    """Имя session-файла: отдельное от боевого Краба, не конфликтует."""
    base = os.getenv("TELEGRAM_SESSION_NAME", "krab").strip()
    return f"{base}_mcp"


def _make_client() -> Client:
    api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not api_id_raw or not api_hash:
        raise RuntimeError(
            "TELEGRAM_API_ID и TELEGRAM_API_HASH должны быть заданы в .env"
        )
    session_path = str(_session_dir() / _session_name())
    return Client(
        name=session_path,
        api_id=int(api_id_raw),
        api_hash=api_hash,
    )


def _msg_to_dict(msg: Message) -> dict[str, Any]:
    """Преобразует Pyrogram Message в JSON-сериализуемый dict."""
    return {
        "id": msg.id,
        "chat_id": msg.chat.id if msg.chat else None,
        "chat_title": getattr(msg.chat, "title", None) or getattr(msg.chat, "first_name", None),
        "from_user": msg.from_user.first_name if msg.from_user else None,
        "text": msg.text or msg.caption or "",
        "date": msg.date.isoformat() if msg.date else None,
        "has_media": msg.media is not None,
        "media_type": str(msg.media) if msg.media else None,
        "reply_to_message_id": msg.reply_to_message_id,
    }


class TelegramBridge:
    """
    Singleton-обёртка над Pyrogram Client.

    Использование:
        bridge = TelegramBridge()
        await bridge.start()    # ← вызывается из FastMCP lifespan
        ...
        await bridge.stop()     # ← вызывается из FastMCP lifespan
    """

    def __init__(self) -> None:
        self._client: Client | None = None

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Стартует Pyrogram Client. Вызывается из FastMCP lifespan."""
        self._client = _make_client()
        await self._client.start()

    async def stop(self) -> None:
        """Корректно останавливает клиент. Вызывается из FastMCP lifespan."""
        if self._client is not None:
            try:
                await self._client.stop()
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._client = None

    @property
    def client(self) -> Client:
        if self._client is None:
            raise RuntimeError("TelegramBridge не инициализирован. Вызови start() сначала.")
        return self._client

    # ─── Telegram API методы ──────────────────────────────────────────────────

    async def get_dialogs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Возвращает список последних диалогов (чаты, группы, каналы)."""
        result: list[dict[str, Any]] = []
        async for dialog in self.client.get_dialogs(limit=limit):
            chat = dialog.chat
            result.append({
                "id": chat.id,
                "title": getattr(chat, "title", None) or getattr(chat, "first_name", None),
                "type": str(chat.type),
                "username": getattr(chat, "username", None),
                "unread_count": dialog.unread_messages_count,
                "top_message": dialog.top_message.text if dialog.top_message else None,
            })
        return result

    async def get_chat_history(
        self, chat_id: int | str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Возвращает последние сообщения из чата."""
        messages: list[dict[str, Any]] = []
        async for msg in self.client.get_chat_history(chat_id, limit=limit):
            messages.append(_msg_to_dict(msg))
        return messages

    async def send_message(self, chat_id: int | str, text: str) -> dict[str, Any]:
        """Отправляет текстовое сообщение и возвращает его метаданные."""
        msg = await self.client.send_message(chat_id, text)
        return _msg_to_dict(msg)

    async def download_media(
        self, chat_id: int | str, message_id: int
    ) -> str:
        """
        Скачивает медиафайл (фото / документ / голосовое) из сообщения.

        Возвращает абсолютный путь к скачанному файлу во временной директории.
        """
        msgs = await self.client.get_messages(chat_id, message_ids=message_id)
        if msgs is None:
            raise ValueError(f"Сообщение {message_id} не найдено в чате {chat_id}")
        msg: Message = msgs if not isinstance(msgs, list) else msgs[0]
        if not msg.media:
            raise ValueError(f"Сообщение {message_id} не содержит медиафайла")

        tmp_dir = Path(tempfile.gettempdir()) / "krab_mcp_media"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        file_path = await self.client.download_media(msg, file_name=str(tmp_dir) + "/")
        return str(file_path)

    async def get_voice_file(
        self, chat_id: int | str, message_id: int
    ) -> str:
        """
        Скачивает голосовое сообщение или audio и возвращает путь к файлу.
        Используется `telegram_transcribe_voice` для передачи в KrabEar.
        """
        return await self.download_media(chat_id, message_id)

    async def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Глобальный поиск по всем чатам Telegram."""
        results: list[dict[str, Any]] = []
        async for msg in self.client.search_global(query, limit=limit):
            results.append(_msg_to_dict(msg))
        return results

    async def edit_message(
        self, chat_id: int | str, message_id: int, text: str
    ) -> dict[str, Any]:
        """Редактирует ранее отправленное сообщение."""
        msg = await self.client.edit_message_text(chat_id, message_id, text)
        return _msg_to_dict(msg)
