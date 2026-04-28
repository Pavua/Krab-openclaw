# -*- coding: utf-8 -*-
"""Тесты для MemorySummarizer (Idea 14, Session 28).

Покрытие:
- should_summarize: корректно реагирует на threshold (под/над/равно)
- summarize_window: дёргает инжектированный LLM и записывает результат
- persist: после summarize JSON содержит запись
- get_summary: достаёт по chat_id, нормализуя int/str
- dedup: повторный вызов с тем же набором IDs не идёт в LLM повторно
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.core.memory_summarizer import MemorySummarizer, RollingSummary


def _fake_clock(value: datetime):
    """Возвращает callable, отдающий зафиксированный datetime."""
    return lambda: value


@pytest.mark.asyncio
async def test_should_summarize_threshold(tmp_path: Path):
    """Threshold honored: under → False, equal/above → True; covered IDs учтены."""
    storage = tmp_path / "rolling.json"
    summarizer = MemorySummarizer(storage_path=storage, threshold=10)

    # Никаких записей → нужно сравнить count с 0
    assert summarizer.should_summarize("chat-1", message_count=9) is False
    assert summarizer.should_summarize("chat-1", message_count=10) is True
    assert summarizer.should_summarize("chat-1", message_count=42) is True

    # Невалидные входы → False (граничные кейсы)
    assert summarizer.should_summarize("", message_count=100) is False
    assert summarizer.should_summarize("chat-1", message_count=0) is False
    assert summarizer.should_summarize("chat-1", message_count="bad") is False  # type: ignore[arg-type]

    # Если уже покрыто 50 IDs, threshold считается от delta
    summarizer.record_summary(
        RollingSummary(
            chat_id="chat-1",
            summary_text="старое",
            covers_message_ids=list(range(1, 51)),
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    assert summarizer.should_summarize("chat-1", message_count=55) is False  # delta=5
    assert summarizer.should_summarize("chat-1", message_count=60) is True  # delta=10


@pytest.mark.asyncio
async def test_summarize_window_calls_llm(tmp_path: Path):
    """summarize_window дёргает инжектированный llm_call и возвращает RollingSummary."""
    storage = tmp_path / "rolling.json"
    captured_prompts: list[str] = []

    async def fake_llm(prompt: str) -> str:
        captured_prompts.append(prompt)
        return "Договорились встретиться в среду в 18:00."

    fixed = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    summarizer = MemorySummarizer(
        storage_path=storage,
        llm_call=fake_llm,
        now_fn=_fake_clock(fixed),
    )

    messages = [
        {"id": 100, "sender": "alice", "text": "Привет, давай в среду?"},
        {"id": 101, "sender": "bob", "text": "Окей, в 18:00 норм."},
        {"id": 102, "sender": "alice", "text": "Договорились!"},
    ]

    result = await summarizer.summarize_window("chat-42", messages)

    assert result is not None
    assert result.chat_id == "chat-42"
    assert "среду" in result.summary_text
    assert result.covers_message_ids == [100, 101, 102]
    assert result.generated_at == fixed.isoformat()
    assert len(captured_prompts) == 1
    assert "ДИАЛОГ:" in captured_prompts[0]
    assert "alice: Привет" in captured_prompts[0]


@pytest.mark.asyncio
async def test_summarize_window_persists_to_disk(tmp_path: Path):
    """После summarize_window JSON-файл содержит запись."""
    storage = tmp_path / "rolling.json"

    async def fake_llm(prompt: str) -> str:
        return "Краткая сводка обсуждения."

    summarizer = MemorySummarizer(storage_path=storage, llm_call=fake_llm)

    await summarizer.summarize_window(
        "chat-7",
        [
            {"id": 1, "sender": "u1", "text": "А"},
            {"id": 2, "sender": "u2", "text": "Б"},
        ],
    )

    assert storage.exists(), "JSON не был записан на диск"
    raw = json.loads(storage.read_text(encoding="utf-8"))
    assert "chat-7" in raw
    entry = raw["chat-7"]
    assert entry["summary_text"] == "Краткая сводка обсуждения."
    assert entry["covers_message_ids"] == [1, 2]

    # Новый инстанс грузит запись с диска
    fresh = MemorySummarizer(storage_path=storage)
    loaded = fresh.get_summary("chat-7")
    assert loaded is not None
    assert loaded.summary_text == "Краткая сводка обсуждения."
    assert loaded.covers_message_ids == [1, 2]


@pytest.mark.asyncio
async def test_get_summary_normalizes_chat_id(tmp_path: Path):
    """get_summary должен принимать как int, так и str chat_id."""

    async def fake_llm(prompt: str) -> str:
        return "Сводка."

    summarizer = MemorySummarizer(storage_path=tmp_path / "r.json", llm_call=fake_llm)
    await summarizer.summarize_window(
        -1003703978531,
        [{"id": 9, "sender": "x", "text": "test message body"}],
    )

    by_int = summarizer.get_summary(-1003703978531)
    by_str = summarizer.get_summary("-1003703978531")
    assert by_int is not None
    assert by_str is not None
    assert by_int.summary_text == by_str.summary_text == "Сводка."

    # list_summaries возвращает копию, мутация не ломает store
    snapshots = summarizer.list_summaries()
    assert len(snapshots) == 1
    snapshots.clear()
    assert len(summarizer.list_summaries()) == 1


@pytest.mark.asyncio
async def test_summarize_window_dedup_by_message_ids(tmp_path: Path):
    """Повторный summarize с тем же набором message_ids не идёт в LLM повторно."""
    storage = tmp_path / "rolling.json"
    call_count = 0

    async def fake_llm(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"Сводка #{call_count}"

    summarizer = MemorySummarizer(storage_path=storage, llm_call=fake_llm)

    msgs = [
        {"id": 10, "sender": "a", "text": "первое"},
        {"id": 11, "sender": "b", "text": "второе"},
    ]

    first = await summarizer.summarize_window("chat-d", msgs)
    second = await summarizer.summarize_window("chat-d", msgs)
    assert first is not None and second is not None
    assert call_count == 1, "LLM должен быть вызван только один раз"
    assert first.summary_text == second.summary_text == "Сводка #1"

    # Новые IDs → новый вызов
    msgs2 = [
        {"id": 12, "sender": "a", "text": "третье"},
        {"id": 13, "sender": "b", "text": "четвёртое"},
    ]
    third = await summarizer.summarize_window("chat-d", msgs2)
    assert third is not None
    assert call_count == 2
    assert third.summary_text == "Сводка #2"
    # Новый snapshot перезаписывает старый — это by design (rolling).
    assert summarizer.get_summary("chat-d") == third
