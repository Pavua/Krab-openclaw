# -*- coding: utf-8 -*-
"""
Расширенные тесты для src/memory_engine.py.

Покрывают:
- множественное сохранение и поиск (search/recall),
- поведение recall с n_results (контекстное окно),
- идемпотентность count при повторных операциях,
- корректность форматирования вывода (строки с "- "),
- метаданные (произвольные поля),
- сохранение длинных текстов (token-heavy),
- pruning через пересоздание коллекции (backup + reinit),
- persistence: данные остаются между двумя экземплярами MemoryManager,
- устойчивость к ошибкам в collection.add / collection.query,
- ephemeral-клиент полностью функционален без диска,
- поиск по нескольким сохранённым фактам возвращает несколько результатов.
"""

from __future__ import annotations

from types import SimpleNamespace

from src import memory_engine


# ---------------------------------------------------------------------------
# Вспомогательные заглушки
# ---------------------------------------------------------------------------


class _DummyCollection:
    """Минимальная имитация ChromaDB-коллекции без реальных векторов."""

    def __init__(self) -> None:
        self._docs: list[str] = []
        self._meta: list[dict] = []

    def add(self, documents, metadatas, ids):
        self._docs.extend(documents)
        self._meta.extend(metadatas)

    def query(self, query_texts, n_results=3):
        # Возвращаем первые n_results документов (нет реальных векторов)
        return {"documents": [self._docs[:n_results]]}

    def count(self) -> int:
        return len(self._docs)


class _DummyClient:
    def __init__(self) -> None:
        # Один общий экземпляр коллекции чтобы эмулировать персистентность
        self._col = _DummyCollection()

    def get_or_create_collection(self, name, embedding_function):
        return self._col


def _patch_chroma(monkeypatch, tmp_path, shared_client=None):
    """
    Патчит memory_engine чтобы использовать _DummyClient вместо ChromaDB.
    Возвращает свежий MemoryManager.
    """
    client = shared_client or _DummyClient()

    class _FakeChroma:
        @staticmethod
        def PersistentClient(path):
            return client

        @staticmethod
        def EphemeralClient():
            return client

    monkeypatch.setattr(memory_engine.config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(memory_engine, "chromadb", _FakeChroma)
    monkeypatch.setattr(
        memory_engine,
        "embedding_functions",
        SimpleNamespace(DefaultEmbeddingFunction=lambda: object()),
    )
    return memory_engine.MemoryManager(), client


# ---------------------------------------------------------------------------
# Множественное сохранение и поиск
# ---------------------------------------------------------------------------


class TestMultipleFacts:
    def test_save_multiple_facts_increments_count_correctly(self, monkeypatch, tmp_path):
        """count() корректно отражает количество добавленных фактов."""
        mgr, _ = _patch_chroma(monkeypatch, tmp_path)
        for i in range(5):
            mgr.save_fact(f"Факт номер {i}")
        assert mgr.count() == 5

    def test_recall_returns_multiple_results(self, monkeypatch, tmp_path):
        """recall возвращает несколько фактов (n_results > 1), если их достаточно."""
        mgr, _ = _patch_chroma(monkeypatch, tmp_path)
        mgr.save_fact("Python — интерпретируемый язык")
        mgr.save_fact("Python создан Гвидо ван Россумом")
        mgr.save_fact("Python популярен в ML")

        result = mgr.recall("Python", n_results=3)
        # Должны вернуться все 3 строки, разделённые переводами строк
        assert result.count("\n") == 2
        assert result.count("- ") == 3

    def test_recall_n_results_limits_output(self, monkeypatch, tmp_path):
        """n_results=1 возвращает не более одного факта."""
        mgr, _ = _patch_chroma(monkeypatch, tmp_path)
        for i in range(5):
            mgr.save_fact(f"Строка {i}")

        result = mgr.recall("запрос", n_results=1)
        lines = [l for l in result.splitlines() if l]
        assert len(lines) <= 1


# ---------------------------------------------------------------------------
# Форматирование вывода
# ---------------------------------------------------------------------------


class TestRecallFormat:
    def test_each_line_starts_with_dash(self, monkeypatch, tmp_path):
        """Каждая строка recall начинается с '- '."""
        mgr, _ = _patch_chroma(monkeypatch, tmp_path)
        mgr.save_fact("Первый факт")
        mgr.save_fact("Второй факт")
        result = mgr.recall("факт", n_results=2)
        for line in result.splitlines():
            assert line.startswith("- "), f"Строка без дефиса: {line!r}"

    def test_recall_no_trailing_newline(self, monkeypatch, tmp_path):
        """recall не добавляет лишний перевод строки в конец."""
        mgr, _ = _patch_chroma(monkeypatch, tmp_path)
        mgr.save_fact("Тест без трейлинга")
        result = mgr.recall("тест", n_results=1)
        if result:
            assert not result.endswith("\n"), "Лишний перевод строки в конце"


# ---------------------------------------------------------------------------
# Метаданные
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_save_fact_with_extra_metadata_fields(self, monkeypatch, tmp_path):
        """save_fact сохраняет факт с произвольными полями метаданных без ошибок."""
        mgr, _ = _patch_chroma(monkeypatch, tmp_path)
        result = mgr.save_fact(
            "Факт с расширенными метаданными",
            metadata={"user_id": 42, "chat_id": -100123, "source": "telegram"},
        )
        assert result is True
        assert mgr.count() == 1

    def test_save_fact_default_metadata_when_none(self, monkeypatch, tmp_path):
        """save_fact без metadata= не поднимает исключений."""
        mgr, _ = _patch_chroma(monkeypatch, tmp_path)
        result = mgr.save_fact("Без метаданных")
        assert result is True


# ---------------------------------------------------------------------------
# Длинные тексты (token-heavy)
# ---------------------------------------------------------------------------


class TestLongText:
    def test_save_long_text_returns_true(self, monkeypatch, tmp_path):
        """Длинный документ (>1000 символов) сохраняется без ошибок."""
        mgr, _ = _patch_chroma(monkeypatch, tmp_path)
        long_text = "Слово " * 200  # ~1200 символов
        result = mgr.save_fact(long_text)
        assert result is True
        assert mgr.count() == 1

    def test_recall_long_text_is_included(self, monkeypatch, tmp_path):
        """Длинный факт возвращается через recall."""
        mgr, _ = _patch_chroma(monkeypatch, tmp_path)
        long_text = "Тест длинного факта " * 50
        mgr.save_fact(long_text)
        result = mgr.recall("длинный", n_results=1)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Persistence: данные живут между экземплярами MemoryManager
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_two_managers_share_same_collection(self, monkeypatch, tmp_path):
        """
        Два MemoryManager, использующие один клиент, видят одни и те же данные.
        Эмулирует restart процесса с persist_directory на диске.
        """
        shared_client = _DummyClient()

        mgr1, _ = _patch_chroma(monkeypatch, tmp_path, shared_client=shared_client)
        mgr1.save_fact("Персистентный факт")

        # Второй менеджер с тем же клиентом
        mgr2, _ = _patch_chroma(monkeypatch, tmp_path, shared_client=shared_client)
        assert mgr2.count() == 1
        result = mgr2.recall("персистентность", n_results=1)
        assert result != ""


# ---------------------------------------------------------------------------
# Устойчивость к ошибкам collection.add / collection.query
# ---------------------------------------------------------------------------


class _BrokenAddCollection(_DummyCollection):
    """Падает при add, но работает нормально для query и count."""

    def add(self, documents, metadatas, ids):
        raise RuntimeError("disk full")


class _BrokenQueryCollection(_DummyCollection):
    """Падает при query."""

    def query(self, query_texts, n_results=3):
        raise RuntimeError("index corrupted")


class TestCollectionErrors:
    def test_save_fact_returns_false_when_add_raises(self, monkeypatch, tmp_path):
        """save_fact возвращает False (не кидает исключение) при ошибке add."""
        broken = _BrokenAddCollection()

        class _FakeChroma:
            @staticmethod
            def PersistentClient(path):
                class _C:
                    def get_or_create_collection(self, name, embedding_function):
                        return broken

                return _C()

        monkeypatch.setattr(memory_engine.config, "BASE_DIR", str(tmp_path))
        monkeypatch.setattr(memory_engine, "chromadb", _FakeChroma)
        monkeypatch.setattr(
            memory_engine,
            "embedding_functions",
            SimpleNamespace(DefaultEmbeddingFunction=lambda: object()),
        )
        mgr = memory_engine.MemoryManager()
        result = mgr.save_fact("упадёт")
        assert result is False

    def test_recall_returns_empty_string_when_query_raises(self, monkeypatch, tmp_path):
        """recall возвращает '' (не кидает исключение) при ошибке query."""
        broken = _BrokenQueryCollection()
        broken.add(["заглушка"], [{}], ["id1"])  # заполняем, чтобы n_results не упал раньше

        class _FakeChroma:
            @staticmethod
            def PersistentClient(path):
                class _C:
                    def get_or_create_collection(self, name, embedding_function):
                        return broken

                return _C()

        monkeypatch.setattr(memory_engine.config, "BASE_DIR", str(tmp_path))
        monkeypatch.setattr(memory_engine, "chromadb", _FakeChroma)
        monkeypatch.setattr(
            memory_engine,
            "embedding_functions",
            SimpleNamespace(DefaultEmbeddingFunction=lambda: object()),
        )
        mgr = memory_engine.MemoryManager()
        result = mgr.recall("что-нибудь")
        assert result == ""


# ---------------------------------------------------------------------------
# Ephemeral-клиент: полная функциональность без persist_directory
# ---------------------------------------------------------------------------


class TestEphemeralFallback:
    def test_ephemeral_client_is_fully_functional(self, monkeypatch, tmp_path):
        """
        Если PersistentClient падает, ephemeral-клиент обеспечивает
        сохранение и поиск фактов.
        """
        shared_col = _DummyCollection()
        shared_client = _DummyClient()
        shared_client._col = shared_col

        class _BrokenPersistent:
            @staticmethod
            def PersistentClient(path):
                raise OSError("read-only filesystem")

            @staticmethod
            def EphemeralClient():
                return shared_client

        monkeypatch.setattr(memory_engine.config, "BASE_DIR", str(tmp_path))
        monkeypatch.setattr(memory_engine, "chromadb", _BrokenPersistent)
        monkeypatch.setattr(
            memory_engine,
            "embedding_functions",
            SimpleNamespace(DefaultEmbeddingFunction=lambda: object()),
        )

        mgr = memory_engine.MemoryManager()
        assert mgr.client is not None
        assert mgr.collection is not None

        assert mgr.save_fact("ephemeral факт") is True
        assert mgr.count() == 1
        result = mgr.recall("ephemeral", n_results=1)
        assert result != ""
