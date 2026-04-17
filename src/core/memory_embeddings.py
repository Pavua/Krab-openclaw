"""
Memory Layer Phase 2: Model2Vec embeddings для semantic search.

Это высокоуровневая «foundation» обёртка над Model2Vec StaticModel.
В отличие от ``memory_embedder.py`` (воркер, который идёт в archive.db и
пишет векторы в ``vec_chunks``), этот модуль предоставляет простые
помощники ``encode_text``/``encode_batch``/``cosine_similarity`` для
произвольного кода, которому нужно получить embedding отдельной строки
или пары строк (например, !ask / !recall / !search).

Model2Vec — маленькая (~32MB) embedding-модель, быстрая, локальная.
Используется для augmentation FTS5-поиска в archive.db семантикой.

Дефолтная модель — multilingual 256-dim (M2V_multilingual_output),
совместима по размерности с ``memory_embedder.DEFAULT_DIM``.
"""

from __future__ import annotations

import struct
from typing import Any

from structlog import get_logger

logger = get_logger(__name__)

#: Канонический HuggingFace id мультиязычной Model2Vec-модели.
#: 256 dims, совместим с ``memory_embedder.DEFAULT_DIM``.
DEFAULT_MODEL = "minishlab/M2V_multilingual_output"

#: Размерность вектора (для decode/cosine без параметров).
DEFAULT_DIM = 256

# Singleton модели. Грузим лениво, держим между вызовами.
_model_singleton: Any | None = None


def get_embedding_model() -> Any:
    """Лениво загрузить Model2Vec-модель (singleton).

    При отсутствии пакета или ошибке загрузки — поднимает исключение,
    чтобы caller мог fallback на FTS5.
    """
    global _model_singleton
    if _model_singleton is None:
        try:
            from model2vec import StaticModel  # type: ignore[import-not-found]
        except ImportError as exc:
            logger.error("model2vec_import_failed", error=str(exc))
            raise
        try:
            _model_singleton = StaticModel.from_pretrained(DEFAULT_MODEL)
            logger.info("model2vec_loaded", model=DEFAULT_MODEL)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "model2vec_load_failed",
                model=DEFAULT_MODEL,
                error=str(exc),
            )
            raise
    return _model_singleton


def encode_text(text: str) -> bytes:
    """Encode одну строку в float32-bytes (готово к INSERT в BLOB)."""
    model = get_embedding_model()
    # StaticModel.encode возвращает numpy.ndarray.
    vec = model.encode(text)
    # Приводим к float32 — vec0 sqlite-vec ждёт именно 4-byte floats.
    astype = getattr(vec, "astype", None)
    if astype is not None:
        return astype("float32").tobytes()
    # Generic fallback для list/tuple.
    values = list(vec)
    return struct.pack(f"<{len(values)}f", *values)


def encode_batch(texts: list[str]) -> list[bytes]:
    """Batch-encode — в десятки раз быстрее, чем по одному (Model2Vec vectorized)."""
    model = get_embedding_model()
    vecs = model.encode(texts)
    result: list[bytes] = []
    for v in vecs:
        astype = getattr(v, "astype", None)
        if astype is not None:
            result.append(astype("float32").tobytes())
        else:
            values = list(v)
            result.append(struct.pack(f"<{len(values)}f", *values))
    return result


def decode_embedding(blob: bytes, dim: int = DEFAULT_DIM) -> list[float]:
    """Decode BLOB обратно в list[float] (для debug/display)."""
    return list(struct.unpack(f"<{dim}f", blob))


def cosine_similarity(a: bytes, b: bytes, dim: int = DEFAULT_DIM) -> float:
    """Cosine similarity между двумя embedding-blobs.

    dim определяется автоматически из длины blob, если не передан.
    """
    # numpy быстрее struct + math; импорт локальный — numpy тянется
    # и model2vec'ом, и chromadb, так что точно доступен в runtime.
    import numpy as np

    va = np.frombuffer(a, dtype=np.float32)
    vb = np.frombuffer(b, dtype=np.float32)
    # dim явно не используется; оставлен для обратной совместимости API.
    _ = dim
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom < 1e-9:
        return 0.0
    return float(np.dot(va, vb) / denom)
