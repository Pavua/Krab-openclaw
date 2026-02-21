# -*- coding: utf-8 -*-
"""
Telegram Summary Service.

Назначение:
1) Собирать последние X сообщений выбранного чата через Telegram API.
2) Строить саммари (обычный и map-reduce режимы).
3) Поддерживать фокус по теме (--focus).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class SummaryRequest:
    """Параметры запроса саммари."""

    chat_id: int
    limit: int
    focus: str = ""
    language: str = "ru"


class TelegramSummaryService:
    """Сервис построения саммари по живой истории Telegram-чата."""

    def __init__(
        self,
        router,
        min_limit: int = 20,
        max_limit: int = 2000,
        map_reduce_threshold: int = 500,
        chunk_size: int = 250,
    ):
        self.router = router
        self.min_limit = min_limit
        self.max_limit = max_limit
        self.map_reduce_threshold = map_reduce_threshold
        self.chunk_size = chunk_size

    def clamp_limit(self, raw_limit: int) -> int:
        """Нормализует X в допустимые границы."""
        return max(self.min_limit, min(int(raw_limit), self.max_limit))

    async def fetch_chat_messages(self, client, chat_id: int, limit: int) -> list[dict[str, str]]:
        """Забирает последние текстовые сообщения чата."""
        rows: list[dict[str, str]] = []
        async for msg in client.get_chat_history(chat_id, limit=limit):
            text = (msg.text or msg.caption or "").strip()
            if not text:
                continue
            sender = (
                msg.from_user.username
                if msg.from_user and msg.from_user.username
                else (msg.from_user.first_name if msg.from_user else "Unknown")
            )
            ts = msg.date.strftime("%Y-%m-%d %H:%M") if msg.date else ""
            rows.append(
                {
                    "sender": str(sender),
                    "text": text[:700],
                    "ts": ts,
                }
            )
        rows.reverse()
        return rows

    async def summarize(self, client, req: SummaryRequest, chat_title: str = "") -> str:
        """Генерирует итоговое саммари по запросу."""
        limit = self.clamp_limit(req.limit)
        messages = await self.fetch_chat_messages(client, req.chat_id, limit=limit)
        if not messages:
            return "❌ Не найдено текстовых сообщений для саммари."

        if len(messages) > self.map_reduce_threshold:
            return await self._summarize_map_reduce(messages, req.focus, chat_title, req.language)
        return await self._summarize_single_pass(messages, req.focus, chat_title, req.language)

    async def _summarize_single_pass(
        self,
        messages: list[dict[str, str]],
        focus: str,
        chat_title: str,
        language: str,
    ) -> str:
        """Обычный режим суммаризации без промежуточных стадий."""
        history_text = "\n".join(
            f"[{m['ts']}] {m['sender']}: {m['text']}"
            for m in messages
        )
        focus_block = (
            f"\nПриоритет фокуса: {focus}\nВыдели только относящееся к этой теме."
            if focus
            else ""
        )
        prompt = (
            f"Сделай краткое и структурированное саммари чата '{chat_title or req_safe_chat_title(messages)}'.\n"
            f"Язык ответа: {language}.\n"
            f"Формат: 1) Ключевые темы 2) Решения 3) Важные факты 4) To-Do/риски.{focus_block}\n\n"
            f"ЛОГ СООБЩЕНИЙ:\n{history_text}"
        )
        return await self.router.route_query(prompt=prompt, task_type="reasoning", use_rag=False)

    async def _summarize_map_reduce(
        self,
        messages: list[dict[str, str]],
        focus: str,
        chat_title: str,
        language: str,
    ) -> str:
        """Map-reduce режим для длинных чатов."""
        chunk_summaries: list[str] = []
        for idx in range(0, len(messages), self.chunk_size):
            chunk = messages[idx : idx + self.chunk_size]
            chunk_text = "\n".join(f"[{m['ts']}] {m['sender']}: {m['text']}" for m in chunk)
            focus_block = f"\nФокус: {focus}" if focus else ""
            prompt = (
                f"Сожми этот фрагмент переписки в 6-10 пунктов. Язык: {language}.{focus_block}\n\n"
                f"{chunk_text}"
            )
            chunk_summary = await self.router.route_query(
                prompt=prompt,
                task_type="reasoning",
                use_rag=False,
            )
            chunk_summaries.append(f"[Chunk {idx // self.chunk_size + 1}] {chunk_summary}")

        reduce_prompt = (
            f"Объедини промежуточные summary в итоговое саммари чата '{chat_title or req_safe_chat_title(messages)}'.\n"
            f"Язык: {language}. Формат: 1) Главные темы 2) Решения 3) Факты 4) Риски/To-Do."
        )
        if focus:
            reduce_prompt += f"\nСделай акцент на теме: {focus}."
        reduce_prompt += "\n\nПРОМЕЖУТОЧНЫЕ САММАРИ:\n" + "\n\n".join(chunk_summaries)

        return await self.router.route_query(
            prompt=reduce_prompt,
            task_type="reasoning",
            use_rag=False,
        )


def req_safe_chat_title(messages: list[dict[str, str]]) -> str:
    """Технический fallback заголовка."""
    if not messages:
        return "unknown"
    first_ts = messages[0].get("ts", "")
    last_ts = messages[-1].get("ts", "")
    if first_ts and last_ts:
        return f"period {first_ts} .. {last_ts}"
    return datetime.now().strftime("%Y-%m-%d")
