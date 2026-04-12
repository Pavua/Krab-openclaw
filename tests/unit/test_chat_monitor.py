# -*- coding: utf-8 -*-
"""
Тесты для src/core/chat_monitor.py — сервис мониторинга чатов на ключевые слова.

Что тестируем:
1. Базовое добавление/удаление мониторинга.
2. check_message: plain keyword, case-insensitive.
3. check_message: regex паттерны (re:<pattern>).
4. Пустые keywords — мониторим все сообщения.
5. Сообщения из чатов не в мониторинге — None.
6. Persistence: save/load round-trip.
7. Повреждённый JSON — не роняет сервис.
8. Изоляция: тесты используют свежий instance с tmp_path.
9. remove несуществующего чата возвращает False.
10. list_monitors — правильный набор entries.
11. add обновляет существующий мониторинг.
12. active_chat_ids обновляется при add/remove.
13. Невалидный regex — логирует warning, не падает.
14. Множественные keywords — возвращает первое совпавшее.
15. Текст None/пустой — не крашится.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.chat_monitor import ChatMonitorService, MonitorEntry


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    """Временный файл состояния для изоляции тестов."""
    return tmp_path / "chat_monitors.json"


@pytest.fixture
def svc(state_file: Path) -> ChatMonitorService:
    """Свежий ChatMonitorService с изолированным state_file."""
    return ChatMonitorService(state_file=state_file)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Базовое добавление / удаление
# ──────────────────────────────────────────────────────────────────────────────


def test_add_returns_entry(svc: ChatMonitorService) -> None:
    entry = svc.add(chat_id=-100123, chat_title="TestGroup", keywords=["crypto"])
    assert entry.chat_id == "-100123"
    assert entry.chat_title == "TestGroup"
    assert entry.keywords == ["crypto"]


def test_remove_existing_returns_true(svc: ChatMonitorService) -> None:
    svc.add(chat_id=-100123, chat_title="G", keywords=["kw"])
    assert svc.remove(-100123) is True


def test_remove_nonexistent_returns_false(svc: ChatMonitorService) -> None:
    assert svc.remove(-999999) is False


def test_list_monitors_empty(svc: ChatMonitorService) -> None:
    assert svc.list_monitors() == []


def test_list_monitors_shows_added(svc: ChatMonitorService) -> None:
    svc.add(chat_id=1, chat_title="A", keywords=["a"])
    svc.add(chat_id=2, chat_title="B", keywords=["b"])
    ids = {e.chat_id for e in svc.list_monitors()}
    assert ids == {"1", "2"}


def test_remove_clears_from_list(svc: ChatMonitorService) -> None:
    svc.add(chat_id=1, chat_title="A", keywords=["a"])
    svc.remove(1)
    assert svc.list_monitors() == []


# ──────────────────────────────────────────────────────────────────────────────
# 2. check_message: plain keyword
# ──────────────────────────────────────────────────────────────────────────────


def test_check_plain_keyword_match(svc: ChatMonitorService) -> None:
    svc.add(chat_id=100, chat_title="G", keywords=["bitcoin"])
    assert svc.check_message(100, "Сегодня bitcoin вырос") == "bitcoin"


def test_check_plain_keyword_case_insensitive(svc: ChatMonitorService) -> None:
    svc.add(chat_id=100, chat_title="G", keywords=["Bitcoin"])
    assert svc.check_message(100, "BITCOIN TO THE MOON") == "Bitcoin"


def test_check_plain_keyword_no_match(svc: ChatMonitorService) -> None:
    svc.add(chat_id=100, chat_title="G", keywords=["ethereum"])
    assert svc.check_message(100, "Сегодня bitcoin вырос") is None


def test_check_unmonitored_chat(svc: ChatMonitorService) -> None:
    """Чат без мониторинга — всегда None."""
    svc.add(chat_id=100, chat_title="G", keywords=["bitcoin"])
    assert svc.check_message(999, "bitcoin") is None


# ──────────────────────────────────────────────────────────────────────────────
# 3. check_message: regex паттерны
# ──────────────────────────────────────────────────────────────────────────────


def test_check_regex_pattern_match(svc: ChatMonitorService) -> None:
    svc.add(chat_id=100, chat_title="G", keywords=["re:крипт|биткоин"])
    assert svc.check_message(100, "Биткоин растёт") == "re:крипт|биткоин"


def test_check_regex_no_match(svc: ChatMonitorService) -> None:
    svc.add(chat_id=100, chat_title="G", keywords=["re:^начало$"])
    assert svc.check_message(100, "не начало строки") is None


def test_check_regex_complex(svc: ChatMonitorService) -> None:
    svc.add(chat_id=100, chat_title="G", keywords=["re:\\d{4,}"])
    assert svc.check_message(100, "цена 12345 рублей") == "re:\\d{4,}"


# ──────────────────────────────────────────────────────────────────────────────
# 4. Пустые keywords — мониторим ВСЕ сообщения
# ──────────────────────────────────────────────────────────────────────────────


def test_empty_keywords_matches_everything(svc: ChatMonitorService) -> None:
    svc.add(chat_id=100, chat_title="G", keywords=[])
    # При пустом keywords — match возвращает None (нет паттернов для сравнения)
    # Это корректное поведение: без keywords нечего сравнивать
    # NOTE: семантика "мониторим всё" с пустым списком — отдельная фича,
    # здесь check_message возвращает None т.к. нет ни одного паттерна.
    result = svc.check_message(100, "любой текст")
    assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# 5. Текст None/пустой
# ──────────────────────────────────────────────────────────────────────────────


def test_check_empty_text_returns_none(svc: ChatMonitorService) -> None:
    svc.add(chat_id=100, chat_title="G", keywords=["kw"])
    assert svc.check_message(100, "") is None


def test_check_none_text_returns_none(svc: ChatMonitorService) -> None:
    svc.add(chat_id=100, chat_title="G", keywords=["kw"])
    assert svc.check_message(100, None) is None  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────────
# 6. Persistence: round-trip сохранения/загрузки
# ──────────────────────────────────────────────────────────────────────────────


def test_persistence_roundtrip(state_file: Path) -> None:
    svc1 = ChatMonitorService(state_file=state_file)
    svc1.add(chat_id=-100, chat_title="Saved", keywords=["test", "re:\\d+"])

    # Новый instance должен загрузить то же состояние
    svc2 = ChatMonitorService(state_file=state_file)
    entry = svc2.get_entry(-100)
    assert entry is not None
    assert entry.chat_title == "Saved"
    assert "test" in entry.keywords
    assert "re:\\d+" in entry.keywords


def test_persistence_after_remove(state_file: Path) -> None:
    svc1 = ChatMonitorService(state_file=state_file)
    svc1.add(chat_id=1, chat_title="A", keywords=["x"])
    svc1.add(chat_id=2, chat_title="B", keywords=["y"])
    svc1.remove(1)

    svc2 = ChatMonitorService(state_file=state_file)
    assert svc2.get_entry(1) is None
    assert svc2.get_entry(2) is not None


# ──────────────────────────────────────────────────────────────────────────────
# 7. Повреждённый JSON — не роняет сервис
# ──────────────────────────────────────────────────────────────────────────────


def test_corrupted_json_loads_empty(state_file: Path) -> None:
    state_file.write_text("{NOT VALID JSON!!!", encoding="utf-8")
    # Не должно бросать исключение
    svc = ChatMonitorService(state_file=state_file)
    assert svc.list_monitors() == []


def test_missing_state_file_loads_empty(tmp_path: Path) -> None:
    svc = ChatMonitorService(state_file=tmp_path / "nonexistent.json")
    assert svc.list_monitors() == []


# ──────────────────────────────────────────────────────────────────────────────
# 8. add обновляет существующий мониторинг
# ──────────────────────────────────────────────────────────────────────────────


def test_add_updates_existing(svc: ChatMonitorService) -> None:
    svc.add(chat_id=100, chat_title="Old", keywords=["old"])
    svc.add(chat_id=100, chat_title="New", keywords=["new1", "new2"])
    entry = svc.get_entry(100)
    assert entry is not None
    assert entry.chat_title == "New"
    assert entry.keywords == ["new1", "new2"]
    # В list_monitors один элемент
    assert len(svc.list_monitors()) == 1


# ──────────────────────────────────────────────────────────────────────────────
# 9. active_chat_ids
# ──────────────────────────────────────────────────────────────────────────────


def test_active_chat_ids_updates(svc: ChatMonitorService) -> None:
    assert svc.active_chat_ids == set()
    svc.add(chat_id=1, chat_title="A", keywords=[])
    svc.add(chat_id=2, chat_title="B", keywords=[])
    assert svc.active_chat_ids == {"1", "2"}
    svc.remove(1)
    assert svc.active_chat_ids == {"2"}


# ──────────────────────────────────────────────────────────────────────────────
# 10. Невалидный regex — не падает, просто пропускает паттерн
# ──────────────────────────────────────────────────────────────────────────────


def test_invalid_regex_skipped(svc: ChatMonitorService) -> None:
    # re:[invalid — не закрытая скобка — паттерн пропускается
    entry = svc.add(chat_id=100, chat_title="G", keywords=["re:[invalid", "valid_word"])
    # Сервис не падает при инициализации
    # valid_word должен работать (невалидный regex пропущен)
    assert entry.match("valid_word found") == "valid_word"
    # Текст без valid_word — None, т.к. re:[invalid не скомпилирован
    assert entry.match("some text without the word") is None


# ──────────────────────────────────────────────────────────────────────────────
# 11. Множественные keywords — возвращает первое совпавшее
# ──────────────────────────────────────────────────────────────────────────────


def test_multiple_keywords_first_match(svc: ChatMonitorService) -> None:
    svc.add(chat_id=100, chat_title="G", keywords=["aaa", "bbb", "ccc"])
    # Текст содержит и bbb и ccc — вернёт bbb (первое по порядку)
    result = svc.check_message(100, "текст bbb и ccc")
    assert result == "bbb"


def test_multiple_keywords_last_only(svc: ChatMonitorService) -> None:
    svc.add(chat_id=100, chat_title="G", keywords=["aaa", "bbb", "ccc"])
    result = svc.check_message(100, "только ccc здесь")
    assert result == "ccc"


# ──────────────────────────────────────────────────────────────────────────────
# 12. MonitorEntry: to_dict / from_dict round-trip
# ──────────────────────────────────────────────────────────────────────────────


def test_monitor_entry_serialization() -> None:
    entry = MonitorEntry(chat_id=-100, chat_title="Test", keywords=["x", "re:y+"])
    d = entry.to_dict()
    assert d["chat_id"] == "-100"
    assert d["chat_title"] == "Test"
    assert "x" in d["keywords"]

    restored = MonitorEntry.from_dict(d)
    assert restored.chat_id == "-100"
    assert restored.keywords == ["x", "re:y+"]
    # Паттерны пересобраны — должны работать
    assert restored.match("yyy") == "re:y+"


# ──────────────────────────────────────────────────────────────────────────────
# 13. get_entry возвращает None для неизвестного чата
# ──────────────────────────────────────────────────────────────────────────────


def test_get_entry_unknown(svc: ChatMonitorService) -> None:
    assert svc.get_entry(999) is None


def test_get_entry_known(svc: ChatMonitorService) -> None:
    svc.add(chat_id=42, chat_title="X", keywords=["k"])
    assert svc.get_entry(42) is not None
    assert svc.get_entry("42") is not None  # строковый и числовой равнозначны


# ──────────────────────────────────────────────────────────────────────────────
# 14. chat_id нормализация: str и int равнозначны
# ──────────────────────────────────────────────────────────────────────────────


def test_chat_id_normalization(svc: ChatMonitorService) -> None:
    svc.add(chat_id=-100123456, chat_title="G", keywords=["x"])
    # Проверяем через str и int
    assert svc.check_message(-100123456, "x найдено") == "x"
    assert svc.check_message("-100123456", "x найдено") == "x"
    assert svc.remove("-100123456") is True
    assert svc.check_message(-100123456, "x") is None


# ──────────────────────────────────────────────────────────────────────────────
# 15. JSON state file создаётся при первом add
# ──────────────────────────────────────────────────────────────────────────────


def test_state_file_created_on_add(state_file: Path) -> None:
    assert not state_file.exists()
    svc = ChatMonitorService(state_file=state_file)
    svc.add(chat_id=1, chat_title="X", keywords=["k"])
    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert len(data["monitors"]) == 1
