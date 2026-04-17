"""Unit-тесты foundation-обёртки Model2Vec (src/core/memory_embeddings.py).

Никогда не скачивают реальную модель — Model2Vec мокается.
Кейсы:
  * ``encode_text`` возвращает bytes (BLOB-ready) и корректной длины;
  * ``encode_batch`` возвращает список bytes, все одинаковой длины;
  * ``decode_embedding`` — round-trip через struct;
  * ``cosine_similarity`` — identical=1.0, orthogonal~0, zero-norm safe.
"""

from __future__ import annotations

import struct
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


def _reset_singleton() -> None:
    """Сбросить lazy-loaded модель между тестами."""
    import src.core.memory_embeddings as me

    me._model_singleton = None


def test_encode_text_returns_bytes() -> None:
    """encode_text → bytes длиной 4*dim (float32)."""
    _reset_singleton()
    mock_model = MagicMock()
    mock_model.encode.return_value = np.array([0.1, 0.2, 0.3], dtype=np.float32)

    with patch("src.core.memory_embeddings._model_singleton", mock_model):
        from src.core.memory_embeddings import encode_text

        result = encode_text("hello")
        assert isinstance(result, bytes)
        assert len(result) == 12  # 3 * 4 bytes


def test_encode_batch() -> None:
    """encode_batch → list[bytes], всех одинаковой длины."""
    _reset_singleton()
    mock_model = MagicMock()
    mock_model.encode.return_value = np.array(
        [[0.1, 0.2], [0.3, 0.4]], dtype=np.float32
    )

    with patch("src.core.memory_embeddings._model_singleton", mock_model):
        from src.core.memory_embeddings import encode_batch

        results = encode_batch(["a", "b"])
        assert len(results) == 2
        assert all(isinstance(r, bytes) for r in results)
        assert all(len(r) == 8 for r in results)  # 2 * 4 bytes


def test_decode_embedding_roundtrip() -> None:
    """encode → decode возвращает те же значения (float32 precision)."""
    from src.core.memory_embeddings import decode_embedding

    original = [0.1, 0.2, 0.3, -0.4]
    blob = struct.pack(f"<{len(original)}f", *original)
    decoded = decode_embedding(blob, dim=len(original))
    for a, b in zip(original, decoded, strict=True):
        assert abs(a - b) < 1e-6


def test_cosine_similarity_identical() -> None:
    """Идентичные векторы → similarity ≈ 1.0."""
    vec = np.array([1.0, 0.0, 0.0], dtype=np.float32).tobytes()
    from src.core.memory_embeddings import cosine_similarity

    sim = cosine_similarity(vec, vec, dim=3)
    assert sim > 0.99


def test_cosine_similarity_orthogonal() -> None:
    """Ортогональные векторы → similarity ≈ 0."""
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32).tobytes()
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32).tobytes()
    from src.core.memory_embeddings import cosine_similarity

    sim = cosine_similarity(a, b, dim=3)
    assert abs(sim) < 0.01


def test_cosine_similarity_zero_norm_safe() -> None:
    """Zero-vector не даёт ZeroDivisionError — возвращает 0.0."""
    zero = np.array([0.0, 0.0, 0.0], dtype=np.float32).tobytes()
    other = np.array([1.0, 0.0, 0.0], dtype=np.float32).tobytes()
    from src.core.memory_embeddings import cosine_similarity

    assert cosine_similarity(zero, other, dim=3) == 0.0


def test_get_embedding_model_singleton() -> None:
    """Повторные вызовы get_embedding_model возвращают тот же объект."""
    _reset_singleton()
    sentinel = MagicMock(name="fake_model")

    with patch("src.core.memory_embeddings._model_singleton", sentinel):
        from src.core.memory_embeddings import get_embedding_model

        m1 = get_embedding_model()
        m2 = get_embedding_model()
        assert m1 is m2 is sentinel


@pytest.mark.skipif(
    True,  # по умолчанию skip — требует сеть и ~32MB модель.
    reason="Requires model2vec installed + network to download model",
)
def test_encode_real_model() -> None:
    """Опционально: реальная модель Model2Vec (snapshot download)."""
    _reset_singleton()
    from src.core.memory_embeddings import encode_text

    vec = encode_text("hello world")
    assert isinstance(vec, bytes)
    assert len(vec) > 0
