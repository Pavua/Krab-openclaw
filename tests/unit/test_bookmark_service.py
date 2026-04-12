# -*- coding: utf-8 -*-
"""
Тесты BookmarkService — закладки на Telegram-сообщения.

Покрываем:
1) add() — добавление, дедупликация
2) list_all() — список от новых к старым
3) search() — поиск по полям
4) delete() — удаление по ID, несуществующий ID
5) get() — поиск по ID
6) async-обёртки
7) Персистентность: загрузка из JSON, повреждённый файл
8) Граничные случаи: пустой текст, длинный превью, несколько закладок
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from src.core.bookmark_service import _PREVIEW_LEN, BookmarkService

# ─── Фикстуры ─────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """Путь к временному файлу хранения закладок."""
    return tmp_path / "bookmarks.json"


@pytest.fixture
def svc(store: Path) -> BookmarkService:
    """BookmarkService с временным файлом хранения."""
    return BookmarkService(store_path=store)


def _make_bm(svc: BookmarkService, **overrides) -> dict:
    """Вспомогательная функция: создаёт закладку с дефолтными значениями."""
    defaults = dict(
        chat_id=100,
        chat_title="Тестовый чат",
        message_id=42,
        text="Важное сообщение",
        from_user="@user",
    )
    defaults.update(overrides)
    return svc.add(**defaults)


# ─── add ──────────────────────────────────────────────────────────────────────


class TestAdd:
    def test_add_returns_bookmark(self, svc: BookmarkService) -> None:
        """add() возвращает словарь с нужными полями."""
        bm = _make_bm(svc)
        assert bm["id"] == 1
        assert bm["chat_id"] == 100
        assert bm["chat_title"] == "Тестовый чат"
        assert bm["message_id"] == 42
        assert bm["from_user"] == "@user"
        assert bm["text_preview"] == "Важное сообщение"
        assert "timestamp" in bm

    def test_add_increments_id(self, svc: BookmarkService) -> None:
        """Каждая новая закладка получает следующий ID."""
        bm1 = _make_bm(svc, message_id=1)
        bm2 = _make_bm(svc, message_id=2)
        bm3 = _make_bm(svc, message_id=3)
        assert bm1["id"] == 1
        assert bm2["id"] == 2
        assert bm3["id"] == 3

    def test_add_deduplicates_same_chat_and_message(self, svc: BookmarkService) -> None:
        """Повторное добавление того же сообщения возвращает существующую закладку."""
        bm1 = _make_bm(svc, chat_id=5, message_id=99)
        bm2 = _make_bm(svc, chat_id=5, message_id=99)
        assert bm1["id"] == bm2["id"]
        assert len(svc.list_all()) == 1

    def test_add_different_chat_same_message_not_duplicate(self, svc: BookmarkService) -> None:
        """То же message_id в другом чате — не дубликат."""
        _make_bm(svc, chat_id=1, message_id=10)
        _make_bm(svc, chat_id=2, message_id=10)
        assert len(svc.list_all()) == 2

    def test_add_truncates_long_preview(self, svc: BookmarkService) -> None:
        """Длинный текст обрезается до _PREVIEW_LEN символов с многоточием."""
        long_text = "А" * (_PREVIEW_LEN + 50)
        bm = _make_bm(svc, text=long_text)
        assert len(bm["text_preview"]) <= _PREVIEW_LEN
        assert bm["text_preview"].endswith("…")

    def test_add_short_preview_unchanged(self, svc: BookmarkService) -> None:
        """Короткий текст сохраняется без изменений."""
        short_text = "Кратко"
        bm = _make_bm(svc, text=short_text)
        assert bm["text_preview"] == short_text

    def test_add_persists_to_disk(self, svc: BookmarkService, store: Path) -> None:
        """После add() файл создаётся на диске."""
        _make_bm(svc)
        assert store.exists()
        data = json.loads(store.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 1


# ─── list_all ─────────────────────────────────────────────────────────────────


class TestListAll:
    def test_list_all_empty(self, svc: BookmarkService) -> None:
        """Пустой список при отсутствии закладок."""
        assert svc.list_all() == []

    def test_list_all_returns_all(self, svc: BookmarkService) -> None:
        """list_all() возвращает все закладки."""
        _make_bm(svc, message_id=1)
        _make_bm(svc, message_id=2)
        _make_bm(svc, message_id=3)
        assert len(svc.list_all()) == 3

    def test_list_all_newest_first(self, svc: BookmarkService) -> None:
        """Закладки отсортированы от новых к старым."""
        bm1 = _make_bm(svc, message_id=1)
        time.sleep(0.01)
        bm2 = _make_bm(svc, message_id=2)
        items = svc.list_all()
        # Более новая идёт первой
        assert items[0]["id"] == bm2["id"]
        assert items[1]["id"] == bm1["id"]


# ─── search ───────────────────────────────────────────────────────────────────


class TestSearch:
    def test_search_by_text_preview(self, svc: BookmarkService) -> None:
        """Поиск по тексту превью."""
        _make_bm(svc, text="Встреча завтра в 10:00", message_id=1)
        _make_bm(svc, text="Купить кофе", message_id=2)
        results = svc.search("встреча")
        assert len(results) == 1
        assert "Встреча" in results[0]["text_preview"]

    def test_search_by_chat_title(self, svc: BookmarkService) -> None:
        """Поиск по названию чата."""
        _make_bm(svc, chat_title="Рабочий чат", message_id=1)
        _make_bm(svc, chat_title="Личный", message_id=2)
        results = svc.search("рабоч")
        assert len(results) == 1
        assert results[0]["chat_title"] == "Рабочий чат"

    def test_search_by_from_user(self, svc: BookmarkService) -> None:
        """Поиск по имени отправителя."""
        _make_bm(svc, from_user="@boss_ivan", message_id=1)
        _make_bm(svc, from_user="@friend", message_id=2)
        results = svc.search("boss")
        assert len(results) == 1
        assert results[0]["from_user"] == "@boss_ivan"

    def test_search_case_insensitive(self, svc: BookmarkService) -> None:
        """Поиск нечувствителен к регистру."""
        _make_bm(svc, text="Важный дедлайн", message_id=1)
        assert len(svc.search("ВАЖНЫЙ")) == 1
        assert len(svc.search("дедлайн")) == 1

    def test_search_no_results(self, svc: BookmarkService) -> None:
        """Пустой список при отсутствии совпадений."""
        _make_bm(svc, text="Что-то одно", message_id=1)
        assert svc.search("несуществующее") == []

    def test_search_returns_newest_first(self, svc: BookmarkService) -> None:
        """Результаты поиска отсортированы от новых к старым."""
        _make_bm(svc, text="совпадение", message_id=1)
        time.sleep(0.01)
        _make_bm(svc, text="совпадение второе", message_id=2)
        results = svc.search("совпадение")
        assert results[0]["message_id"] == 2


# ─── delete ───────────────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_existing(self, svc: BookmarkService) -> None:
        """delete() возвращает True и удаляет закладку."""
        bm = _make_bm(svc)
        ok = svc.delete(bm["id"])
        assert ok is True
        assert svc.list_all() == []

    def test_delete_nonexistent(self, svc: BookmarkService) -> None:
        """delete() возвращает False для несуществующего ID."""
        ok = svc.delete(9999)
        assert ok is False

    def test_delete_persists(self, svc: BookmarkService, store: Path) -> None:
        """После удаления изменения записываются на диск."""
        bm = _make_bm(svc)
        svc.delete(bm["id"])
        data = json.loads(store.read_text(encoding="utf-8"))
        assert data == []

    def test_delete_one_of_many(self, svc: BookmarkService) -> None:
        """Удаление одной из нескольких закладок не затрагивает остальные."""
        bm1 = _make_bm(svc, message_id=1)
        bm2 = _make_bm(svc, message_id=2)
        bm3 = _make_bm(svc, message_id=3)
        svc.delete(bm2["id"])
        remaining_ids = {b["id"] for b in svc.list_all()}
        assert bm1["id"] in remaining_ids
        assert bm2["id"] not in remaining_ids
        assert bm3["id"] in remaining_ids


# ─── get ──────────────────────────────────────────────────────────────────────


class TestGet:
    def test_get_existing(self, svc: BookmarkService) -> None:
        """get() возвращает закладку по ID."""
        bm = _make_bm(svc)
        found = svc.get(bm["id"])
        assert found is not None
        assert found["id"] == bm["id"]

    def test_get_nonexistent(self, svc: BookmarkService) -> None:
        """get() возвращает None для несуществующего ID."""
        assert svc.get(9999) is None


# ─── Персистентность ──────────────────────────────────────────────────────────


class TestPersistence:
    def test_reload_from_disk(self, store: Path) -> None:
        """Закладки загружаются из файла при создании нового экземпляра."""
        svc1 = BookmarkService(store_path=store)
        _make_bm(svc1, message_id=10)

        # Новый экземпляр — должен прочитать файл
        svc2 = BookmarkService(store_path=store)
        items = svc2.list_all()
        assert len(items) == 1
        assert items[0]["message_id"] == 10

    def test_corrupted_file_resets(self, store: Path) -> None:
        """Повреждённый JSON-файл сбрасывается к пустому списку."""
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("НЕ ВАЛИДНЫЙ JSON{{{{", encoding="utf-8")
        svc = BookmarkService(store_path=store)
        assert svc.list_all() == []

    def test_nonlist_json_resets(self, store: Path) -> None:
        """Файл с не-списком сбрасывается к пустому списку."""
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text('{"key": "value"}', encoding="utf-8")
        svc = BookmarkService(store_path=store)
        assert svc.list_all() == []

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """Отсутствие файла — list_all() возвращает пустой список."""
        svc = BookmarkService(store_path=tmp_path / "nonexistent.json")
        assert svc.list_all() == []


# ─── Async-обёртки ────────────────────────────────────────────────────────────


class TestAsync:
    def test_add_async(self, svc: BookmarkService) -> None:
        """add_async() добавляет закладку асинхронно."""
        bm = asyncio.run(
            svc.add_async(
                chat_id=1,
                chat_title="Чат",
                message_id=1,
                text="Тест async",
                from_user="@x",
            )
        )
        assert bm["id"] == 1
        assert bm["text_preview"] == "Тест async"

    def test_delete_async(self, svc: BookmarkService) -> None:
        """delete_async() удаляет закладку асинхронно."""
        bm = _make_bm(svc)
        ok = asyncio.run(svc.delete_async(bm["id"]))
        assert ok is True
        assert svc.list_all() == []

    def test_delete_async_nonexistent(self, svc: BookmarkService) -> None:
        """delete_async() возвращает False для несуществующего ID."""
        ok = asyncio.run(svc.delete_async(42))
        assert ok is False


# ─── Граничные случаи ─────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_text_with_only_whitespace_stored_stripped(self, svc: BookmarkService) -> None:
        """Текст с пробелами сохраняется в stripped-виде."""
        bm = _make_bm(svc, text="   Текст   ")
        assert bm["text_preview"] == "Текст"

    def test_add_many_bookmarks_ids_unique(self, svc: BookmarkService) -> None:
        """ID закладок уникальны при массовом добавлении."""
        bms = [_make_bm(svc, message_id=i) for i in range(1, 21)]
        ids = [b["id"] for b in bms]
        assert len(set(ids)) == 20

    def test_preview_exactly_at_limit(self, svc: BookmarkService) -> None:
        """Текст ровно в _PREVIEW_LEN символов не обрезается."""
        exact_text = "Б" * _PREVIEW_LEN
        bm = _make_bm(svc, text=exact_text)
        assert bm["text_preview"] == exact_text

    def test_chat_title_special_chars(self, svc: BookmarkService) -> None:
        """Название чата со спецсимволами сохраняется корректно."""
        bm = _make_bm(svc, chat_title='Чат "Тест" & <проект>')
        assert bm["chat_title"] == 'Чат "Тест" & <проект>'
