# -*- coding: utf-8 -*-
"""Тесты TodoExtractor — извлечение TODO из текста (Idea 21)."""

from __future__ import annotations

from src.core.todo_extractor import ExtractedTodo, TodoExtractor


def test_ru_patterns_basic_task() -> None:
    """Русские триггеры task: 'надо сделать X', 'не забыть Y'."""
    extractor = TodoExtractor()
    todos = extractor.extract_todos("Надо сделать отчёт к понедельнику.")
    assert any(t.category == "task" and "отчёт" in t.action_text.lower() for t in todos)

    todos2 = extractor.extract_todos("Не забыть забрать посылку")
    assert any(t.category == "task" and "посылк" in t.action_text.lower() for t in todos2)


def test_ru_patterns_shopping_and_contact() -> None:
    """Русские категории shopping и contact."""
    extractor = TodoExtractor()
    todos = extractor.extract_todos("Купить молоко и хлеб в магазине")
    assert any(t.category == "shopping" for t in todos)

    todos2 = extractor.extract_todos("Позвонить маме вечером")
    assert any(t.category == "contact" and "мам" in t.action_text.lower() for t in todos2)


def test_en_patterns() -> None:
    """Английские паттерны: need to / buy / call / TODO:."""
    extractor = TodoExtractor()
    todos = extractor.extract_todos("I need to finish the report today", lang="en")
    assert any(t.category == "task" for t in todos)

    todos2 = extractor.extract_todos("buy bread tomorrow", lang="en")
    assert any(t.category == "shopping" for t in todos2)

    todos3 = extractor.extract_todos("TODO: refactor the parser", lang="en")
    assert any(t.category == "task" and t.confidence >= 0.85 for t in todos3)


def test_multi_todo_extraction() -> None:
    """Несколько TODO в одном тексте — все извлекаются."""
    extractor = TodoExtractor()
    text = "Надо сделать презентацию. Купить кофе. Позвонить Андрею."
    todos = extractor.extract_todos(text)
    categories = {t.category for t in todos}
    assert "task" in categories
    assert "shopping" in categories
    assert "contact" in categories
    assert len(todos) >= 3


def test_low_confidence_ignored() -> None:
    """Слишком короткие/слабые матчи отсекаются."""
    extractor = TodoExtractor(min_confidence=0.9)
    # base 0.75, action короткий → confidence ниже 0.9
    todos = extractor.extract_todos("надо это")
    assert todos == []


def test_empty_text_returns_empty() -> None:
    """Пустой/whitespace вход → []."""
    extractor = TodoExtractor()
    assert extractor.extract_todos("") == []
    assert extractor.extract_todos("   \n\t  ") == []
    assert extractor.extract_todos("просто болтовня без триггеров") == []


def test_category_detection_idea() -> None:
    """Категория idea для 'идея: X' и 'idea: X'."""
    extractor = TodoExtractor()
    todos_ru = extractor.extract_todos("Идея: добавить тёмную тему в панель")
    assert any(t.category == "idea" for t in todos_ru)

    todos_en = extractor.extract_todos("idea: ship a CLI tool", lang="en")
    assert any(t.category == "idea" for t in todos_en)


def test_extracted_todo_to_dict() -> None:
    """ExtractedTodo сериализуется в dict."""
    todo = ExtractedTodo(action_text="купить хлеб", category="shopping", confidence=0.85)
    d = todo.to_dict()
    assert d == {"action_text": "купить хлеб", "category": "shopping", "confidence": 0.85}
