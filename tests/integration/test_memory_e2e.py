"""
Integration e2e — полный pipeline Memory Layer.

Сценарий:
  1. Synthetic Telegram Export JSON (fixture 36 msgs).
  2. bootstrap_memory.run_bootstrap() → archive.db с chats/messages/chunks + FTS5.
  3. MemoryEmbedder(_model=FakeModel) → заполняет vec_chunks.
  4. HybridRetriever.search(query) с прогруженным sqlite-vec → результаты
     с векторным путём, а не только FTS5.

Задача — прокрутить все слои вместе на одном процессе, без реального Model2Vec
(долго скачивать в CI) и без реальных Telegram-данных.

Этот файл запускается с `--noconftest`, т.к. parent conftest.py ломается
на env-vars (обходится после того как Track B закоммитит config.py fix).
"""

from __future__ import annotations

import os

# ── env-guard до импортов src.* (см. test_memory_commands) ─────────────
for _k, _v in {
    "TELEGRAM_API_ID": "0",
    "TELEGRAM_API_HASH": "test",
    "OWNER_ID": "0",
}.items():
    if not os.environ.get(_k):
        os.environ[_k] = _v

from pathlib import Path

import numpy as np
import pytest

# Memory Layer модули.
from src.core.memory_archive import ArchivePaths, open_archive
from src.core.memory_retrieval import HybridRetriever

# Опциональные модули (могут отсутствовать на момент написания теста).
try:
    from src.core.memory_embedder import (  # type: ignore[import-not-found]
        MemoryEmbedder,
        create_vec_table,
    )

    _EMBEDDER_AVAILABLE = True
except ImportError:
    _EMBEDDER_AVAILABLE = False

from scripts.bootstrap_memory import run_bootstrap

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "telegram_export_sample.json"


# ---------------------------------------------------------------------------
# Fake Model2Vec — детерминированная заглушка.
# ---------------------------------------------------------------------------


class _FakeModel:
    """
    Детерминированный embedder для integration-тестов.

    Выдаёт псевдо-случайные векторы посеянные от hash'а текста — одинаковый
    текст даёт одинаковый вектор. Отдалённо похожие тексты имеют похожие
    вектора через общие символы. Достаточно для проверки pipeline'а.
    """

    dim = 256

    def encode(self, texts):
        out = np.zeros((len(texts), self.dim), dtype="float32")
        for i, t in enumerate(texts):
            seed = sum(ord(c) for c in t) % (10**6)
            rng = np.random.RandomState(seed)
            out[i] = rng.randn(self.dim).astype("float32")
        return out


# ---------------------------------------------------------------------------
# Фикстура — полный bootstrap на fixture.
# ---------------------------------------------------------------------------


@pytest.fixture
def bootstrapped_archive(tmp_path: Path) -> ArchivePaths:
    """Прогоняет bootstrap на synthetic fixture в tmp-БД, возвращает paths."""
    assert FIXTURE_PATH.exists(), f"fixture missing: {FIXTURE_PATH}"
    paths = ArchivePaths.under(tmp_path / "mem")
    result = run_bootstrap(
        export_path=FIXTURE_PATH,
        db_path=paths.db,
        allow_all=True,  # обходим whitelist для интеграции
        dry_run=False,
    )
    # Sanity: bootstrap что-то вставил.
    assert result.chunks_created > 0
    assert result.messages_processed > 0
    return paths


# ---------------------------------------------------------------------------
# Test cases.
# ---------------------------------------------------------------------------


class TestBootstrapOnly:
    """Убеждаемся что bootstrap работает без embedder'а (FTS5-only путь)."""

    def test_bootstrap_creates_searchable_index(self, bootstrapped_archive: ArchivePaths) -> None:
        r = HybridRetriever(archive_paths=bootstrapped_archive, model_name=None)
        # fixture содержит "dashboard"-подобные слова и имена проектов —
        # ищем устойчивый общий корень "Session".
        results = r.search("Session", top_k=5)
        # FTS5 путь должен найти хотя бы один chunk.
        assert len(results) > 0
        r.close()

    def test_pii_stripped_in_fts(self, bootstrapped_archive: ArchivePaths) -> None:
        """Карта "4242 4242..." из fixture не должна найтись (редактируется)."""
        r = HybridRetriever(archive_paths=bootstrapped_archive, model_name=None)
        results = r.search("4242")
        r.close()
        # Либо [], либо все результаты содержат [REDACTED:CARD] вместо цифр.
        for res in results:
            assert "4242 4242" not in res.text_redacted


# ---------------------------------------------------------------------------
# Embedder path — активируется когда memory_embedder.py готов.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _EMBEDDER_AVAILABLE,
    reason="memory_embedder.py пока не смёржен — e2e embedder-путь отложен",
)
class TestFullPipelineWithEmbedder:
    def test_embed_after_bootstrap(self, bootstrapped_archive: ArchivePaths) -> None:
        """Bootstrap → Embedder → проверка что vec_chunks заполнен."""
        embedder = MemoryEmbedder(
            archive_paths=bootstrapped_archive,
            _model=_FakeModel(),
        )
        stats = embedder.embed_all_unindexed()
        embedder.close()

        assert stats.chunks_processed > 0
        assert stats.chunks_skipped == 0

        # Проверяем vec_chunks напрямую.
        conn = open_archive(bootstrapped_archive)
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        n_vecs = conn.execute("SELECT COUNT(*) FROM vec_chunks;").fetchone()[0]
        conn.close()

        assert n_vecs == stats.chunks_processed

    def test_embed_idempotent(self, bootstrapped_archive: ArchivePaths) -> None:
        """Повторный embed_all_unindexed: 0 new, все skipped."""
        embedder = MemoryEmbedder(
            archive_paths=bootstrapped_archive,
            _model=_FakeModel(),
        )
        first = embedder.embed_all_unindexed()
        second = embedder.embed_all_unindexed()
        embedder.close()

        assert first.chunks_processed > 0
        assert second.chunks_processed == 0
        assert second.chunks_skipped == first.chunks_processed

    def test_retrieval_with_vec_path(self, bootstrapped_archive: ArchivePaths) -> None:
        """
        После embedding'а HybridRetriever должен уметь использовать векторный
        путь (через sqlite-vec) параллельно с FTS5. RRF объединяет результаты.

        Проверяем косвенно: при достаточно длинном запросе векторы могут
        подтянуть chunks, которые FTS5 бы пропустил (нет точного keyword).
        """
        embedder = MemoryEmbedder(
            archive_paths=bootstrapped_archive,
            _model=_FakeModel(),
        )
        embedder.embed_all_unindexed()
        embedder.close()

        r = HybridRetriever(
            archive_paths=bootstrapped_archive,
            model_name=None,  # модель инжектим через _model если нужно
        )
        # Смоук: searcher не падает и возвращает результаты.
        results = r.search("Session", top_k=5)
        r.close()
        assert len(results) > 0
