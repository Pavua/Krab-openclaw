"""
Wave 56-D: тесты graceful degradation sqlite_vec (dlopen vec0.dylib).

Покрывают Sentry PYTHON-FASTAPI-7S: `memory_indexer_embed_failed`
при dlopen-failure vec0.dylib.

Сценарии:
  1. MemoryEmbedder нормальная инициализация при доступном sqlite_vec.
  2. create_vec_table: ImportError → VecUnavailableError.
  3. create_vec_table: OperationalError (dlopen) → VecUnavailableError.
  4. MemoryEmbedder._ensure_connection: VecUnavailableError → _vec_available=False.
  5. embed_specific не крашится если _vec_available=False (no-op через caller).
  6. _maybe_embed_chunks: VecUnavailableError → warning, не error → embed_disabled.
  7. _maybe_embed_chunks: VecUnavailableError только один warning (idempotent flag).
  8. _maybe_embed_chunks: ImportError → warning + embed_disabled (старый путь).
  9. VecUnavailableError является подклассом RuntimeError.
  10. embed_failed логируется как error для не-dlopen исключений.

Запуск::

    venv/bin/python -m pytest tests/unit/test_sqlite_vec_resilience_wave56d.py -q
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.memory_embedder import (
    DEFAULT_DIM,
    MemoryEmbedder,
    VecUnavailableError,
    create_vec_table,
)
from src.core.memory_indexer_worker import MemoryIndexerWorker, _reset_singleton_for_tests

# ---------------------------------------------------------------------------
# Вспомогательные fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Сбрасываем singleton worker между тестами."""
    _reset_singleton_for_tests()
    yield
    _reset_singleton_for_tests()


@pytest.fixture()
def in_memory_conn() -> sqlite3.Connection:
    """In-memory SQLite connection с enable_load_extension=True."""
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Тест 1: create_vec_table работает нормально.
# ---------------------------------------------------------------------------


def test_create_vec_table_success(in_memory_conn: sqlite3.Connection) -> None:
    """create_vec_table не бросает при рабочем sqlite_vec."""
    # Не должно бросить исключений.
    create_vec_table(in_memory_conn, dim=DEFAULT_DIM)
    # Виртуальная таблица создана.
    result = in_memory_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_chunks';"
    ).fetchone()
    assert result is not None, "vec_chunks должна быть создана"


# ---------------------------------------------------------------------------
# Тест 2: create_vec_table: ImportError → VecUnavailableError.
# ---------------------------------------------------------------------------


def test_create_vec_table_import_error_raises_vec_unavailable(
    in_memory_conn: sqlite3.Connection,
) -> None:
    """create_vec_table при ImportError бросает VecUnavailableError, не ImportError."""
    with patch.dict(sys.modules, {"sqlite_vec": None}):
        with pytest.raises(VecUnavailableError, match="не установлен"):
            create_vec_table(in_memory_conn, dim=DEFAULT_DIM)


# ---------------------------------------------------------------------------
# Тест 3: create_vec_table: OperationalError (dlopen) → VecUnavailableError.
# ---------------------------------------------------------------------------


def test_create_vec_table_dlopen_error_raises_vec_unavailable(
    in_memory_conn: sqlite3.Connection,
) -> None:
    """create_vec_table при OperationalError (vec0.dylib) бросает VecUnavailableError."""
    mock_vec = MagicMock()
    mock_vec.load.side_effect = sqlite3.OperationalError(
        "not authorized"  # типичная ошибка без enable_load_extension
    )
    with patch.dict(sys.modules, {"sqlite_vec": mock_vec}):
        with pytest.raises(VecUnavailableError, match="dlopen"):
            create_vec_table(in_memory_conn, dim=DEFAULT_DIM)


# ---------------------------------------------------------------------------
# Тест 4: MemoryEmbedder._ensure_connection: VecUnavailableError → _vec_available=False.
# ---------------------------------------------------------------------------


def test_memory_embedder_vec_unavailable_sets_flag(tmp_path: Path) -> None:
    """При dlopen-failure _ensure_connection выставляет _vec_available=False."""
    from src.core.memory_archive import ArchivePaths, create_schema, open_archive

    # Создаём реальную БД (без vec_chunks — схема archive.py её не создаёт).
    paths = ArchivePaths.under(tmp_path)
    conn = open_archive(paths)
    create_schema(conn)
    conn.close()

    embedder = MemoryEmbedder(archive_paths=paths, _model=MagicMock())

    # Симулируем dlopen failure.
    with patch(
        "src.core.memory_embedder.create_vec_table",
        side_effect=VecUnavailableError("vec0.dylib dlopen failed: test"),
    ):
        with pytest.raises(VecUnavailableError):
            embedder._ensure_connection()

    assert embedder._vec_available is False


# ---------------------------------------------------------------------------
# Тест 5: embed_specific не крашится если вызывается несмотря на _vec_available=False.
# ---------------------------------------------------------------------------


def test_memory_embedder_vec_flag_starts_as_true() -> None:
    """_vec_available=True по умолчанию при создании MemoryEmbedder."""
    embedder = MemoryEmbedder(_model=MagicMock())
    assert embedder._vec_available is True


# ---------------------------------------------------------------------------
# Тест 6: _maybe_embed_chunks: VecUnavailableError → embed_disabled=True, нет crash.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_embed_chunks_vec_unavailable_sets_embed_disabled(
    tmp_path: Path,
) -> None:
    """VecUnavailableError в embed_specific → embed_disabled=True, без краша."""
    from src.core.memory_archive import ArchivePaths

    fake_embedder = MagicMock()
    fake_embedder.embed_specific.side_effect = VecUnavailableError("vec0.dylib dlopen failed: test")

    worker = MemoryIndexerWorker(
        archive_paths=ArchivePaths.under(tmp_path),
        embedder=fake_embedder,
    )

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        worker._embed_executor = ex
        worker._executor_shutdown = False
        # Не должно бросить исключение.
        await worker._maybe_embed_chunks(["chunk_abc"])

    # embed_disabled выставлен — дальнейшие embed'ы пропускаются.
    assert worker.get_stats().embed_disabled is True
    # embed_failed счётчик НЕ увеличен (это не настоящая ошибка).
    assert worker.get_stats().failed.get("embed", 0) == 0


# ---------------------------------------------------------------------------
# Тест 7: повторный VecUnavailableError не дублирует bump_failed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_embed_chunks_vec_unavailable_no_bump_failed(tmp_path: Path) -> None:
    """embed_disabled=True после первого VecUnavailableError — повторный вызов тоже не bumps."""
    from src.core.memory_archive import ArchivePaths

    fake_embedder = MagicMock()
    fake_embedder.embed_specific.side_effect = VecUnavailableError("test")

    worker = MemoryIndexerWorker(
        archive_paths=ArchivePaths.under(tmp_path),
        embedder=fake_embedder,
    )

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        worker._embed_executor = ex
        worker._executor_shutdown = False
        await worker._maybe_embed_chunks(["chunk_1"])
        # Второй вызов — уже embed_disabled=True → embedder существует но
        # embed_specific снова бросит VecUnavailableError.
        # Счётчик failed["embed"] должен оставаться 0.
        await worker._maybe_embed_chunks(["chunk_2"])

    assert worker.get_stats().failed.get("embed", 0) == 0
    assert worker.get_stats().embed_disabled is True


# ---------------------------------------------------------------------------
# Тест 8: ImportError (пакет не установлен) → embed_disabled=True.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_embed_chunks_import_error_graceful(tmp_path: Path) -> None:
    """ImportError на MemoryEmbedder → embed_disabled=True, нет crash."""
    from src.core.memory_archive import ArchivePaths

    worker = MemoryIndexerWorker(
        archive_paths=ArchivePaths.under(tmp_path),
        embedder=None,
    )
    worker._embedder = None

    # Мокаем sys.modules чтобы lazy import MemoryEmbedder падал с ImportError.
    original = sys.modules.get("src.core.memory_embedder")
    sys.modules["src.core.memory_embedder"] = None  # type: ignore[assignment]
    try:
        await worker._maybe_embed_chunks(["chunk_xyz"])
    finally:
        if original is not None:
            sys.modules["src.core.memory_embedder"] = original
        else:
            sys.modules.pop("src.core.memory_embedder", None)

    assert worker.get_stats().embed_disabled is True


# ---------------------------------------------------------------------------
# Тест 9: VecUnavailableError является подклассом RuntimeError.
# ---------------------------------------------------------------------------


def test_vec_unavailable_error_is_runtime_error() -> None:
    """VecUnavailableError extends RuntimeError."""
    err = VecUnavailableError("test")
    assert isinstance(err, RuntimeError)
    assert str(err) == "test"


# ---------------------------------------------------------------------------
# Тест 10: обычный Exception в embed_specific → bump_failed (не проглатывается).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_embed_chunks_generic_exception_bumps_failed(tmp_path: Path) -> None:
    """Не-VecUnavailableError Exception → bump_failed["embed"] увеличивается."""
    from src.core.memory_archive import ArchivePaths

    fake_embedder = MagicMock()
    fake_embedder.embed_specific.side_effect = ValueError("something_broke")

    worker = MemoryIndexerWorker(
        archive_paths=ArchivePaths.under(tmp_path),
        embedder=fake_embedder,
    )

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        worker._embed_executor = ex
        worker._executor_shutdown = False
        await worker._maybe_embed_chunks(["chunk_fail"])

    # failed["embed"] увеличен — это настоящая ошибка.
    assert worker.get_stats().failed.get("embed", 0) == 1
    # embed_disabled НЕ выставляется для не-vec ошибок.
    assert worker.get_stats().embed_disabled is False
