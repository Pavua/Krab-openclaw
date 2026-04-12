"""
Тесты для src/memory_engine.py.

Проверяют:
- инициализацию MemoryManager в нормальном режиме;
- save_fact / recall / count в рабочем состоянии;
- degraded-mode при отсутствии ChromaDB;
- edge cases: пустая БД, метаданные, специальные символы.

Каждый тест получает свой tmp_path, поэтому изоляция полная.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Хелпер: создаёт MemoryManager с persist_directory = tmp_path
# ---------------------------------------------------------------------------


def _make_manager(tmp_path, monkeypatch):
    """Возвращает свежий MemoryManager, хранящий базу в tmp_path."""
    import src.memory_engine as me

    monkeypatch.setattr(me.config, "BASE_DIR", str(tmp_path))
    manager = me.MemoryManager()
    return manager


# ---------------------------------------------------------------------------
# Инициализация
# ---------------------------------------------------------------------------


class TestMemoryManagerInit:
    def test_normal_init_sets_collection(self, tmp_path, monkeypatch):
        """При успешной инициализации collection != None."""
        mgr = _make_manager(tmp_path, monkeypatch)
        assert mgr.collection is not None

    def test_persist_directory_under_base_dir(self, tmp_path, monkeypatch):
        """persist_directory находится внутри BASE_DIR."""
        mgr = _make_manager(tmp_path, monkeypatch)
        assert mgr.persist_directory.startswith(str(tmp_path))

    def test_disabled_reason_empty_on_success(self, tmp_path, monkeypatch):
        """disabled_reason пустой, если инициализация прошла успешно."""
        mgr = _make_manager(tmp_path, monkeypatch)
        assert mgr.disabled_reason == ""

    def test_degraded_mode_when_chromadb_none(self, tmp_path, monkeypatch):
        """При chromadb=None менеджер переходит в degraded-mode."""
        import src.memory_engine as me

        monkeypatch.setattr(me.config, "BASE_DIR", str(tmp_path))
        monkeypatch.setattr(me, "chromadb", None)
        monkeypatch.setattr(me, "embedding_functions", None)

        mgr = me.MemoryManager()
        assert mgr.collection is None
        assert mgr.client is None


# ---------------------------------------------------------------------------
# save_fact
# ---------------------------------------------------------------------------


class TestSaveFact:
    def test_save_returns_true(self, tmp_path, monkeypatch):
        """save_fact возвращает True при успешном сохранении."""
        mgr = _make_manager(tmp_path, monkeypatch)
        result = mgr.save_fact("Пользователь любит Python")
        assert result is True

    def test_save_increments_count(self, tmp_path, monkeypatch):
        """После save_fact счётчик коллекции увеличивается."""
        mgr = _make_manager(tmp_path, monkeypatch)
        before = mgr.count()
        mgr.save_fact("Факт номер один")
        assert mgr.count() == before + 1

    def test_save_with_custom_metadata(self, tmp_path, monkeypatch):
        """save_fact принимает пользовательские метаданные без ошибок."""
        mgr = _make_manager(tmp_path, monkeypatch)
        result = mgr.save_fact("Факт с тегом", metadata={"tag": "test", "priority": "high"})
        assert result is True

    def test_save_returns_false_when_disabled(self, tmp_path, monkeypatch):
        """В degraded-mode save_fact возвращает False без исключений."""
        import src.memory_engine as me

        monkeypatch.setattr(me.config, "BASE_DIR", str(tmp_path))
        monkeypatch.setattr(me, "chromadb", None)
        monkeypatch.setattr(me, "embedding_functions", None)

        mgr = me.MemoryManager()
        result = mgr.save_fact("Любой текст")
        assert result is False

    def test_save_special_chars(self, tmp_path, monkeypatch):
        """save_fact корректно обрабатывает Unicode и спецсимволы."""
        mgr = _make_manager(tmp_path, monkeypatch)
        result = mgr.save_fact("Факт: 日本語テスト 🦀 <>&\"'")
        assert result is True


# ---------------------------------------------------------------------------
# recall
# ---------------------------------------------------------------------------


class TestRecall:
    def test_recall_empty_db_returns_empty_string(self, tmp_path, monkeypatch):
        """recall на пустой коллекции возвращает пустую строку."""
        mgr = _make_manager(tmp_path, monkeypatch)
        result = mgr.recall("что угодно")
        assert result == ""

    def test_recall_finds_saved_fact(self, tmp_path, monkeypatch):
        """Сохранённый факт находится через recall."""
        mgr = _make_manager(tmp_path, monkeypatch)
        mgr.save_fact("Краб любит море и рыбу")
        result = mgr.recall("морепродукты")
        # Факт должен попасть в результат (семантический поиск)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_recall_returns_dash_prefixed_lines(self, tmp_path, monkeypatch):
        """Каждый факт в ответе начинается с '- '."""
        mgr = _make_manager(tmp_path, monkeypatch)
        mgr.save_fact("Тестовый факт для форматирования")
        result = mgr.recall("тест")
        if result:
            for line in result.splitlines():
                assert line.startswith("- "), f"Ожидался префикс '- ', получено: {line!r}"

    def test_recall_disabled_returns_empty_string(self, tmp_path, monkeypatch):
        """В degraded-mode recall возвращает пустую строку без исключений."""
        import src.memory_engine as me

        monkeypatch.setattr(me.config, "BASE_DIR", str(tmp_path))
        monkeypatch.setattr(me, "chromadb", None)
        monkeypatch.setattr(me, "embedding_functions", None)

        mgr = me.MemoryManager()
        result = mgr.recall("любой запрос")
        assert result == ""


# ---------------------------------------------------------------------------
# count
# ---------------------------------------------------------------------------


class TestCount:
    def test_count_zero_on_empty(self, tmp_path, monkeypatch):
        """count() == 0 на пустой коллекции."""
        mgr = _make_manager(tmp_path, monkeypatch)
        assert mgr.count() == 0

    def test_count_disabled_returns_zero(self, tmp_path, monkeypatch):
        """count() == 0 в degraded-mode."""
        import src.memory_engine as me

        monkeypatch.setattr(me.config, "BASE_DIR", str(tmp_path))
        monkeypatch.setattr(me, "chromadb", None)
        monkeypatch.setattr(me, "embedding_functions", None)

        mgr = me.MemoryManager()
        assert mgr.count() == 0
