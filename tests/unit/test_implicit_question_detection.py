# -*- coding: utf-8 -*-
"""
tests/unit/test_implicit_question_detection.py — Wave 26-B.

Покрывает detect_implicit_question:
  - позитивные: "ну а если?", "?", "кто там?", "что думаешь о Vertex?", "а ты откуда?"
  - негативные: обычный текст, пустая строка, out-of-window
  - ENV gate: KRAB_IMPLICIT_QUESTION_DETECTION_ENABLED=0 → всегда False
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from src.core.trigger_detector import detect_implicit_question, last_krab_msg

# ---------------------------------------------------------------------------
# Вспомогательный fixture — сброс store
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_store():
    last_krab_msg._store.clear()
    yield
    last_krab_msg._store.clear()


# ---------------------------------------------------------------------------
# 1. in_window=True + вопросительный текст → True
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "ну а если?",
        "?",
        "кто там?",
        "Что думаешь о Vertex?",
        "а ты откуда?",
        "почему это работает так?",
        "когда будет готово?",
        "а если бы иначе?",
        "как думаешь, стоит?",
        "ну как тебе?",
        "и что дальше?",
        "что скажешь насчёт этого?",
        "где найти документацию?",
        "куда смотреть?",
        "зачем это нужно?",
        "откуда такие данные?",
        "продолжай",
        "интересно",
    ],
)
def test_detect_implicit_question_positive(text: str) -> None:
    """Вопросительные сообщения в окне → True."""
    assert detect_implicit_question(text, chat_id="100", in_window=True) is True


# ---------------------------------------------------------------------------
# 2. in_window=True + обычный текст → False
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "обычный текст без вопроса",
        "просто сообщение",
        "спасибо за ответ",
        "ок",
        "понял",
        "лады",
        "хорошо сделано",
        "отличный результат",
    ],
)
def test_detect_implicit_question_no_question_marker(text: str) -> None:
    """Текст без вопросительных маркеров → False, даже в окне."""
    assert detect_implicit_question(text, chat_id="100", in_window=True) is False


# ---------------------------------------------------------------------------
# 3. in_window=False + вопрос → False (вне окна)
# ---------------------------------------------------------------------------


def test_detect_implicit_question_out_of_window() -> None:
    """Вопрос, но Краб не отвечал в нужном окне → False."""
    assert detect_implicit_question("а ты откуда?", chat_id="200", in_window=False) is False


# ---------------------------------------------------------------------------
# 4. Только "?" → True
# ---------------------------------------------------------------------------


def test_detect_implicit_question_just_question_mark() -> None:
    """Одиночный "?" в окне → True."""
    assert detect_implicit_question("?", chat_id="300", in_window=True) is True


# ---------------------------------------------------------------------------
# 5. Что думаешь о Vertex? → True
# ---------------------------------------------------------------------------


def test_detect_implicit_question_vertex() -> None:
    """'Что думаешь о Vertex?' → True."""
    assert detect_implicit_question("Что думаешь о Vertex?", chat_id="400", in_window=True) is True


# ---------------------------------------------------------------------------
# 6. "кто там?" → True
# ---------------------------------------------------------------------------


def test_detect_implicit_question_kto_tam() -> None:
    """'кто там?' → True (начинается с 'кто ')."""
    assert detect_implicit_question("кто там?", chat_id="500", in_window=True) is True


# ---------------------------------------------------------------------------
# 7. Пустая строка → False
# ---------------------------------------------------------------------------


def test_detect_implicit_question_empty() -> None:
    """Пустая строка → False независимо от окна."""
    assert detect_implicit_question("", chat_id="600", in_window=True) is False
    assert detect_implicit_question("   ", chat_id="600", in_window=True) is False


# ---------------------------------------------------------------------------
# 8. ENV gate отключён → всегда False
# ---------------------------------------------------------------------------


def test_detect_implicit_question_env_gate_disabled() -> None:
    """При KRAB_IMPLICIT_QUESTION_DETECTION_ENABLED=0 → всегда False."""
    import src.core.trigger_detector as td_mod

    original = td_mod.KRAB_IMPLICIT_QUESTION_DETECTION_ENABLED
    try:
        td_mod.KRAB_IMPLICIT_QUESTION_DETECTION_ENABLED = False
        # Даже явный вопрос в окне → False
        assert detect_implicit_question("а как так?", chat_id="700", in_window=True) is False
        assert detect_implicit_question("?", chat_id="700", in_window=True) is False
    finally:
        td_mod.KRAB_IMPLICIT_QUESTION_DETECTION_ENABLED = original


# ---------------------------------------------------------------------------
# 9. Реальное окно — in_window=None, last_krab_msg не писал → False
# ---------------------------------------------------------------------------


def test_detect_implicit_question_real_window_empty() -> None:
    """in_window=None, Краб не отвечал в этом чате → False."""
    # Убеждаемся что store пустой (autouse fixture)
    assert detect_implicit_question("почему?", chat_id="800") is False


# ---------------------------------------------------------------------------
# 10. Реальное окно — in_window=None, Краб только что ответил → True
# ---------------------------------------------------------------------------


def test_detect_implicit_question_real_window_recent() -> None:
    """in_window=None, Краб только что ответил → True для вопроса."""
    chat_id = "900"
    last_krab_msg.record(chat_id)
    assert detect_implicit_question("где документация?", chat_id=chat_id) is True
