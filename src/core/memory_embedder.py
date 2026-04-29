"""
Model2Vec embedder воркер для Memory Layer (Phase 2).

Назначение
==========
Берёт chunks из ``archive.db`` (таблица ``chunks``), прогоняет их через
Model2Vec StaticModel, и пишет полученные 256-мерные векторы в ``vec_chunks``
— виртуальную таблицу sqlite-vec с distance_metric=cosine.

После отработки этого воркера активируется векторный путь
``HybridRetriever._vector_search()``: FTS5 + vector + RRF реально работает.

Ключевые свойства
-----------------
* **Идемпотентно**: повторный запуск не дублирует векторы. Определение
  "неиндексированных" chunks через ``LEFT JOIN vec_chunks ON rowid``.
* **Batch-обработка**: ``batch_size=512`` по умолчанию; Model2Vec возвращает
  numpy-массив, который сериализуется в float32 bytes одним tobytes().
* **Lazy-init модели**: Model2Vec скачивается/загружается только при первом
  вызове embed_*; импорт модуля безопасен и на машине без HuggingFace-кеша.
* **Тестируемо**: конструктор принимает ``_model`` для инъекции fake-модели
  в unit-тестах — чтобы не тянуть настоящий Model2Vec на каждый прогон.
* **rowid alignment**: ``vec_chunks.rowid == chunks.id`` (id — alias для rowid
  в INTEGER PRIMARY KEY). Это позволяет JOIN'ить две таблицы без посредников.

Этот модуль — вторая половина Phase 2 (первая половина — skeleton в
``memory_retrieval.py``).
"""

from __future__ import annotations

import sqlite3
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from structlog import get_logger

from src.core.memory_archive import ArchivePaths, open_archive

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Константы.
# ---------------------------------------------------------------------------

#: Размерность Model2Vec multilingual (M2V_multilingual_output): 256.
DEFAULT_DIM = 256

#: Канонический идентификатор мультиязычной Model2Vec-модели.
DEFAULT_MODEL_NAME = "minishlab/M2V_multilingual_output"

#: Размер batch'а для encode + INSERT. 512 — компромисс между памятью и IO.
DEFAULT_BATCH_SIZE = 512


# ---------------------------------------------------------------------------
# DDL helper.
# ---------------------------------------------------------------------------


def create_vec_table(conn: sqlite3.Connection, dim: int = DEFAULT_DIM) -> None:
    """
    Создать виртуальную таблицу ``vec_chunks`` sqlite-vec, если её ещё нет.

    Грузит расширение ``sqlite_vec`` (требует ``enable_load_extension(True)``).
    Таблица хранит float32-векторы фиксированной размерности ``dim`` с
    cosine distance. ``rowid`` таблицы совпадает с ``chunks.id`` — это позволяет
    JOIN'ить ``vec_chunks`` и ``chunks`` без дополнительных ключей.

    Функция идемпотентна: ``IF NOT EXISTS`` на CREATE VIRTUAL TABLE.
    """
    import sqlite_vec  # type: ignore[import-not-found]

    # enable_load_extension по умолчанию False на многих сборках; включаем
    # ровно на время загрузки и возвращаем обратно, чтобы не открывать
    # attack-surface для произвольных SQL-запросов.
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)

    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks "
        f"USING vec0(vector float[{dim}] distance_metric=cosine);"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Serialization helper.
# ---------------------------------------------------------------------------


def serialize_f32(vec: Any) -> bytes:
    """
    Сериализовать вектор в сырые float32 bytes (little-endian).

    Поддерживает:
      * numpy.ndarray — через ``.astype('float32').tobytes()`` (быстрый путь).
      * list/tuple любых чисел — через ``struct.pack('<{N}f', *vec)``.

    Формат совместим с ``vec0`` sqlite-vec.
    """
    # numpy-массив — быстрый путь. Принудительно float32, чтобы размер был
    # ровно 4*N байт (vec0 ожидает именно float32).
    tobytes = getattr(vec, "tobytes", None)
    astype = getattr(vec, "astype", None)
    if tobytes is not None and astype is not None:
        return astype("float32").tobytes()
    # Generic fallback: list / tuple / generator.
    values = list(vec)
    return struct.pack(f"<{len(values)}f", *values)


# ---------------------------------------------------------------------------
# Stats.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmbedStats:
    """Результат запуска embedder'а: сколько chunks прошло и сколько времени."""

    #: Сколько chunks успешно проэмбеддили на этом запуске.
    chunks_processed: int
    #: Сколько chunks уже было в vec_chunks (skip при идемпотентном запуске).
    chunks_skipped: int
    #: Сколько batch'ей прогнали через модель.
    batches: int
    #: Общее время выполнения embed-фазы (без загрузки модели).
    duration_sec: float
    #: Время загрузки Model2Vec (или 0.0 если модель была инжектирована).
    model_load_sec: float


# ---------------------------------------------------------------------------
# MemoryEmbedder.
# ---------------------------------------------------------------------------


class MemoryEmbedder:
    """
    Воркер, эмбеддящий chunks в vec_chunks.

    Args:
        archive_paths: пути к БД/директории. None → ``ArchivePaths.default()``.
        model_name: HuggingFace id модели Model2Vec. По умолчанию —
            multilingual 256-dim модель.
        batch_size: размер batch'а для encode + executemany INSERT.
        dim: размерность векторного пространства. Должна совпадать с моделью
            (256 для M2V_multilingual_output).
        _model: опциональная инъекция модели (для тестов). Если передана —
            загрузка по ``model_name`` пропускается.
    """

    def __init__(
        self,
        archive_paths: ArchivePaths | None = None,
        model_name: str = DEFAULT_MODEL_NAME,
        batch_size: int = DEFAULT_BATCH_SIZE,
        dim: int = DEFAULT_DIM,
        _model: Any | None = None,
    ) -> None:
        self._paths = archive_paths or ArchivePaths.default()
        self._model_name = model_name
        self._batch_size = max(1, int(batch_size))
        self._dim = int(dim)

        # Thread-local connection cache: SQLite connection объекты нельзя
        # разделять между потоками (sqlite3.Connection created in a thread
        # can only be used in that same thread). Каждый поток — свой
        # connection, открывается лениво при первом _ensure_connection().
        # Список открытых connections (для close()) защищаем отдельным
        # lock'ом, т.к. tracking может идти из любого потока.
        self._tls: threading.local = threading.local()
        self._conns_lock: threading.Lock = threading.Lock()
        self._all_conns: list[sqlite3.Connection] = []
        # Writes в vec_chunks идут INSERT/DELETE из разных потоков
        # (asyncio.to_thread создаёт новый worker для каждого вызова).
        # SQLite одиночной БД: сериализуем write'ы через lock, чтобы
        # исключить "database is locked" при конкурентных embed'ах.
        self._write_lock: threading.Lock = threading.Lock()
        # Если модель инжектирована (тесты) — загрузка пропускается.
        self._model: Any | None = _model
        # Фиксируем флаг, чтобы model_load_sec в тестах был 0.
        self._model_injected: bool = _model is not None

    # ------------------------------------------------------------------
    # Публичный API.
    # ------------------------------------------------------------------

    def embed_all_unindexed(self) -> EmbedStats:
        """
        Проэмбеддить все chunks без вектора в ``vec_chunks``.

        Использует LEFT JOIN vec_chunks ON rowid = chunks.id — возвращает
        только chunks, для которых ещё нет записи в векторной таблице.
        Идемпотентно: повторный вызов возвращает stats(processed=0).
        """
        conn = self._ensure_connection()
        model_load_sec = self._ensure_model_loaded()

        rows = conn.execute(
            """
            SELECT c.id, c.chunk_id, c.text_redacted
            FROM chunks AS c
            LEFT JOIN vec_chunks AS v ON v.rowid = c.id
            WHERE v.rowid IS NULL
            ORDER BY c.id;
            """,
        ).fetchall()

        # Общее число chunks (для подсчёта skipped).
        total = conn.execute("SELECT COUNT(*) FROM chunks;").fetchone()[0]
        chunks_skipped = total - len(rows)

        stats = self._process_rows(rows, model_load_sec=model_load_sec)
        # C7: фиксируем metadata только если что-то реально проиндексировано.
        # Идемпотентный no-op не перезаписывает indexed_at timestamp.
        if stats.chunks_processed > 0:
            self._write_vec_meta(conn)
        return EmbedStats(
            chunks_processed=stats.chunks_processed,
            chunks_skipped=chunks_skipped,
            batches=stats.batches,
            duration_sec=stats.duration_sec,
            model_load_sec=model_load_sec,
        )

    def embed_specific(self, chunk_ids: Iterable[str]) -> EmbedStats:
        """
        Re-index конкретных chunks (для invalidation в Phase 4).

        Для каждого ``chunk_id`` DELETE'ит старую запись из ``vec_chunks``
        (если была) и вставляет новый вектор. Безопасно вызывать с chunk_ids,
        которых нет в БД — они будут тихо пропущены.
        """
        conn = self._ensure_connection()
        model_load_sec = self._ensure_model_loaded()

        ids = list(chunk_ids)
        if not ids:
            return EmbedStats(
                chunks_processed=0,
                chunks_skipped=0,
                batches=0,
                duration_sec=0.0,
                model_load_sec=model_load_sec,
            )

        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"""
            SELECT id, chunk_id, text_redacted
            FROM chunks
            WHERE chunk_id IN ({placeholders})
            ORDER BY id;
            """,
            ids,
        ).fetchall()

        # Удаляем старые векторы для этих rowid'ов (если были).
        row_ids = [r[0] for r in rows]
        if row_ids:
            del_placeholders = ",".join("?" * len(row_ids))
            with self._write_lock:
                conn.execute(
                    f"DELETE FROM vec_chunks WHERE rowid IN ({del_placeholders});",
                    row_ids,
                )
                conn.commit()

        chunks_skipped = len(ids) - len(rows)
        stats = self._process_rows(rows, model_load_sec=model_load_sec)
        # C7: обновляем metadata если хотя бы один chunk был re-indexed.
        if stats.chunks_processed > 0:
            self._write_vec_meta(conn)
        return EmbedStats(
            chunks_processed=stats.chunks_processed,
            chunks_skipped=chunks_skipped,
            batches=stats.batches,
            duration_sec=stats.duration_sec,
            model_load_sec=model_load_sec,
        )

    def rebuild_all(self) -> EmbedStats:
        """
        Полностью пересоздать ``vec_chunks`` и переиндексировать все chunks.

        Для dev/одноразовых миграций: DROP → CREATE → embed_all_unindexed.
        В production используй ``embed_all_unindexed()`` — оно идемпотентно.
        """
        conn = self._ensure_connection()

        # DROP и CREATE виртуальной таблицы.
        conn.execute("DROP TABLE IF EXISTS vec_chunks;")
        conn.commit()
        create_vec_table(conn, dim=self._dim)

        return self.embed_all_unindexed()

    def close(self) -> None:
        """Закрыть connections (все потоки). Безопасно вызывать повторно."""
        with self._conns_lock:
            conns = list(self._all_conns)
            self._all_conns.clear()
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        # Сбросим TLS-кеш текущего потока, чтобы следующий вызов
        # _ensure_connection() открыл fresh connection.
        if hasattr(self._tls, "conn"):
            try:
                delattr(self._tls, "conn")
            except AttributeError:
                pass

    # ------------------------------------------------------------------
    # Внутренние помощники.
    # ------------------------------------------------------------------

    @property
    def _conn(self) -> sqlite3.Connection | None:
        """
        Backcompat: старый код/тесты читали ``embedder._conn`` напрямую.

        Возвращает connection текущего потока (или ``None``, если в этом
        потоке ещё не вызывали ``_ensure_connection()``). Set-атрибута
        больше нет — если нужно инвалидировать connection, зовите ``close()``.
        """
        return getattr(self._tls, "conn", None)

    def _ensure_connection(self) -> sqlite3.Connection:
        """
        Открыть БД в текущем потоке и гарантировать наличие vec_chunks.

        Thread-local: каждый поток получает свой собственный connection,
        потому что SQLite запрещает делить connection между потоками
        ("SQLite objects created in a thread can only be used in that
        same thread"). asyncio.to_thread каждый раз может стартовать
        новый worker-thread, поэтому lazy-init на уровне TLS.
        """
        conn = getattr(self._tls, "conn", None)
        if conn is not None:
            return conn
        # open_archive создаст файл если его нет; для embedder'а это ок —
        # он всегда работает с уже существующей схемой (создаётся bootstrap'ом
        # или create_schema).
        conn = open_archive(self._paths, read_only=False)
        # vec-таблица создаётся отдельно — схема archive.py её не трогает.
        create_vec_table(conn, dim=self._dim)
        self._tls.conn = conn
        with self._conns_lock:
            self._all_conns.append(conn)
        return conn

    def _write_vec_meta(self, conn: sqlite3.Connection) -> None:
        """
        C7: записывает метаданные текущей embedding-модели в vec_chunks_meta.

        Таблица создаётся в create_schema() (memory_archive.py). Если её
        нет (legacy-БД, bootstrap не прогонялся) — пытаемся создать и
        повторить INSERT; любая ошибка проглатывается (retrieval-layer
        graceful fallback в FTS-only режим).
        """
        rows = [
            ("model_name", str(self._model_name)),
            ("model_dim", str(self._dim)),
            ("indexed_at", datetime.now(timezone.utc).isoformat()),
        ]
        try:
            with self._write_lock:
                conn.executemany(
                    "INSERT OR REPLACE INTO vec_chunks_meta(key, value) VALUES (?, ?);",
                    rows,
                )
                conn.commit()
        except sqlite3.OperationalError as exc:
            # Таблица ещё не создана — пробуем создать и повторить.
            try:
                with self._write_lock:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS vec_chunks_meta (
                            key   TEXT PRIMARY KEY,
                            value TEXT NOT NULL
                        ) WITHOUT ROWID;
                        """
                    )
                    conn.executemany(
                        "INSERT OR REPLACE INTO vec_chunks_meta(key, value) VALUES (?, ?);",
                        rows,
                    )
                    conn.commit()
            except sqlite3.Error as exc2:  # noqa: BLE001
                logger.warning(
                    "embedder_vec_meta_write_failed",
                    error=str(exc),
                    error_retry=str(exc2),
                )

    def _ensure_model_loaded(self) -> float:
        """
        Late-init модели. Возвращает время загрузки в секундах.

        Если модель была инжектирована в __init__ (через ``_model=``) —
        возвращает 0.0 без попыток загрузки.
        """
        if self._model is not None:
            return 0.0
        t0 = time.perf_counter()
        try:
            from model2vec import StaticModel  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.error("embedder_model2vec_import_failed", error=str(exc))
            raise
        try:
            self._model = StaticModel.from_pretrained(self._model_name)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "embedder_model_load_failed",
                name=self._model_name,
                error=str(exc),
            )
            raise
        elapsed = time.perf_counter() - t0
        logger.info(
            "embedder_model_loaded",
            name=self._model_name,
            sec=round(elapsed, 3),
        )
        return elapsed

    def _process_rows(
        self,
        rows: list[tuple[int, str, str]],
        model_load_sec: float,
    ) -> EmbedStats:
        """Прогнать список (id, chunk_id, text) через модель и вставить."""
        if not rows:
            return EmbedStats(
                chunks_processed=0,
                chunks_skipped=0,
                batches=0,
                duration_sec=0.0,
                model_load_sec=model_load_sec,
            )

        conn = getattr(self._tls, "conn", None)
        assert conn is not None  # _ensure_connection был вызван выше
        assert self._model is not None  # _ensure_model_loaded был вызван выше

        t0 = time.perf_counter()
        batches = 0
        processed = 0
        bs = self._batch_size

        for start in range(0, len(rows), bs):
            batch = rows[start : start + bs]
            self._embed_batch(batch)
            batches += 1
            processed += len(batch)
            logger.info(
                "embedder_batch_done",
                batch=batches,
                chunks=len(batch),
                total=processed,
            )

        duration = time.perf_counter() - t0
        logger.info(
            "embedder_run_done",
            chunks=processed,
            batches=batches,
            sec=round(duration, 3),
        )
        return EmbedStats(
            chunks_processed=processed,
            chunks_skipped=0,  # caller сам подставит skipped
            batches=batches,
            duration_sec=duration,
            model_load_sec=model_load_sec,
        )

    def _embed_batch(self, rows: list[tuple[int, str, str]]) -> None:
        """
        Проэмбеддить один batch и вставить векторы в vec_chunks.

        rows = [(id, chunk_id, text_redacted), ...]. Model2Vec возвращает
        numpy-массив shape (N, dim). Для cosine distance vec0 сам нормализует,
        так что можно передавать не-нормализованные векторы.
        """
        conn = getattr(self._tls, "conn", None)
        assert conn is not None
        assert self._model is not None

        texts = [r[2] for r in rows]
        vecs = self._model.encode(texts)

        payload = []
        # numpy-массив индексируется vecs[i]; list-of-list — тоже.
        for i, row in enumerate(rows):
            payload.append((row[0], serialize_f32(vecs[i])))

        # Сериализуем write в vec_chunks между потоками — SQLite-уровень
        # single-writer, избегаем "database is locked" при конкурентных
        # embed'ах из разных asyncio.to_thread воркеров.
        with self._write_lock:
            conn.executemany(
                "INSERT INTO vec_chunks(rowid, vector) VALUES (?, ?);",
                payload,
            )
            conn.commit()
