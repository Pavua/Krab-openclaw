#!/usr/bin/env python3
"""
Feature G — CLI для batch reclustering chunks по semantic clusters.

Usage:
    venv/bin/python scripts/memory_recluster.py --num-clusters 30
    venv/bin/python scripts/memory_recluster.py --num-clusters 30 --dry-run

Читает embedding'и chunks из archive.db (vec_chunks) и перезаписывает
chunk_clusters/cluster_meta. Не трогает основные таблицы.

В dry-run просто печатает statistics без записи.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_embeddings_from_archive(limit: int | None) -> dict[str, list[float]]:
    """Читает embedding'и chunks из vec_chunks (если доступно).

    Без sqlite-vec extension вернём {} — в этом случае рекластер не сработает,
    но скрипт корректно отрапортует и выйдет.
    """
    from src.core.memory_archive import open_archive

    conn = open_archive()
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT name FROM sqlite_master WHERE name='vec_chunks';")
            if cur.fetchone() is None:
                print(
                    "vec_chunks table missing — нет embedding'ов для кластеризации", file=sys.stderr
                )
                return {}
            # vec_chunks имеет схему (chunk_id TEXT, embedding BLOB) — формат blob
            # зависит от sqlite-vec, поэтому пробуем сначала через json-extract.
            # Если не получится — вернём {} и попросим использовать external feeder.
            sql = "SELECT chunk_id, embedding FROM vec_chunks"
            if limit:
                sql += f" LIMIT {int(limit)}"
            rows = cur.execute(sql).fetchall()
        except Exception as exc:  # noqa: BLE001
            print(f"vec_chunks read failed: {exc}", file=sys.stderr)
            return {}

        result: dict[str, list[float]] = {}
        for chunk_id, blob in rows:
            vec = _decode_embedding_blob(blob)
            if vec is not None:
                result[str(chunk_id)] = vec
        return result
    finally:
        conn.close()


def _decode_embedding_blob(blob) -> list[float] | None:
    """Пытается декодировать blob в list[float]. Поддерживает sqlite-vec float32-LE."""
    if blob is None:
        return None
    if isinstance(blob, str):
        try:
            data = json.loads(blob)
            return [float(x) for x in data] if isinstance(data, list) else None
        except (json.JSONDecodeError, ValueError):
            return None
    if isinstance(blob, (bytes, memoryview)):
        import struct

        raw = bytes(blob)
        if len(raw) % 4 != 0:
            return None
        n = len(raw) // 4
        try:
            return list(struct.unpack(f"<{n}f", raw))
        except struct.error:
            return None
    return None


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(_project_root()))

    parser = argparse.ArgumentParser(description="Memory topic clustering — batch recluster")
    parser.add_argument("--num-clusters", type=int, default=30, help="целевое число кластеров")
    parser.add_argument("--limit", type=int, default=None, help="лимит chunks (для дебага)")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed для детерминизма")
    parser.add_argument(
        "--model-name", default=None, help="имя embedding-модели (для cluster_meta)"
    )
    parser.add_argument("--dry-run", action="store_true", help="не писать в БД, только статистика")
    args = parser.parse_args(argv)

    print(f"Reading embeddings from archive.db (limit={args.limit})…")
    embeddings = _load_embeddings_from_archive(args.limit)
    if not embeddings:
        print("Эмбеддингов не найдено — пропускаю.", file=sys.stderr)
        return 1
    print(f"Loaded {len(embeddings)} embeddings.")

    from src.core.memory_topic_clusters import TopicClusterIndex, _kmeans_cosine, _l2_normalize

    if args.dry_run:
        chunk_ids = list(embeddings.keys())
        normalized = [_l2_normalize(embeddings[cid]) for cid in chunk_ids]
        assignments, _centroids = _kmeans_cosine(
            normalized, num_clusters=args.num_clusters, seed=args.seed
        )
        sizes: dict[int, int] = {}
        for cluster in assignments:
            sizes[int(cluster)] = sizes.get(int(cluster), 0) + 1
        print(f"[dry-run] clusters={len(sizes)} chunks={len(assignments)}")
        print(f"[dry-run] sizes={dict(sorted(sizes.items()))}")
        return 0

    index = TopicClusterIndex()
    stats = index.recluster(
        embeddings,
        num_clusters=args.num_clusters,
        model_name=args.model_name,
        seed=args.seed,
    )
    print(
        f"Reclustered: clusters={stats.num_clusters} chunks={stats.indexed_chunks} "
        f"indexed_at={stats.indexed_at}"
    )
    print(f"Cluster sizes: {dict(sorted(stats.cluster_sizes.items()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
