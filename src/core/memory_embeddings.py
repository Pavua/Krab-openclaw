"""
Helpers для Memory Layer Phase 2: Model2Vec embeddings.

Назначение
==========
Функции-помощники поверх ``MemoryEmbedder`` (см. ``memory_embedder.py``):

* ``get_embedding_model()`` — lazy singleton Model2Vec ``StaticModel``.
* ``encode_batch(texts)`` — эмбеддинг списка строк → list[numpy.ndarray].
* ``encode_text(text)``  — эмбеддинг одной строки → numpy.ndarray.
* ``cosine_similarity(a, b)`` — косинусная близость двух векторов,
  принимает как numpy-массивы, так и сырые float32-bytes из vec_chunks.

Модель загружается один раз и кэшируется на уровне модуля. Thread-safe
в стандартном GIL-режиме (модель readonly).
"""

from __future__ import annotations

import struct
from typing import Any, Sequence

import numpy as np

# Модель Model2Vec multilingual 256-dim (совпадает с memory_embedder.py).
DEFAULT_MODEL_NAME = "minishlab/M2V_multilingual_output"
DEFAULT_DIM = 256

# Singleton модели (lazy-init). Защищён от повторной загрузки.
_MODEL: Any | None = None


def get_embedding_model() -> Any:
    """Загрузить Model2Vec один раз, вернуть уже загруженную при повторных вызовах."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    from model2vec import StaticModel  # type: ignore[import-not-found]

    _MODEL = StaticModel.from_pretrained(DEFAULT_MODEL_NAME)
    return _MODEL


def encode_batch(texts: Sequence[str]) -> list[np.ndarray]:
    """
    Эмбеддинг списка строк.

    Model2Vec возвращает ``numpy.ndarray`` shape (N, dim). Возвращаем
    list per-row для удобства итерации.
    """
    if not texts:
        return []
    model = get_embedding_model()
    # На пустых строках Model2Vec всё равно возвращает валидный (пустой tokens) вектор.
    vecs = model.encode(list(texts))
    # Гарантируем float32.
    vecs = np.asarray(vecs, dtype=np.float32)
    return [vecs[i] for i in range(vecs.shape[0])]


def encode_text(text: str) -> np.ndarray:
    """Эмбеддинг одной строки → 1D numpy.ndarray(float32, shape=(dim,))."""
    out = encode_batch([text])
    return out[0] if out else np.zeros(DEFAULT_DIM, dtype=np.float32)


def _deserialize_f32(data: Any) -> np.ndarray:
    """
    Привести любой формат вектора к numpy.ndarray(float32).

    Допустимые входы:
      * numpy.ndarray;
      * list/tuple чисел;
      * bytes (сырой float32 little-endian), как хранит sqlite-vec.
    """
    if isinstance(data, np.ndarray):
        return data.astype(np.float32, copy=False)
    if isinstance(data, (bytes, bytearray, memoryview)):
        raw = bytes(data)
        n = len(raw) // 4
        return np.asarray(struct.unpack(f"<{n}f", raw), dtype=np.float32)
    # list / tuple / generator.
    return np.asarray(list(data), dtype=np.float32)


def cosine_similarity(a: Any, b: Any) -> float:
    """
    Косинусная близость двух векторов в диапазоне [-1, 1] (обычно [0, 1]).

    Принимает numpy-массивы, списки чисел или сырые float32 bytes
    (как хранит sqlite-vec).
    """
    va = _deserialize_f32(a)
    vb = _deserialize_f32(b)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))
