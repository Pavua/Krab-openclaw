# -*- coding: utf-8 -*-
"""
Тесты отказоустойчивости `src/memory_engine.py`.

Проверяем, что падение/недоступность ChromaDB не валит процесс,
а память переходит в деградированный режим или в ephemeral-fallback.
"""

from __future__ import annotations

from types import SimpleNamespace

from src import memory_engine


class _DummyCollection:
    def __init__(self) -> None:
        self._docs: list[str] = []

    def add(self, documents, metadatas, ids):
        self._docs.extend(documents)

    def query(self, query_texts, n_results=3):
        return {"documents": [self._docs[:n_results]]}

    def count(self) -> int:
        return len(self._docs)


class _DummyClient:
    def __init__(self, *, raise_on_collection: bool = False) -> None:
        self.raise_on_collection = raise_on_collection

    def get_or_create_collection(self, name, embedding_function):
        if self.raise_on_collection:
            raise RuntimeError("collection boom")
        return _DummyCollection()


def test_memory_manager_fallback_to_ephemeral_when_persistent_crashes(monkeypatch, tmp_path):
    class _BrokenPersistentChroma:
        @staticmethod
        def PersistentClient(path):
            raise RuntimeError("persistent boom")

        @staticmethod
        def EphemeralClient():
            return _DummyClient()

    monkeypatch.setattr(memory_engine.config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(memory_engine, "chromadb", _BrokenPersistentChroma)
    monkeypatch.setattr(
        memory_engine,
        "embedding_functions",
        SimpleNamespace(DefaultEmbeddingFunction=lambda: object()),
    )

    manager = memory_engine.MemoryManager()

    assert manager.client is not None
    assert manager.collection is not None
    assert manager.save_fact("test fact") is True
    assert "test fact" in manager.recall("test")


def test_memory_manager_goes_degraded_when_collection_init_fails(monkeypatch, tmp_path):
    class _CollectionCrashChroma:
        @staticmethod
        def PersistentClient(path):
            return _DummyClient(raise_on_collection=True)

    monkeypatch.setattr(memory_engine.config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(memory_engine, "chromadb", _CollectionCrashChroma)
    monkeypatch.setattr(
        memory_engine,
        "embedding_functions",
        SimpleNamespace(DefaultEmbeddingFunction=lambda: object()),
    )

    manager = memory_engine.MemoryManager()

    assert manager.collection is None
    assert manager.disabled_reason.startswith("collection_init_failed")
    assert manager.save_fact("will not persist") is False
    assert manager.recall("anything") == ""
    assert manager.count() == 0


def test_memory_manager_handles_missing_chromadb_dependency(monkeypatch, tmp_path):
    monkeypatch.setattr(memory_engine.config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(memory_engine, "chromadb", None)
    monkeypatch.setattr(memory_engine, "embedding_functions", None)
    monkeypatch.setattr(memory_engine, "CHROMADB_IMPORT_ERROR", ImportError("missing chromadb"))

    manager = memory_engine.MemoryManager()

    assert manager.client is None
    assert manager.collection is None
    assert manager.save_fact("noop") is False
    assert manager.recall("noop") == ""
    assert manager.count() == 0
