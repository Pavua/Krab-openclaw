"""Тесты Feature G — Topic Clustering."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.memory_archive import (
    ArchivePaths,
    create_schema,
    open_archive,
)
from src.core.memory_topic_clusters import (
    TopicClusterIndex,
    _cosine_distance,
    _kmeans_cosine,
    _l2_normalize,
)


@pytest.fixture()
def archive_dir(tmp_path: Path) -> Path:
    paths = ArchivePaths.under(tmp_path)
    conn = open_archive(paths)
    try:
        create_schema(conn)
        # Минимальный chat + chunks (FK satisfied).
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO chats(chat_id, title, chat_type) VALUES (?, ?, ?);", ("c1", "t", "private")
        )
        for i in range(6):
            cur.execute(
                """
                INSERT INTO chunks(chunk_id, chat_id, start_ts, end_ts, message_count, char_len, text_redacted)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    f"ch{i}",
                    "c1",
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:01:00Z",
                    1,
                    10,
                    f"chunk {i}",
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def test_l2_normalize_unit_length():
    vec = [3.0, 4.0]
    out = _l2_normalize(vec)
    # Сумма квадратов ≈ 1.
    assert abs(sum(x * x for x in out) - 1.0) < 1e-9


def test_cosine_distance_identical_vectors_near_zero():
    a = _l2_normalize([1.0, 2.0, 3.0])
    assert _cosine_distance(a, a) < 1e-9


def test_kmeans_clusters_two_groups():
    # Две явные группы: вокруг (1,0,0) и вокруг (0,1,0).
    group_a = [_l2_normalize([1.0, 0.05 * i, 0.0]) for i in range(5)]
    group_b = [_l2_normalize([0.05 * i, 1.0, 0.0]) for i in range(5)]
    embeddings = group_a + group_b
    assignments, centroids = _kmeans_cosine(embeddings, num_clusters=2, seed=1)
    assert len(assignments) == 10
    assert len(centroids) == 2
    # Все 5 элементов group_a должны попасть в один кластер.
    a_clusters = set(assignments[:5])
    b_clusters = set(assignments[5:])
    assert len(a_clusters) == 1
    assert len(b_clusters) == 1
    assert a_clusters != b_clusters


def test_recluster_persists_and_expands(archive_dir: Path):
    paths = ArchivePaths.under(archive_dir)
    index = TopicClusterIndex(paths)
    embeddings = {
        "ch0": [1.0, 0.0, 0.0],
        "ch1": [0.99, 0.05, 0.0],
        "ch2": [0.98, 0.01, 0.01],
        "ch3": [0.0, 1.0, 0.0],
        "ch4": [0.05, 0.99, 0.0],
        "ch5": [0.01, 0.98, 0.05],
    }
    stats = index.recluster(embeddings, num_clusters=2, model_name="test-model", seed=7)
    assert stats.num_clusters == 2
    assert stats.indexed_chunks == 6
    assert stats.model_name == "test-model"
    # Sizes покрывают все 6.
    assert sum(stats.cluster_sizes.values()) == 6

    # Expansion: дай ch0 → должно вернуть других из его кластера, не себя.
    extras = index.expand_with_cluster(["ch0"], max_per_cluster=2)
    assert "ch0" not in extras
    assert len(extras) <= 2
    # ch3, ch4, ch5 — другая группа, не должны попасть.
    assert all(eid in {"ch1", "ch2"} for eid in extras)


def test_cluster_stats_empty_when_never_reclustered(tmp_path: Path):
    paths = ArchivePaths.under(tmp_path)
    conn = open_archive(paths)
    try:
        create_schema(conn)
    finally:
        conn.close()
    index = TopicClusterIndex(paths)
    stats = index.cluster_stats()
    assert stats.num_clusters == 0
    assert stats.indexed_chunks == 0
    assert stats.cluster_sizes == {}
