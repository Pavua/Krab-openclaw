# -*- coding: utf-8 -*-
"""
Тесты PersonalTodoService — персональный менеджер задач.

Покрываем:
1) add() — добавление задач, автоинкремент ID
2) list_all() — порядок: активные → выполненные
3) mark_done() — отметка, несуществующий ID
4) delete() — удаление, несуществующий ID
5) clear_done() — очистка выполненных
6) render() — форматирование, пустой список
7) персистентность — данные сохраняются между инстанциями
8) граничные случаи: пустое хранилище, повреждённый файл
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.personal_todo import PersonalTodoService


# ─── фикстуры ────────────────────────────────────────────────────────────────

@pytest.fixture
def todo_file(tmp_path: Path) -> Path:
    """Временный файл хранилища задач."""
    return tmp_path / "personal_todos.json"


@pytest.fixture
def svc(todo_file: Path) -> PersonalTodoService:
    """PersonalTodoService с временным хранилищем."""
    return PersonalTodoService(todo_file=todo_file)


# ─── add ─────────────────────────────────────────────────────────────────────

class TestAdd:
    def test_add_returns_item(self, svc: PersonalTodoService) -> None:
        """add() возвращает объект с корректными полями."""
        item = svc.add("Купить молоко")
        assert item["text"] == "Купить молоко"
        assert item["done"] is False
        assert item["id"] == 1
        assert item["created_at"] > 0

    def test_add_strips_whitespace(self, svc: PersonalTodoService) -> None:
        """Лишние пробелы обрезаются."""
        item = svc.add("  Позвонить врачу  ")
        assert item["text"] == "Позвонить врачу"

    def test_add_increments_id(self, svc: PersonalTodoService) -> None:
        """Каждая новая задача получает уникальный ID."""
        ids = [svc.add(f"задача {i}")["id"] for i in range(3)]
        assert ids == [1, 2, 3]

    def test_add_persists_to_file(self, svc: PersonalTodoService, todo_file: Path) -> None:
        """Задача сохраняется в файл."""
        svc.add("Написать отчёт")
        assert todo_file.exists()
        data = json.loads(todo_file.read_text())
        assert len(data) == 1
        assert data[0]["text"] == "Написать отчёт"

    def test_add_multiple_persisted(self, svc: PersonalTodoService, todo_file: Path) -> None:
        """Несколько задач сохраняются корректно."""
        svc.add("Задача A")
        svc.add("Задача B")
        svc.add("Задача C")
        data = json.loads(todo_file.read_text())
        assert len(data) == 3

    def test_add_after_delete_last_id_reused_or_incremented(self, svc: PersonalTodoService) -> None:
        """После удаления последней задачи новый ID >= старого max."""
        svc.add("Задача 1")
        svc.add("Задача 2")
        svc.add("Задача 3")
        svc.delete(3)  # удаляем последнюю
        item = svc.add("Задача 4")
        # ID должен быть > max оставшихся (2), т.е. >= 3
        assert item["id"] >= 3


# ─── list_all ─────────────────────────────────────────────────────────────────

class TestListAll:
    def test_list_empty(self, svc: PersonalTodoService) -> None:
        """Пустой список — пустой результат."""
        assert svc.list_all() == []

    def test_list_order_active_first(self, svc: PersonalTodoService) -> None:
        """Активные задачи идут перед выполненными."""
        svc.add("А")
        svc.add("Б")
        svc.add("В")
        svc.mark_done(1)
        items = svc.list_all()
        # Первые два — активные (id 2, 3), последний — выполненный (id 1)
        assert not items[0]["done"]
        assert not items[1]["done"]
        assert items[2]["done"]

    def test_list_within_group_sorted_by_id(self, svc: PersonalTodoService) -> None:
        """Внутри группы задачи отсортированы по id."""
        for i in range(5):
            svc.add(f"задача {i}")
        ids = [t["id"] for t in svc.list_all()]
        assert ids == sorted(ids)


# ─── mark_done ───────────────────────────────────────────────────────────────

class TestMarkDone:
    def test_mark_done_existing(self, svc: PersonalTodoService) -> None:
        """Задача отмечается как выполненная."""
        svc.add("тест")
        item = svc.mark_done(1)
        assert item is not None
        assert item["done"] is True

    def test_mark_done_persists(self, svc: PersonalTodoService, todo_file: Path) -> None:
        """Статус done сохраняется в файл."""
        svc.add("тест")
        svc.mark_done(1)
        data = json.loads(todo_file.read_text())
        assert data[0]["done"] is True

    def test_mark_done_nonexistent(self, svc: PersonalTodoService) -> None:
        """Несуществующий ID → None."""
        svc.add("тест")
        result = svc.mark_done(999)
        assert result is None

    def test_mark_done_already_done(self, svc: PersonalTodoService) -> None:
        """Повторная отметка не ломает данные."""
        svc.add("тест")
        svc.mark_done(1)
        item = svc.mark_done(1)
        assert item is not None
        assert item["done"] is True

    def test_mark_done_empty_list(self, svc: PersonalTodoService) -> None:
        """mark_done на пустом списке → None."""
        assert svc.mark_done(1) is None


# ─── delete ──────────────────────────────────────────────────────────────────

class TestDelete:
    def test_delete_existing(self, svc: PersonalTodoService, todo_file: Path) -> None:
        """Задача удаляется из списка."""
        svc.add("Удалить меня")
        result = svc.delete(1)
        assert result is True
        data = json.loads(todo_file.read_text())
        assert data == []

    def test_delete_nonexistent(self, svc: PersonalTodoService) -> None:
        """Несуществующий ID → False."""
        svc.add("тест")
        assert svc.delete(999) is False

    def test_delete_one_of_many(self, svc: PersonalTodoService) -> None:
        """Удаляется только указанная задача."""
        svc.add("А")
        svc.add("Б")
        svc.add("В")
        svc.delete(2)
        items = svc.list_all()
        assert len(items) == 2
        assert all(t["id"] != 2 for t in items)

    def test_delete_empty_list(self, svc: PersonalTodoService) -> None:
        """delete на пустом списке → False."""
        assert svc.delete(1) is False


# ─── clear_done ──────────────────────────────────────────────────────────────

class TestClearDone:
    def test_clear_done_removes_completed(self, svc: PersonalTodoService) -> None:
        """Выполненные задачи удаляются."""
        svc.add("А")
        svc.add("Б")
        svc.add("В")
        svc.mark_done(1)
        svc.mark_done(3)
        count = svc.clear_done()
        assert count == 2
        remaining = svc.list_all()
        assert len(remaining) == 1
        assert remaining[0]["id"] == 2

    def test_clear_done_none_done(self, svc: PersonalTodoService) -> None:
        """Если нет выполненных — возвращает 0."""
        svc.add("А")
        svc.add("Б")
        count = svc.clear_done()
        assert count == 0
        assert len(svc.list_all()) == 2

    def test_clear_done_empty_list(self, svc: PersonalTodoService) -> None:
        """clear_done на пустом списке → 0."""
        assert svc.clear_done() == 0

    def test_clear_done_all_done(self, svc: PersonalTodoService) -> None:
        """Если все выполнены — список становится пустым."""
        svc.add("А")
        svc.add("Б")
        svc.mark_done(1)
        svc.mark_done(2)
        count = svc.clear_done()
        assert count == 2
        assert svc.list_all() == []


# ─── render ──────────────────────────────────────────────────────────────────

class TestRender:
    def test_render_empty(self, svc: PersonalTodoService) -> None:
        """Пустой список — подсказка добавить задачу."""
        text = svc.render()
        assert "пуст" in text.lower()
        assert "!todo add" in text

    def test_render_header_counts(self, svc: PersonalTodoService) -> None:
        """Заголовок содержит счётчики активных и выполненных."""
        svc.add("А")
        svc.add("Б")
        svc.add("В")
        svc.mark_done(1)
        text = svc.render()
        assert "2 активных" in text
        assert "1 выполнено" in text

    def test_render_active_symbol(self, svc: PersonalTodoService) -> None:
        """Активные задачи помечаются ⬜."""
        svc.add("Активная")
        text = svc.render()
        assert "⬜" in text
        assert "Активная" in text

    def test_render_done_symbol_and_strikethrough(self, svc: PersonalTodoService) -> None:
        """Выполненные задачи — ✅ и ~зачёркивание~."""
        svc.add("Выполненная")
        svc.mark_done(1)
        text = svc.render()
        assert "✅" in text
        assert "~Выполненная~" in text

    def test_render_separator(self, svc: PersonalTodoService) -> None:
        """Разделительная линия присутствует."""
        svc.add("тест")
        assert "─────────────" in svc.render()

    def test_render_only_active_no_done_count(self, svc: PersonalTodoService) -> None:
        """Если нет выполненных — счётчик выполненных не показывается."""
        svc.add("А")
        text = svc.render()
        assert "выполнено" not in text

    def test_render_only_done_no_active_count(self, svc: PersonalTodoService) -> None:
        """Если нет активных — счётчик активных не показывается."""
        svc.add("А")
        svc.mark_done(1)
        text = svc.render()
        assert "активных" not in text

    def test_render_id_in_output(self, svc: PersonalTodoService) -> None:
        """ID задачи присутствует в выводе."""
        svc.add("Задача")
        text = svc.render()
        assert "1." in text


# ─── персистентность ─────────────────────────────────────────────────────────

class TestPersistence:
    def test_data_survives_reinstantiation(self, todo_file: Path) -> None:
        """Данные доступны при создании нового инстанса сервиса."""
        svc1 = PersonalTodoService(todo_file=todo_file)
        svc1.add("Задача 1")
        svc1.add("Задача 2")
        svc1.mark_done(1)

        svc2 = PersonalTodoService(todo_file=todo_file)
        items = svc2.list_all()
        assert len(items) == 2
        done = [t for t in items if t["done"]]
        assert len(done) == 1
        assert done[0]["text"] == "Задача 1"

    def test_id_counter_persists(self, todo_file: Path) -> None:
        """ID монотонно растёт при создании нового инстанса."""
        svc1 = PersonalTodoService(todo_file=todo_file)
        svc1.add("А")
        svc1.add("Б")

        svc2 = PersonalTodoService(todo_file=todo_file)
        item = svc2.add("В")
        assert item["id"] == 3


# ─── граничные случаи ────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_load_nonexistent_file(self, todo_file: Path) -> None:
        """Несуществующий файл → пустой список без ошибок."""
        svc = PersonalTodoService(todo_file=todo_file)
        assert svc.list_all() == []

    def test_load_corrupted_file(self, todo_file: Path) -> None:
        """Повреждённый JSON → пустой список без ошибок."""
        todo_file.write_text("not valid json {{{")
        svc = PersonalTodoService(todo_file=todo_file)
        assert svc.list_all() == []

    def test_load_wrong_type_file(self, todo_file: Path) -> None:
        """Файл содержит объект вместо списка → пустой список."""
        todo_file.write_text('{"key": "value"}')
        svc = PersonalTodoService(todo_file=todo_file)
        assert svc.list_all() == []

    def test_add_creates_parent_dirs(self, tmp_path: Path) -> None:
        """add() создаёт вложенные директории автоматически."""
        nested_file = tmp_path / "a" / "b" / "c" / "todos.json"
        svc = PersonalTodoService(todo_file=nested_file)
        item = svc.add("тест")
        assert item["id"] == 1
        assert nested_file.exists()

    def test_unicode_text(self, svc: PersonalTodoService) -> None:
        """Unicode-текст сохраняется и читается корректно."""
        texts = ["Купить 🥦 и 🥕", "Позвонить маме", "Сделать 报告"]
        for t in texts:
            svc.add(t)
        items = svc.list_all()
        for item, expected in zip(items, texts):
            assert item["text"] == expected

    def test_long_text(self, svc: PersonalTodoService) -> None:
        """Длинный текст сохраняется без усечения."""
        long_text = "A" * 500
        item = svc.add(long_text)
        assert item["text"] == long_text

    def test_add_many_tasks(self, svc: PersonalTodoService) -> None:
        """100 задач добавляются без ошибок."""
        for i in range(100):
            svc.add(f"задача {i}")
        assert len(svc.list_all()) == 100
