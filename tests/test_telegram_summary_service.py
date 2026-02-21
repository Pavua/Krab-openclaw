# -*- coding: utf-8 -*-
"""Тесты для TelegramSummaryService."""

import pytest

from src.core.telegram_summary_service import SummaryRequest, TelegramSummaryService


class _MockMessage:
    def __init__(self, text: str, username: str, ts):
        self.text = text
        self.caption = None
        self.date = ts
        self.from_user = type("U", (), {"username": username, "first_name": username})()


class _MockClient:
    def __init__(self, messages):
        self._messages = messages

    async def get_chat_history(self, chat_id, limit=50):
        for msg in self._messages[:limit]:
            yield msg


class _MockRouter:
    async def route_query(self, prompt: str, task_type: str = "reasoning", use_rag: bool = False):
        return f"SUMMARY[{task_type}]::{len(prompt)}"


@pytest.mark.asyncio
async def test_summary_single_pass():
    from datetime import datetime

    messages = [
        _MockMessage(text=f"msg {i}", username="u", ts=datetime(2026, 2, 12, 12, 0))
        for i in range(30)
    ]
    svc = TelegramSummaryService(router=_MockRouter(), map_reduce_threshold=500)
    client = _MockClient(messages=messages)
    res = await svc.summarize(client, SummaryRequest(chat_id=1, limit=30, focus="решения"))
    assert res.startswith("SUMMARY[reasoning]")


def test_summary_limit_clamp():
    svc = TelegramSummaryService(router=_MockRouter(), min_limit=20, max_limit=2000)
    assert svc.clamp_limit(1) == 20
    assert svc.clamp_limit(3000) == 2000
    assert svc.clamp_limit(150) == 150


@pytest.mark.asyncio
async def test_summary_empty():
    svc = TelegramSummaryService(router=_MockRouter())
    client = _MockClient(messages=[])
    res = await svc.summarize(client, SummaryRequest(chat_id=1, limit=50))
    assert res.startswith("❌")


@pytest.mark.asyncio
async def test_summary_only_media():
    from datetime import datetime
    
    class EmptyMessage(_MockMessage):
        def __init__(self):
            super().__init__("", "u", datetime(2026, 2, 12, 12, 0))
            
    messages = [EmptyMessage() for _ in range(5)]
    svc = TelegramSummaryService(router=_MockRouter())
    client = _MockClient(messages=messages)
    res = await svc.summarize(client, SummaryRequest(chat_id=1, limit=5))
    assert res.startswith("❌")
