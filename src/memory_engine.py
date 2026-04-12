"""
Модуль долговременной памяти Krab на базе ChromaDB.

Зачем нужен:
1) хранит факты пользователя в векторном виде для команд `!remember/!recall`;
2) работает как best-effort подсистема и не должен ронять весь процесс;
3) при сбоях локальной базы автоматически уходит в безопасный degraded-mode.

Связь с проектом:
- используется в `src/handlers/command_handlers.py`;
- инициализируется на старте как singleton `memory_manager`;
- критичен для reliability: ошибка памяти не должна ломать userbot/web runtime.
"""

from __future__ import annotations

import os
import shutil
from typing import Any

from structlog import get_logger

from .config import config

try:
    import chromadb
    from chromadb.utils import embedding_functions
except Exception as exc:  # noqa: BLE001 - dependency может отсутствовать/ломаться при импорте.
    chromadb = None  # type: ignore[assignment]
    embedding_functions = None  # type: ignore[assignment]
    CHROMADB_IMPORT_ERROR: Exception | None = exc
else:
    CHROMADB_IMPORT_ERROR = None

logger = get_logger(__name__)


class MemoryManager:
    """
    Управляет долгосрочной памятью бота используя Vector Database (ChromaDB).
    Позволяет сохранять факты и искать их по смыслу.
    """

    def __init__(self):
        self.persist_directory = os.path.join(config.BASE_DIR, "memory_db")
        self.client = self._init_client()
        self.collection: Any | None = None
        self.embedding_fn: Any | None = None
        self.disabled_reason = ""

        if self.client is None:
            self.disabled_reason = "client_init_failed"
            logger.warning(
                "memory_manager_started_degraded",
                path=self.persist_directory,
                reason=self.disabled_reason,
            )
            return

        try:
            if embedding_functions is None:
                raise RuntimeError("embedding_functions_unavailable")
            self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()
            self.collection = self.client.get_or_create_collection(
                name="krab_facts",
                embedding_function=self.embedding_fn,
            )
            logger.info("memory_manager_initialized", path=self.persist_directory)
        except BaseException as exc:  # noqa: BLE001 - pyo3 panic/driver crash не должны убивать процесс.
            self.disabled_reason = f"collection_init_failed:{exc.__class__.__name__}"
            self.collection = None
            logger.error(
                "memory_manager_collection_init_failed",
                error=str(exc),
                reason=self.disabled_reason,
            )

    def _backup_corrupted_store(self) -> None:
        """Делает ротацию backup каталога перед пересозданием ChromaDB."""
        backup = self.persist_directory + "_backup"
        try:
            if os.path.exists(backup):
                shutil.rmtree(backup)
            if os.path.exists(self.persist_directory):
                shutil.move(self.persist_directory, backup)
                logger.info("chromadb_db_backed_up", backup=backup)
        except OSError as exc:
            logger.warning(
                "chromadb_backup_failed",
                error=str(exc),
                path=self.persist_directory,
                backup=backup,
            )

    def _build_persistent_client(self) -> Any | None:
        """Создаёт persistent-клиент, перехватывая даже panic-исключения движка."""
        if chromadb is None:
            logger.warning(
                "chromadb_unavailable",
                error=str(CHROMADB_IMPORT_ERROR) if CHROMADB_IMPORT_ERROR else "not_installed",
            )
            return None
        try:
            return chromadb.PersistentClient(path=self.persist_directory)
        except BaseException as exc:  # noqa: BLE001 - panic внутри rust-биндингов.
            logger.warning(
                "chromadb_persistent_init_failed",
                error=str(exc),
                path=self.persist_directory,
            )
            return None

    def _build_ephemeral_client(self) -> Any | None:
        """Фолбэк в оперативную память (без диска), если persistent-режим недоступен."""
        if chromadb is None or not hasattr(chromadb, "EphemeralClient"):
            return None
        try:
            logger.warning("chromadb_fallback_ephemeral")
            return chromadb.EphemeralClient()
        except BaseException as exc:  # noqa: BLE001
            logger.error("chromadb_ephemeral_init_failed", error=str(exc))
            return None

    def _init_client(self) -> Any | None:
        """
        Инициализирует ChromaDB без падения процесса.

        Порядок:
        1) persistent client;
        2) backup + повторная инициализация persistent;
        3) ephemeral client;
        4) degraded-mode (None).
        """
        client = self._build_persistent_client()
        if client is not None:
            return client

        self._backup_corrupted_store()
        client = self._build_persistent_client()
        if client is not None:
            logger.info("chromadb_reinitialized_after_backup", path=self.persist_directory)
            return client

        return self._build_ephemeral_client()

    def save_fact(self, text: str, metadata: dict = None) -> bool:
        """Сохраняет факт в память"""
        if self.collection is None:
            logger.warning("save_fact_skipped_memory_disabled", reason=self.disabled_reason)
            return False
        try:
            # Генерируем ID на основе хеша текста или просто рандом
            import uuid

            fact_id = str(uuid.uuid4())

            self.collection.add(
                documents=[text], metadatas=[metadata or {"source": "user"}], ids=[fact_id]
            )
            logger.info("fact_saved", text=text[:50])
            return True
        except (ValueError, RuntimeError, OSError, TypeError) as e:
            logger.error("save_fact_error", error=str(e))
            return False

    def recall(self, query: str, n_results: int = 3) -> str:
        """Поиск релевантных фактов"""
        if self.collection is None:
            logger.warning("recall_skipped_memory_disabled", reason=self.disabled_reason)
            return ""
        try:
            results = self.collection.query(query_texts=[query], n_results=n_results)

            if not results["documents"][0]:
                return ""

            # Форматируем найденные факты
            facts = results["documents"][0]
            return "\n".join([f"- {fact}" for fact in facts])
        except (ValueError, RuntimeError, OSError, KeyError, IndexError, TypeError) as e:
            logger.error("recall_error", error=str(e))
            return ""

    def count(self) -> int:
        if self.collection is None:
            return 0
        try:
            return int(self.collection.count())
        except (ValueError, RuntimeError, OSError, TypeError):
            return 0


# Синглтон
memory_manager = MemoryManager()
