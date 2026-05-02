"""Tests for src/core/hallucination_guard.py.

Wave 9-A — defensive guard against LLM-fabricated tool-success reports.
"""

from __future__ import annotations

import pytest

from src.core.hallucination_guard import (
    HALLUCINATION_WARNING_PREFIX,
    detect_hallucinated_tool_success,
)


def test_detect_no_match_normal_text():
    """Plain conversational text — никаких triggers."""
    text = "Привет! Сегодня хорошая погода, можно погулять."
    assert detect_hallucinated_tool_success(text, []) is False


def test_detect_with_real_tool_call():
    """Pattern matches AND real write-tool ran → not a hallucination."""
    text = "Отправил сообщение Дашке, доставка прошла успешно."
    snapshot = [
        {"name": "telegram_send_message", "status": "done", "started_at": 0.0},
    ]
    assert detect_hallucinated_tool_success(text, snapshot) is False


def test_detect_hallucination_no_tool_call():
    """Pattern matches AND no tool calls → hallucination detected."""
    text = (
        "Отправил в личку Дашке: 'Ты какашка 🦀'\n\n"
        "Доставка прошла успешно: chat id 1467625424, message id 1677"
    )
    assert detect_hallucinated_tool_success(text, []) is True


def test_detect_hallucination_only_unrelated_tools():
    """Tool calls existed, but none are write-actions → still hallucination."""
    text = "Отправил сообщение пользователю, message id 9999."
    snapshot = [
        {"name": "fs_read_file", "status": "done"},
        {"name": "krab_memory_search", "status": "done"},
    ]
    assert detect_hallucinated_tool_success(text, snapshot) is True


def test_detect_specific_msg_id_pattern():
    """Structured 'message id NNN' tells without a tool call → flagged."""
    text = "Готово. message id: 1677"
    assert detect_hallucinated_tool_success(text, []) is True


def test_detect_msg_id_with_tool_call_ok():
    """Same structured tell, but real tool ran → not flagged."""
    text = "Готово. message id: 1677"
    snapshot = [{"name": "telegram_send_message", "status": "done"}]
    assert detect_hallucinated_tool_success(text, snapshot) is False


def test_detect_multiple_languages():
    """English variants of tool-success phrases also detected."""
    assert detect_hallucinated_tool_success("Sent to user. Delivered successfully.", []) is True
    assert detect_hallucinated_tool_success("The message was sent. msg_id 4242", []) is True


def test_no_false_positive_on_quote():
    """Quoted text mentioning 'отправил' without action keywords нет trigger."""
    text = 'Пользователь написал: "я уже отправил тебе письмо вчера".'
    # 'отправил тебе письмо' — нет keyword (лич/дм/чат/пользовател/сообщ
    # действительно есть 'письмо' но не в списке) — должно НЕ trigger.
    # NB: pattern требует одного из ключевых слов рядом — здесь "тебе письмо"
    # не матчит ни одно из них.
    assert detect_hallucinated_tool_success(text, []) is False


def test_no_false_positive_on_creative_completion():
    """'Сделал презентацию' без send-context → не trigger."""
    text = "Сделал презентацию по твоей теме, прикрепляю к ответу."
    assert detect_hallucinated_tool_success(text, []) is False


def test_empty_response():
    assert detect_hallucinated_tool_success("", []) is False
    assert detect_hallucinated_tool_success("", None) is False


def test_none_response_safe():
    """Non-string input не должен падать."""
    assert detect_hallucinated_tool_success(None, []) is False  # type: ignore[arg-type]


def test_running_tool_not_counted_as_success():
    """Tool ещё выполняется — не legit success."""
    text = "Отправил сообщение в чат."
    snapshot = [{"name": "telegram_send_message", "status": "running"}]
    assert detect_hallucinated_tool_success(text, snapshot) is True


def test_legit_imessage_send():
    """imessage_send_imessage — допустимый write-tool."""
    text = "Отправил сообщение в iMessage Дашке."
    snapshot = [{"name": "send_imessage", "status": "done"}]
    assert detect_hallucinated_tool_success(text, snapshot) is False


def test_warning_prefix_constant():
    """Public constant exists and is non-empty."""
    assert HALLUCINATION_WARNING_PREFIX
    assert "⚠️" in HALLUCINATION_WARNING_PREFIX


@pytest.mark.parametrize(
    "phrase",
    [
        "Отправил в личку Анне.",
        "Отправила в чат сообщение.",
        "Сообщение отправлено.",
        "Доставка прошла успешно.",
        "Sent to the user successfully.",
        "Delivered to recipient.",
        "Message was sent.",
    ],
)
def test_pattern_variants_no_tool(phrase: str):
    """Каждый pattern одиночно triggers без tool-call."""
    assert detect_hallucinated_tool_success(phrase, []) is True
