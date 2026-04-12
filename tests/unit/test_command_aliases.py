# -*- coding: utf-8 -*-
"""
Тесты AliasService — пользовательские алиасы команд.

Покрываем:
1) add() — создание, обновление, валидация, зарезервированные имена, лимит
2) remove() — удаление существующего / несуществующего алиаса
3) resolve() — подмена команды в тексте сообщения
4) list_all() / format_list() — листинг
5) persistent storage — сохранение/загрузка из JSON
6) thread safety — concurrent добавление
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from src.core.command_aliases import AliasService, MAX_ALIASES


# ─── фикстуры ────────────────────────────────────────────────────────────────

@pytest.fixture
def svc(tmp_path: Path) -> AliasService:
    """AliasService с временным файлом хранения."""
    storage = tmp_path / "aliases.json"
    return AliasService(storage_path=storage)


@pytest.fixture
def svc_with_aliases(svc: AliasService) -> AliasService:
    """AliasService с несколькими предзаполненными алиасами."""
    svc.add("t", "!translate")
    svc.add("s", "!search")
    svc.add("w", "!web")
    return svc


# ─── add ─────────────────────────────────────────────────────────────────────

class TestAdd:
    def test_add_new_alias(self, svc: AliasService) -> None:
        ok, msg = svc.add("t", "!translate")
        assert ok
        assert "создан" in msg
        assert "!t" in msg

    def test_add_stores_alias(self, svc: AliasService) -> None:
        svc.add("t", "!translate")
        aliases = svc.list_all()
        assert "t" in aliases
        assert aliases["t"] == "translate"

    def test_add_strips_exclamation_from_name(self, svc: AliasService) -> None:
        """Имя '!t' нормализуется в 't'."""
        ok, _ = svc.add("!t", "translate")
        assert ok
        assert "t" in svc.list_all()
        assert "!t" not in svc.list_all()

    def test_add_strips_exclamation_from_command(self, svc: AliasService) -> None:
        """Команда '!translate' нормализуется в 'translate'."""
        svc.add("t", "!translate")
        assert svc.list_all()["t"] == "translate"

    def test_add_update_existing(self, svc: AliasService) -> None:
        svc.add("t", "translate")
        ok, msg = svc.add("t", "search")
        assert ok
        assert "обновлён" in msg
        assert svc.list_all()["t"] == "search"

    def test_add_empty_name_fails(self, svc: AliasService) -> None:
        ok, msg = svc.add("", "translate")
        assert not ok
        assert "пустым" in msg

    def test_add_empty_command_fails(self, svc: AliasService) -> None:
        ok, msg = svc.add("t", "")
        assert not ok

    def test_add_reserved_name_fails(self, svc: AliasService) -> None:
        ok, msg = svc.add("alias", "status")
        assert not ok
        assert "зарезервировано" in msg

    def test_add_reserved_help_fails(self, svc: AliasService) -> None:
        ok, _ = svc.add("help", "status")
        assert not ok

    def test_add_long_name_fails(self, svc: AliasService) -> None:
        ok, msg = svc.add("a" * 33, "translate")
        assert not ok
        assert "длинное" in msg

    def test_add_exactly_32_chars_ok(self, svc: AliasService) -> None:
        ok, _ = svc.add("a" * 32, "translate")
        assert ok

    def test_add_case_insensitive_name(self, svc: AliasService) -> None:
        """Имя сохраняется строчными буквами."""
        svc.add("TRanslate", "search")
        assert "translate" in svc.list_all()

    def test_add_only_exclamation_command_fails(self, svc: AliasService) -> None:
        """Команда из одного '!' — невалидна."""
        ok, _ = svc.add("x", "!")
        assert not ok


# ─── remove ───────────────────────────────────────────────────────────────────

class TestRemove:
    def test_remove_existing(self, svc_with_aliases: AliasService) -> None:
        ok, msg = svc_with_aliases.remove("t")
        assert ok
        assert "удалён" in msg
        assert "t" not in svc_with_aliases.list_all()

    def test_remove_with_exclamation(self, svc_with_aliases: AliasService) -> None:
        """remove('!t') нормализует в 't'."""
        ok, _ = svc_with_aliases.remove("!t")
        assert ok
        assert "t" not in svc_with_aliases.list_all()

    def test_remove_nonexistent(self, svc: AliasService) -> None:
        ok, msg = svc.remove("nonexistent")
        assert not ok
        assert "не найден" in msg

    def test_remove_leaves_others(self, svc_with_aliases: AliasService) -> None:
        svc_with_aliases.remove("t")
        aliases = svc_with_aliases.list_all()
        assert "s" in aliases
        assert "w" in aliases


# ─── resolve ──────────────────────────────────────────────────────────────────

class TestResolve:
    def test_resolve_simple_alias(self, svc: AliasService) -> None:
        svc.add("t", "translate")
        result = svc.resolve("!t")
        assert result == "!translate"

    def test_resolve_with_args(self, svc: AliasService) -> None:
        svc.add("t", "translate")
        result = svc.resolve("!t привет мир")
        assert result == "!translate привет мир"

    def test_resolve_no_match(self, svc: AliasService) -> None:
        """Неизвестная команда остаётся как есть."""
        result = svc.resolve("!status")
        assert result == "!status"

    def test_resolve_not_command(self, svc: AliasService) -> None:
        """Обычный текст не изменяется."""
        svc.add("t", "translate")
        result = svc.resolve("просто текст")
        assert result == "просто текст"

    def test_resolve_empty_text(self, svc: AliasService) -> None:
        result = svc.resolve("")
        assert result == ""

    def test_resolve_slash_prefix(self, svc: AliasService) -> None:
        """Алиасы работают с префиксом /."""
        svc.add("t", "translate")
        result = svc.resolve("/t hello")
        assert result == "/translate hello"

    def test_resolve_dot_prefix(self, svc: AliasService) -> None:
        """Алиасы работают с префиксом ."""
        svc.add("t", "translate")
        result = svc.resolve(".t hello")
        assert result == ".translate hello"

    def test_resolve_preserves_original_if_no_alias(self, svc: AliasService) -> None:
        result = svc.resolve("!search python asyncio")
        assert result == "!search python asyncio"

    def test_resolve_case_insensitive_command(self, svc: AliasService) -> None:
        """!T (заглавная) — тоже разрешается как 't'."""
        svc.add("t", "translate")
        result = svc.resolve("!T привет")
        assert result == "!translate привет"

    def test_resolve_only_prefix_no_word(self, svc: AliasService) -> None:
        """Текст '!' без слова не меняется."""
        result = svc.resolve("!")
        assert result == "!"

    def test_resolve_multiword_target(self, svc: AliasService) -> None:
        """Алиас на составную команду (edge case: команда = 'translate ru')."""
        svc.add("tru", "translate ru")
        # resolve подставляет только первое слово команды
        result = svc.resolve("!tru hello")
        assert result == "!translate ru hello"


# ─── list / format ────────────────────────────────────────────────────────────

class TestListAndFormat:
    def test_list_all_empty(self, svc: AliasService) -> None:
        assert svc.list_all() == {}

    def test_list_all_returns_copy(self, svc_with_aliases: AliasService) -> None:
        copy = svc_with_aliases.list_all()
        copy["injected"] = "hack"
        assert "injected" not in svc_with_aliases.list_all()

    def test_format_list_empty(self, svc: AliasService) -> None:
        msg = svc.format_list()
        assert "нет" in msg.lower()
        assert "!alias set" in msg

    def test_format_list_with_aliases(self, svc_with_aliases: AliasService) -> None:
        msg = svc_with_aliases.format_list()
        assert "!t" in msg
        assert "translate" in msg
        assert "Алиасы" in msg

    def test_format_list_shows_count(self, svc_with_aliases: AliasService) -> None:
        msg = svc_with_aliases.format_list()
        assert "3/" in msg  # 3 алиаса из MAX_ALIASES


# ─── persistent storage ───────────────────────────────────────────────────────

class TestPersistence:
    def test_save_and_reload(self, tmp_path: Path) -> None:
        storage = tmp_path / "aliases.json"
        svc1 = AliasService(storage_path=storage)
        svc1.add("t", "translate")
        svc1.add("s", "search")

        # Создаём новый экземпляр — должен загрузить из файла
        svc2 = AliasService(storage_path=storage)
        aliases = svc2.list_all()
        assert aliases.get("t") == "translate"
        assert aliases.get("s") == "search"

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        storage = tmp_path / "nested" / "dir" / "aliases.json"
        svc = AliasService(storage_path=storage)
        svc.add("t", "translate")
        assert storage.exists()

    def test_save_valid_json(self, tmp_path: Path) -> None:
        storage = tmp_path / "aliases.json"
        svc = AliasService(storage_path=storage)
        svc.add("t", "translate")
        data = json.loads(storage.read_text())
        assert isinstance(data, dict)
        assert data["t"] == "translate"

    def test_load_ignores_corrupt_file(self, tmp_path: Path) -> None:
        storage = tmp_path / "aliases.json"
        storage.write_text("not json {{{", encoding="utf-8")
        # Не должен упасть — пустой словарь
        svc = AliasService(storage_path=storage)
        assert svc.list_all() == {}

    def test_remove_persists(self, tmp_path: Path) -> None:
        storage = tmp_path / "aliases.json"
        svc1 = AliasService(storage_path=storage)
        svc1.add("t", "translate")
        svc1.remove("t")

        svc2 = AliasService(storage_path=storage)
        assert "t" not in svc2.list_all()

    def test_load_ignores_non_dict(self, tmp_path: Path) -> None:
        """JSON-массив в файле — не словарь, игнорируем."""
        storage = tmp_path / "aliases.json"
        storage.write_text('["hello"]', encoding="utf-8")
        svc = AliasService(storage_path=storage)
        assert svc.list_all() == {}


# ─── limit ────────────────────────────────────────────────────────────────────

class TestLimit:
    def test_limit_enforced(self, tmp_path: Path) -> None:
        storage = tmp_path / "aliases.json"
        svc = AliasService(storage_path=storage)
        # Заполняем до лимита
        for i in range(MAX_ALIASES):
            ok, _ = svc.add(f"cmd{i}", f"command{i}")
            assert ok, f"Не смогли добавить алиас {i}"

        # Следующий — должен отказать
        ok, msg = svc.add("overflow", "extra")
        assert not ok
        assert "лимит" in msg.lower()

    def test_update_existing_bypasses_limit(self, tmp_path: Path) -> None:
        """Обновление существующего алиаса не блокируется лимитом."""
        storage = tmp_path / "aliases.json"
        svc = AliasService(storage_path=storage)
        for i in range(MAX_ALIASES):
            svc.add(f"cmd{i}", f"command{i}")

        # Обновляем уже существующий — должно пройти
        ok, _ = svc.add("cmd0", "newcommand")
        assert ok


# ─── thread safety ────────────────────────────────────────────────────────────

class TestThreadSafety:
    def test_concurrent_adds(self, svc: AliasService) -> None:
        errors: list[Exception] = []

        def add_alias(i: int) -> None:
            try:
                svc.add(f"cmd{i}", f"command{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_alias, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # Все 20 алиасов должны быть добавлены
        assert len(svc.list_all()) == 20
