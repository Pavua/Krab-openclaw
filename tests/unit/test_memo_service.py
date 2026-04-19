# -*- coding: utf-8 -*-
"""
Тесты MemoService — быстрые заметки из Telegram в Obsidian.

Покрываем:
1) save() — сохранение, формат файла, frontmatter
2) list_recent() — список заметок, лимит
3) search() — поиск по содержимому
4) save_async() — async-обёртка
5) граничные случаи: пустой текст, нет папки, длинный превью
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from src.core.memo_service import MemoResult, MemoService

# ─── фикстуры ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_inbox(tmp_path: Path) -> Path:
    """Временная папка inbox для тестов."""
    inbox = tmp_path / "00_Inbox"
    inbox.mkdir()
    return inbox


@pytest.fixture
def svc(tmp_inbox: Path) -> MemoService:
    """MemoService с временным inbox."""
    return MemoService(inbox_dir=tmp_inbox)


# ─── save ─────────────────────────────────────────────────────────────────────


class TestSave:
    def test_save_creates_file(self, svc: MemoService, tmp_inbox: Path) -> None:
        """Файл заметки создаётся в inbox."""
        result = svc.save("Купить молоко", chat_title="Личный чат")
        assert result.success
        assert result.file_path is not None
        assert result.file_path.exists()

    def test_save_filename_format(self, svc: MemoService) -> None:
        """Имя файла соответствует шаблону YYYY-MM-DD_HH-MM_memo.md."""
        result = svc.save("тест")
        assert result.file_path is not None
        name = result.file_path.name
        # Формат: 2026-04-12_14-30_memo.md или с секундами
        assert name.endswith("_memo.md")
        assert len(name) >= len("2026-04-12_14-30_memo.md")

    def test_save_frontmatter_created(self, svc: MemoService) -> None:
        """Файл содержит frontmatter с created, source, chat."""
        result = svc.save("Тестовая заметка", chat_title="MyChat")
        content = result.file_path.read_text(encoding="utf-8")
        assert "---" in content
        assert "created:" in content
        assert "source: krab-telegram" in content
        assert "chat: MyChat" in content

    def test_save_body_after_frontmatter(self, svc: MemoService) -> None:
        """Текст заметки размещён после frontmatter."""
        text = "Позвонить врачу завтра"
        result = svc.save(text, chat_title="Тест")
        content = result.file_path.read_text(encoding="utf-8")
        # Находим второй '---' и проверяем, что текст есть после него
        parts = content.split("---\n", 2)
        assert len(parts) == 3
        body = parts[2]
        assert text in body

    def test_save_empty_text_fails(self, svc: MemoService) -> None:
        """Пустой текст возвращает ошибку без создания файла."""
        result = svc.save("   ")
        assert not result.success
        assert result.file_path is None

    def test_save_strips_whitespace(self, svc: MemoService) -> None:
        """Пробелы вокруг текста обрезаются."""
        result = svc.save("  текст с пробелами  ", chat_title="ch")
        content = result.file_path.read_text(encoding="utf-8")
        assert "текст с пробелами" in content
        assert "  текст" not in content

    def test_save_returns_filename_in_message(self, svc: MemoService) -> None:
        """Сообщение об успехе содержит имя файла."""
        result = svc.save("заметка")
        assert "_memo.md" in result.message

    def test_save_default_chat_title(self, svc: MemoService) -> None:
        """При отсутствии chat_title используется 'unknown'."""
        result = svc.save("без чата")
        content = result.file_path.read_text(encoding="utf-8")
        assert "chat: unknown" in content

    def test_save_creates_inbox_if_missing(self, tmp_path: Path) -> None:
        """Папка inbox создаётся автоматически, если её нет."""
        new_inbox = tmp_path / "deep" / "nested" / "00_Inbox"
        svc = MemoService(inbox_dir=new_inbox)
        result = svc.save("тест")
        assert result.success
        assert new_inbox.exists()

    def test_save_duplicate_minute_gets_seconds(self, svc: MemoService, tmp_inbox: Path) -> None:
        """Если файл с именем уже существует — добавляем секунды."""
        # Создаём файл с именем, который будет конфликтовать
        dt = datetime.now()
        base_name = dt.strftime("%Y-%m-%d_%H-%M_memo.md")
        (tmp_inbox / base_name).write_text("existing", encoding="utf-8")

        result = svc.save("новая заметка")
        # Файл создан с другим именем (с секундами)
        assert result.success
        assert result.file_path is not None
        assert result.file_path.name != base_name

    def test_save_created_timestamp_format(self, svc: MemoService) -> None:
        """Поле created имеет формат ISO 8601 без часового пояса."""
        result = svc.save("время")
        content = result.file_path.read_text(encoding="utf-8")
        import re

        match = re.search(r"created: (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", content)
        assert match is not None, f"created не найден в: {content}"


# ─── list_recent ──────────────────────────────────────────────────────────────


class TestListRecent:
    def test_list_empty_inbox(self, svc: MemoService) -> None:
        """Пустой inbox возвращает пустой список."""
        items = svc.list_recent()
        assert items == []

    def test_list_returns_saved_memos(self, svc: MemoService) -> None:
        """Список возвращает сохранённые заметки."""
        svc.save("первая")
        svc.save("вторая")
        items = svc.list_recent()
        assert len(items) == 2

    def test_list_default_limit_5(self, svc: MemoService) -> None:
        """По умолчанию возвращается не более 5 заметок."""
        for i in range(8):
            svc.save(f"заметка {i}")
        items = svc.list_recent()
        assert len(items) == 5

    def test_list_custom_limit(self, svc: MemoService) -> None:
        """Кастомный лимит работает."""
        for i in range(10):
            svc.save(f"z{i}")
        assert len(svc.list_recent(3)) == 3
        assert len(svc.list_recent(10)) == 10

    def test_list_most_recent_first(self, svc: MemoService) -> None:
        """Список отсортирован от новых к старым."""
        svc.save("ранняя")
        import time

        time.sleep(0.01)  # небольшая пауза для различия имён файлов
        svc.save("поздняя")
        items = svc.list_recent()
        # Первый элемент должен быть более поздним по имени
        assert items[0]["filename"] > items[1]["filename"]

    def test_list_item_has_required_keys(self, svc: MemoService) -> None:
        """Элемент списка содержит ключи filename, created, preview."""
        svc.save("тест список")
        items = svc.list_recent()
        assert len(items) == 1
        item = items[0]
        assert "filename" in item
        assert "created" in item
        assert "preview" in item

    def test_list_preview_truncated(self, svc: MemoService) -> None:
        """Длинный текст заметки обрезается в preview до 80 символов."""
        long_text = "А" * 200
        svc.save(long_text)
        items = svc.list_recent()
        assert len(items[0]["preview"]) <= 83  # 80 + "..."

    def test_list_no_inbox(self, tmp_path: Path) -> None:
        """Если inbox не существует, возвращается пустой список."""
        svc = MemoService(inbox_dir=tmp_path / "nonexistent")
        assert svc.list_recent() == []

    def test_list_ignores_non_memo_files(self, svc: MemoService, tmp_inbox: Path) -> None:
        """Файлы без суффикса _memo.md не включаются в список."""
        (tmp_inbox / "note.md").write_text("# обычная заметка", encoding="utf-8")
        (tmp_inbox / "2026-04-12_10-00_memo.md").write_text(
            "---\ncreated: 2026-04-12T10:00:00\nsource: krab-telegram\nchat: test\n---\n\nтекст\n",
            encoding="utf-8",
        )
        items = svc.list_recent()
        assert len(items) == 1
        assert items[0]["filename"] == "2026-04-12_10-00_memo.md"


# ─── search ───────────────────────────────────────────────────────────────────


class TestSearch:
    def test_search_finds_match(self, svc: MemoService) -> None:
        """Поиск находит заметку по ключевому слову."""
        svc.save("Купить молоко и хлеб")
        results = svc.search("молоко")
        assert len(results) == 1

    def test_search_case_insensitive(self, svc: MemoService) -> None:
        """Поиск не чувствителен к регистру."""
        svc.save("Купить Молоко")
        assert len(svc.search("молоко")) == 1
        assert len(svc.search("МОЛОКО")) == 1
        assert len(svc.search("Молоко")) == 1

    def test_search_no_match(self, svc: MemoService) -> None:
        """Поиск без совпадений возвращает пустой список."""
        svc.save("куриный суп")
        assert svc.search("молоко") == []

    def test_search_multiple_files(self, svc: MemoService) -> None:
        """Поиск по нескольким файлам."""
        svc.save("первая заметка с ключом")
        svc.save("вторая заметка с ключом")
        svc.save("третья без")
        results = svc.search("ключом")
        assert len(results) == 2

    def test_search_one_match_per_file(self, svc: MemoService) -> None:
        """Только одно совпадение на файл."""
        svc.save("ключ ключ ключ ключ")
        results = svc.search("ключ")
        assert len(results) == 1

    def test_search_item_has_required_keys(self, svc: MemoService) -> None:
        """Результат поиска содержит ключи filename и match."""
        svc.save("что-то важное")
        results = svc.search("важное")
        assert "filename" in results[0]
        assert "match" in results[0]

    def test_search_match_preview_truncated(self, svc: MemoService) -> None:
        """Длинная строка совпадения обрезается до 100 символов."""
        svc.save("А" * 200 + " ключевое слово " + "Б" * 200)
        results = svc.search("ключевое")
        assert len(results[0]["match"]) <= 103  # 100 + "..."

    def test_search_empty_inbox(self, svc: MemoService) -> None:
        """Поиск в пустом inbox возвращает []."""
        assert svc.search("тест") == []

    def test_search_no_inbox(self, tmp_path: Path) -> None:
        """Если inbox не существует, поиск возвращает []."""
        svc = MemoService(inbox_dir=tmp_path / "nonexistent")
        assert svc.search("тест") == []

    def test_search_finds_in_frontmatter(self, svc: MemoService) -> None:
        """Поиск работает и по frontmatter (например, chat название)."""
        svc.save("текст заметки", chat_title="MySpecialChat")
        results = svc.search("MySpecialChat")
        assert len(results) == 1


# ─── save_async ───────────────────────────────────────────────────────────────


class TestSaveAsync:
    def test_save_async_success(self, svc: MemoService) -> None:
        """Асинхронное сохранение работает корректно."""
        result = asyncio.get_event_loop().run_until_complete(
            svc.save_async("асинхронная заметка", chat_title="тест")
        )
        assert result.success
        assert result.file_path is not None
        assert result.file_path.exists()

    def test_save_async_empty_fails(self, svc: MemoService) -> None:
        """Асинхронное сохранение пустого текста возвращает ошибку."""
        result = asyncio.get_event_loop().run_until_complete(svc.save_async("", chat_title="тест"))
        assert not result.success

    def test_save_async_returns_memo_result(self, svc: MemoService) -> None:
        """Возвращаемый тип — MemoResult."""
        result = asyncio.get_event_loop().run_until_complete(svc.save_async("заметка"))
        assert isinstance(result, MemoResult)


# ─── MemoResult ───────────────────────────────────────────────────────────────


class TestMemoResult:
    def test_memo_result_success_fields(self) -> None:
        """MemoResult с success=True корректно создаётся."""
        r = MemoResult(success=True, message="ок", file_path=Path("/tmp/test.md"))
        assert r.success
        assert r.message == "ок"
        assert r.file_path == Path("/tmp/test.md")

    def test_memo_result_failure_no_path(self) -> None:
        """MemoResult с failure не требует file_path."""
        r = MemoResult(success=False, message="ошибка")
        assert not r.success
        assert r.file_path is None
