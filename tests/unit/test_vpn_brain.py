# -*- coding: utf-8 -*-
"""
Unit tests для VPNBrain (VPN Phase B).

Покрывают:
- known friend → LLM вызывается, ответ распарсен.
- unknown friend / generic вопрос — confidence отражает длину.
- ACTION: <slug> в ответе → suggested_action заполнен.
- LLM timeout → fallback ответ + confidence=0.0.
- Empty question → отдельный валидационный путь без LLM-вызова.
"""

from __future__ import annotations

import asyncio

import pytest

from src.core.vpn_brain import VPNBrain


@pytest.mark.asyncio
async def test_known_friend_question_returns_answer() -> None:
    """LLM вернул нормальный текст — answer.text совпадает, action=None."""
    calls: list[str] = []

    async def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return "Привет, Серёж! Чтобы перевыпустить ключ, открой настройки → VPN → Reissue."

    brain = VPNBrain(llm_callable=fake_llm)
    answer = await brain.answer_friend_question(
        friend_id="123",
        friend_name="Серёжа",
        question="Как перевыпустить ключ?",
        context={"history": [{"role": "user", "text": "Привет"}]},
    )
    assert answer.text.startswith("Привет, Серёж")
    assert answer.suggested_action is None
    assert answer.confidence >= 0.7
    assert answer.latency_ms >= 0
    assert len(calls) == 1
    assert "Серёжа" in calls[0]
    assert "Как перевыпустить ключ?" in calls[0]


@pytest.mark.asyncio
async def test_unknown_llm_not_configured_returns_fallback() -> None:
    """Без LLM callable → fallback-текст и confidence=0.0."""
    brain = VPNBrain(llm_callable=None)
    answer = await brain.answer_friend_question(
        friend_id="999",
        friend_name="Аноним",
        question="VPN не работает",
    )
    assert answer.confidence == 0.0
    assert answer.suggested_action is None
    assert "не могу подключиться" in answer.text.lower() or "vpn" in answer.text.lower()


@pytest.mark.asyncio
async def test_action_prefix_extracted_into_suggested_action() -> None:
    """Если LLM начал ответ с 'ACTION: reissue_key', slug попадает в suggested_action."""

    async def fake_llm(prompt: str) -> str:
        return "ACTION: reissue_key\nКонечно, давай перевыпущу твой ключ прямо сейчас."

    brain = VPNBrain(llm_callable=fake_llm)
    answer = await brain.answer_friend_question(
        friend_id="42",
        friend_name="Друг",
        question="Помоги с ключом",
    )
    assert answer.suggested_action == "reissue_key"
    assert "ACTION:" not in answer.text
    assert answer.text.startswith("Конечно")
    assert answer.confidence >= 0.75


@pytest.mark.asyncio
async def test_llm_timeout_returns_fallback() -> None:
    """LLM зависает дольше timeout_s → fallback текст + confidence=0.0."""

    async def hanging_llm(prompt: str) -> str:
        await asyncio.sleep(5.0)
        return "should not reach"

    brain = VPNBrain(llm_callable=hanging_llm, timeout_s=0.05)
    answer = await brain.answer_friend_question(
        friend_id="1",
        friend_name="Тест",
        question="Привет",
    )
    assert answer.confidence == 0.0
    assert "не могу" in answer.text.lower() or "vpn" in answer.text.lower()
    assert answer.suggested_action is None


@pytest.mark.asyncio
async def test_empty_question_returns_validation_message_without_llm_call() -> None:
    """Пустой вопрос → особый ответ, LLM не вызывается."""
    called = False

    async def fake_llm(prompt: str) -> str:
        nonlocal called
        called = True
        return "не должно вызваться"

    brain = VPNBrain(llm_callable=fake_llm)
    answer = await brain.answer_friend_question(
        friend_id="7",
        friend_name="Друг",
        question="   ",
    )
    assert called is False
    assert answer.confidence == 0.0
    assert "пуст" in answer.text.lower() or "vpn" in answer.text.lower()
