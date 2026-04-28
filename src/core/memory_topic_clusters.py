"""
Feature G — Topic Clustering для memory retrieval.

Группирует chunks (разговорные нити) по semantic clusters через k-means
по их embedding'ам. На retrieval-time top-K результатов поиска расширяются
chunks из тех же кластеров → broader context.

Архитектура:
  - `TopicClusterIndex` — singleton, читает/пишет sidecar таблицы archive.db:
      * `chunk_clusters(chunk_id PK, cluster_id, distance, assigned_at)`
      * `cluster_meta(key, value)` — model_name, num_clusters, indexed_at, …
  - `recluster(embeddings, chunk_ids, num_clusters)` — переиндексирует всё.
  - `expand_with_cluster(chunk_ids, max_per_cluster)` — на retrieval-time
    добавляет соседей по кластеру (returns extra chunk_ids).
  - `cluster_stats()` — для диагностики и CLI.

K-means реализован вручную через cosine-distance, без sklearn — чтобы
не тащить лишнюю зависимость в hot-path. Sklearn опционально подхватываем
для batch reclustering, если он установлен (быстрее и стабильнее).

Persistence: всё лежит в archive.db (sidecar tables); sidecar-режим
позволяет жить с любой версией основной схемы.
"""

from __future__ import annotations

import json
import logging
import math
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.core.memory_archive import (
    ArchivePaths,
    ensure_chunk_clusters_tables,
    open_archive,
)

logger = logging.getLogger(__name__)

# Порог максимальных итераций k-means, чтобы не зависнуть на degenerate данных.
_KMEANS_MAX_ITER = 50
# Порог сходимости (доля смены центров): меньше → стабильнее, дольше итерируем.
_KMEANS_TOL = 1e-4


# ---------------------------------------------------------------------------
# Линейная алгебра без numpy/scipy.
# ---------------------------------------------------------------------------


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-нормализация (in-place эквивалент). Нулевой вектор остаётся нулём."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return list(vec)
    return [x / norm for x in vec]


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """1 - cosine_similarity. Принимает уже нормализованные векторы."""
    # Если a и b нормализованы, dot-product == cosine similarity.
    if len(a) != len(b):
        return 1.0
    sim = sum(x * y for x, y in zip(a, b))
    # Зажимаем в [-1, 1] от плавающего шума.
    if sim > 1.0:
        sim = 1.0
    elif sim < -1.0:
        sim = -1.0
    return 1.0 - sim


def _mean_vector(vectors: list[list[float]]) -> list[float]:
    """Среднее по столбцам, без нормализации."""
    if not vectors:
        return []
    dim = len(vectors[0])
    acc = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            acc[i] += x
    n = float(len(vectors))
    return [x / n for x in acc]


# ---------------------------------------------------------------------------
# K-means (cosine).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClusterAssignment:
    """Результат kmeans для одного chunk'а."""

    chunk_id: str
    cluster_id: int
    distance: float


def _kmeans_cosine(
    embeddings: list[list[float]],
    num_clusters: int,
    *,
    max_iter: int = _KMEANS_MAX_ITER,
    tol: float = _KMEANS_TOL,
    seed: int = 42,
) -> tuple[list[int], list[list[float]]]:
    """Простой k-means с cosine distance. Возвращает (assignments, centroids).

    Все векторы должны быть L2-нормализованы заранее (вызывается из
    `recluster`, где это уже сделано).
    """
    n = len(embeddings)
    if n == 0:
        return [], []
    k = max(1, min(num_clusters, n))

    rng = random.Random(seed)
    # k-means++ упрощённый: первый центр случайный, дальше — самый дальний
    # от уже выбранных.
    indices = [rng.randrange(n)]
    while len(indices) < k:
        best_idx = -1
        best_dist = -1.0
        for i in range(n):
            if i in indices:
                continue
            min_d = min(_cosine_distance(embeddings[i], embeddings[j]) for j in indices)
            if min_d > best_dist:
                best_dist = min_d
                best_idx = i
        if best_idx < 0:
            break
        indices.append(best_idx)

    centroids = [list(embeddings[i]) for i in indices]
    assignments = [0] * n
    prev_assignments: list[int] | None = None

    for _iteration in range(max_iter):
        # Шаг 1: присваиваем точки ближайшим центрам.
        for i, vec in enumerate(embeddings):
            best_c = 0
            best_d = float("inf")
            for c_idx, centroid in enumerate(centroids):
                d = _cosine_distance(vec, centroid)
                if d < best_d:
                    best_d = d
                    best_c = c_idx
            assignments[i] = best_c

        # Шаг 2: пересчитываем центры (среднее + L2-нормализация).
        new_centroids: list[list[float]] = []
        for c_idx in range(k):
            members = [embeddings[i] for i, a in enumerate(assignments) if a == c_idx]
            if not members:
                # Пустой кластер — пере-инициализируем случайной точкой.
                new_centroids.append(list(embeddings[rng.randrange(n)]))
                continue
            mean = _mean_vector(members)
            new_centroids.append(_l2_normalize(mean))

        # Проверка сходимости.
        if prev_assignments is not None:
            changed = sum(1 for a, b in zip(prev_assignments, assignments) if a != b)
            if changed / max(1, n) < tol:
                centroids = new_centroids
                break
        prev_assignments = list(assignments)
        centroids = new_centroids

    return assignments, centroids


# ---------------------------------------------------------------------------
# Публичный API.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClusterStats:
    """Сводка по последней кластеризации."""

    num_clusters: int
    indexed_chunks: int
    indexed_at: str | None
    model_name: str | None
    cluster_sizes: dict[int, int]


class TopicClusterIndex:
    """Управление chunk_clusters/cluster_meta. Sidecar поверх archive.db."""

    def __init__(
        self,
        archive_paths: ArchivePaths | None = None,
        *,
        now_fn=None,
    ) -> None:
        self._paths = archive_paths
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    # ---------- helpers --------------------------------------------------

    def _open(self) -> sqlite3.Connection:
        conn = open_archive(self._paths, read_only=False, create_if_missing=True)
        ensure_chunk_clusters_tables(conn)
        return conn

    def _now_iso(self) -> str:
        return self._now_fn().replace(tzinfo=None).isoformat(timespec="seconds") + "Z"

    # ---------- write paths ---------------------------------------------

    def recluster(
        self,
        embeddings: dict[str, list[float]],
        *,
        num_clusters: int = 30,
        model_name: str | None = None,
        seed: int = 42,
    ) -> ClusterStats:
        """Полная переиндексация: stale assignments удаляются, пишутся новые.

        Args:
            embeddings: {chunk_id: vector}. Векторы будут L2-нормализованы.
            num_clusters: целевое число кластеров (clamp до len(embeddings)).
            model_name: имя embedding-модели (для cluster_meta).
            seed: RNG seed (детерминированность для тестов).
        """
        if not embeddings:
            return ClusterStats(
                num_clusters=0,
                indexed_chunks=0,
                indexed_at=None,
                model_name=None,
                cluster_sizes={},
            )

        chunk_ids = list(embeddings.keys())
        normalized = [_l2_normalize(embeddings[cid]) for cid in chunk_ids]

        assignments, centroids = _kmeans_cosine(
            normalized,
            num_clusters=num_clusters,
            seed=seed,
        )

        # Считаем distance до своего центра — пригодится для дебага и сортировки.
        rows: list[tuple[str, int, float, str]] = []
        now_iso = self._now_iso()
        for cid, vec, cluster in zip(chunk_ids, normalized, assignments):
            d = _cosine_distance(vec, centroids[cluster])
            rows.append((cid, int(cluster), float(d), now_iso))

        cluster_sizes: dict[int, int] = {}
        for _, cluster, _d, _ts in rows:
            cluster_sizes[cluster] = cluster_sizes.get(cluster, 0) + 1

        conn = self._open()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN;")
            cur.execute("DELETE FROM chunk_clusters;")
            cur.executemany(
                """
                INSERT OR REPLACE INTO chunk_clusters
                    (chunk_id, cluster_id, distance, assigned_at)
                VALUES (?, ?, ?, ?);
                """,
                rows,
            )
            # cluster_meta UPSERT (несколько ключей).
            meta_pairs = [
                ("num_clusters", str(len(centroids))),
                ("indexed_chunks", str(len(rows))),
                ("indexed_at", now_iso),
                ("model_name", model_name or ""),
                ("cluster_sizes_json", json.dumps(cluster_sizes, sort_keys=True)),
            ]
            cur.executemany(
                """
                INSERT INTO cluster_meta(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                """,
                meta_pairs,
            )
            conn.commit()
            logger.info(
                "topic_clusters_reclustered chunks=%d clusters=%d",
                len(rows),
                len(centroids),
            )
        except sqlite3.Error:
            conn.rollback()
            logger.exception("topic_clusters_recluster_failed")
            raise
        finally:
            conn.close()

        return ClusterStats(
            num_clusters=len(centroids),
            indexed_chunks=len(rows),
            indexed_at=now_iso,
            model_name=model_name,
            cluster_sizes=cluster_sizes,
        )

    # ---------- read paths ----------------------------------------------

    def get_cluster_id(self, chunk_id: str) -> int | None:
        """Возвращает cluster_id для chunk'а, либо None."""
        conn = self._open()
        try:
            row = conn.execute(
                "SELECT cluster_id FROM chunk_clusters WHERE chunk_id = ?;",
                (chunk_id,),
            ).fetchone()
            return int(row[0]) if row else None
        except sqlite3.OperationalError:
            return None
        finally:
            conn.close()

    def expand_with_cluster(
        self,
        chunk_ids: list[str],
        *,
        max_per_cluster: int = 3,
    ) -> list[str]:
        """Добавляет к input chunk_ids соседей по кластеру.

        Returns:
            Дополнительные chunk_ids (не пересекающиеся с input). Сортированы
            по `distance` ASC (ближе к центроиду — релевантнее).
        """
        if not chunk_ids or max_per_cluster <= 0:
            return []
        conn = self._open()
        try:
            placeholders = ",".join("?" * len(chunk_ids))
            cluster_rows = conn.execute(
                f"""
                SELECT DISTINCT cluster_id FROM chunk_clusters
                WHERE chunk_id IN ({placeholders});
                """,
                list(chunk_ids),
            ).fetchall()
            clusters = [int(r[0]) for r in cluster_rows]
            if not clusters:
                return []

            input_set = set(chunk_ids)
            extras: list[str] = []
            for cluster in clusters:
                rows = conn.execute(
                    """
                    SELECT chunk_id FROM chunk_clusters
                    WHERE cluster_id = ?
                    ORDER BY distance ASC
                    LIMIT ?;
                    """,
                    (cluster, max_per_cluster + len(input_set)),
                ).fetchall()
                added = 0
                for (cid,) in rows:
                    if cid in input_set or cid in extras:
                        continue
                    extras.append(cid)
                    added += 1
                    if added >= max_per_cluster:
                        break
            return extras
        except sqlite3.OperationalError:
            logger.warning("topic_clusters_expand_failed")
            return []
        finally:
            conn.close()

    def cluster_stats(self) -> ClusterStats:
        """Возвращает сводку из cluster_meta + cluster_sizes из реальной таблицы."""
        conn = self._open()
        try:
            meta_rows = conn.execute(
                "SELECT key, value FROM cluster_meta;",
            ).fetchall()
            meta = {k: v for k, v in meta_rows}
            sizes_raw = meta.get("cluster_sizes_json", "{}")
            try:
                sizes = {int(k): int(v) for k, v in json.loads(sizes_raw).items()}
            except (json.JSONDecodeError, ValueError, TypeError):
                sizes = {}
            return ClusterStats(
                num_clusters=int(meta.get("num_clusters", "0") or 0),
                indexed_chunks=int(meta.get("indexed_chunks", "0") or 0),
                indexed_at=meta.get("indexed_at") or None,
                model_name=meta.get("model_name") or None,
                cluster_sizes=sizes,
            )
        except sqlite3.OperationalError:
            return ClusterStats(0, 0, None, None, {})
        finally:
            conn.close()


# Singleton: можно переинициализировать через `configure_default_path()`.
topic_cluster_index = TopicClusterIndex()


def configure_default_path(directory: Path) -> None:
    """Bootstrap-hook: переинициализирует singleton под указанную директорию."""
    global topic_cluster_index
    topic_cluster_index = TopicClusterIndex(ArchivePaths.under(directory))
