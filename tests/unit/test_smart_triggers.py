# -*- coding: utf-8 -*-
"""
tests/unit/test_smart_triggers.py — семантический детектор неявных триггеров.

Покрывает:
  - implicit question patterns (русские)
  - follow-up к Крабу (окно 5 минут)
  - generic AI alias + вопрос
  - non-trigger (просто чат)
  - пороговое поведение (threshold)
  - TriggerType.NONE при пустом тексте
"""

from __future__ import annotations

import time

import pytest

from src.core.trigger_detector import (
    TriggerType,
    _LastKrabMsgStore,
    detect_implicit_mention,
    is_implicit_trigger,
    last_krab_msg,
)

# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_last_krab_msg():
    """Сбрасываем глобальный store перед каждым тестом."""
    last_krab_msg._store.clear()
    yield
    last_krab_msg._store.clear()


# ---------------------------------------------------------------------------
# 1. Implicit question patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Кто-то знает как это починить?",
        "подскажите пожалуйста как сделать",
        "кто в теме по этой теме?",
        "кто шарит в Python?",
        "как решить эту задачу?",
        "посоветуйте хорошую библиотеку",
        "помогите разобраться с этим",
        "кто-нибудь поможет с настройкой?",
        "можете подсказать?",
    ],
)
def test_implicit_question_detected(text: str) -> None:
    result = detect_implicit_mention(text, "chat_1")
    assert result.trigger_type == TriggerType.IMPLICIT_QUESTION
    assert result.score >= 0.4


def test_implicit_question_matched_text() -> None:
    result = detect_implicit_mention("Кто-то знает как парсить JSON?", "chat_1")
    assert result.matched  # должен вернуть что сработало
    assert result.trigger_type == TriggerType.IMPLICIT_QUESTION


# ---------------------------------------------------------------------------
# 2. Follow-up к Крабу
# ---------------------------------------------------------------------------


def test_followup_detected_within_window() -> None:
    last_krab_msg.record("group_42")
    result = detect_implicit_mention("ещё вопрос", "group_42")
    assert result.trigger_type == TriggerType.FOLLOWUP_TO_KRAB
    assert result.score >= 0.6


def test_followup_not_detected_after_window() -> None:
    store = _LastKrabMsgStore()
    # Симулируем запись 6 минут назад
    store._store["old_chat"] = time.monotonic() - (6 * 60)
    result = detect_implicit_mention("hello", "old_chat")
    # follow-up window истёк — не должен срабатывать
    assert result.trigger_type != TriggerType.FOLLOWUP_TO_KRAB


def test_followup_ignored_if_reply_to_other() -> None:
    last_krab_msg.record("group_99")
    result = detect_implicit_mention(
        "ответ другому",
        "group_99",
        is_reply_to_explicit_msg=True,  # reply на чужое сообщение — не follow-up
    )
    assert result.trigger_type != TriggerType.FOLLOWUP_TO_KRAB


def test_followup_not_detected_for_unknown_chat() -> None:
    result = detect_implicit_mention("текст", "chat_never_seen")
    assert result.trigger_type == TriggerType.NONE


def test_last_krab_msg_record_and_within_window() -> None:
    store = _LastKrabMsgStore()
    assert not store.within_window("x")
    store.record("x")
    assert store.within_window("x")
    assert not store.within_window("x", window=0)  # window=0 всегда False


# ---------------------------------------------------------------------------
# 3. Generic AI alias + вопрос
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "бот, ты знаешь ответ?",
        "ии, что думаешь об этом?",
        "нейронка может решить это?",
        "ai подскажет как сделать?",
        "assistant, помоги?",
    ],
)
def test_generic_ai_alias_with_question(text: str) -> None:
    result = detect_implicit_mention(text, "chat_2")
    assert result.trigger_type == TriggerType.GENERIC_AI
    assert result.score >= 0.5


def test_generic_ai_without_question_no_trigger() -> None:
    # «бот» без вопросительного знака — не триггер
    result = detect_implicit_mention("бот молчит", "chat_2")
    assert result.trigger_type == TriggerType.NONE


# ---------------------------------------------------------------------------
# 4. Non-trigger (просто чат)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Привет всем!",
        "Сегодня хорошая погода",
        "Пойдём обедать?",
        "Спасибо за инфо",
        "ок понял",
        "",
        "   ",
    ],
)
def test_no_trigger_for_plain_chat(text: str) -> None:
    result = detect_implicit_mention(text, "chat_3")
    assert result.trigger_type == TriggerType.NONE


# ---------------------------------------------------------------------------
# 5. Threshold behaviour
# ---------------------------------------------------------------------------


def test_threshold_blocks_implicit_question_at_high_threshold() -> None:
    # С очень высоким порогом (0.9) вопрос «в воздух» не срабатывает
    result = detect_implicit_mention(
        "Кто-то знает как это сделать?",
        "chat_4",
        threshold=0.9,
    )
    assert result.trigger_type == TriggerType.NONE
    assert result.score == pytest.approx(0.4)  # счёт возвращается, но ниже порога


def test_threshold_allows_implicit_question_at_default() -> None:
    result = detect_implicit_mention(
        "Кто-то знает как это сделать?",
        "chat_4",
        threshold=0.4,
    )
    assert result.trigger_type == TriggerType.IMPLICIT_QUESTION


def test_threshold_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_IMPLICIT_TRIGGER_THRESHOLD", "0.9")
    # После изменения env — _threshold() должна вернуть 0.9
    from src.core import trigger_detector

    assert trigger_detector._threshold() == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# 6. is_implicit_trigger shortcut
# ---------------------------------------------------------------------------


def test_is_implicit_trigger_true() -> None:
    assert is_implicit_trigger("подскажите как настроить?", "chat_5")


def test_is_implicit_trigger_false() -> None:
    assert not is_implicit_trigger("окей, спасибо", "chat_5")


def test_is_implicit_trigger_followup() -> None:
    last_krab_msg.record("chat_6")
    assert is_implicit_trigger("ещё один вопрос", "chat_6")


# ---------------------------------------------------------------------------
# 7. Edge cases
# ---------------------------------------------------------------------------


def test_empty_text_returns_none() -> None:
    result = detect_implicit_mention("", "chat_7")
    assert result.trigger_type == TriggerType.NONE
    assert result.score == 0.0


def test_invalid_threshold_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_IMPLICIT_TRIGGER_THRESHOLD", "not_a_number")
    from src.core import trigger_detector

    assert trigger_detector._threshold() == pytest.approx(0.4)
