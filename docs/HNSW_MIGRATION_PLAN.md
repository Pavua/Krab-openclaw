# HNSW Migration Plan — Krab Memory Layer

> Подготовлено в Session 24 (2026-04-26). Активация — когда сработают trigger conditions.
> Текущий vec count: 72,358 / 250,000 trigger (~3.5× headroom).

## 1. Trigger conditions

Миграция запускается, когда выполняется **любое** из:

- **Vector count ≥ 250,000** в `vec_chunks` (текущее: 72,358; рост ~2-3k/неделя ⇒ ETA ~12-18 месяцев).
- **Prometheus alert `VecQueryLatencyHigh`** (живой): `histogram_quantile(0.95, sum by(le) (rate(krab_vec_query_duration_seconds_bucket[10m]))) > 0.1` стабильно ≥ 30 минут (текущее p95 ≈25ms).
- **archive.db размер ≥ 1.5 GB** (сейчас 506 MB) — sqlite-vec linear scan начинает упираться в IO.
- **Wall-clock retrieval p95 > 250ms** end-to-end из `krab_memory_retrieval_latency_seconds{phase="vec"}` (Sentry-наблюдаемая регрессия).

Не мигрируем раньше: hnswlib — деградация по точности (approximate KNN) и сложность incremental updates. Linear scan на 256-dim/250k vectors на M4 Max — терпимо.

## 2. Compatibility shape (контракт)

**Read API** (вызывается из `_vector_search` в `memory_retrieval.py:960`):
- `knn(query_vec: list[float], k: int) → list[(rowid:int, distance:float)]`
- Поддержка post-filter по `chat_id`.
- Возврат rowid должен соответствовать `chunks.id` (PK).

**Bulk-read** (для MMR cache, `memory_retrieval.py:1140-1155`):
- `get_vectors(rowids: list[int]) → dict[int, list[float]]`.

**Write API** (из `MemoryEmbedder.embed_specific`):
- `add_item(rowid: int, vec: list[float])` — append-only.
- `delete_item(rowid: int)` — для message deletion.
- `persist()` — flush в файл, atomic rename.

**Точки касания**:
- `src/core/memory_retrieval.py` — `_check_vec_meta_compat`, `_vector_search`, MMR cache.
- `src/core/memory_embedder.py` — write path.
- `scripts/memory_doctor.py` — `check_chunks_vec_alignment` нужно переписать.
- `prometheus_metrics.py` — добавить `krab_hnsw_query_duration_seconds`, dual-emit во время cutover.

**Meta-таблица**: расширить `vec_chunks_meta` ключами `backend` (`sqlite_vec`|`hnswlib`), `index_path`, `M`, `ef_construction`, `ef_search`, `index_built_at`, `index_label_max`. C7 guard расширить.

## 3. Migration sequence

1. **Pre-flight** (read-only): `memory_doctor --json`, snapshot `archive.db`, bench текущий p95.
2. **Dump existing vectors**: `scripts/memory_dump_vectors.py` — `SELECT c.id, v.vector FROM vec_chunks v JOIN chunks c ON c.id=v.rowid` → numpy `.npy`. На 250k×256×float32 ≈ 256 MB RAM.
3. **Build hnswlib index** (offline): `M=16`, `ef_construction=200`, `space="cosine"`, `max_elements = vec_count * 1.5`. Сохранить через `index.save_index(path)` в `~/.openclaw/krab_memory/hnsw_chunks.bin`. Бенч `ef_search ∈ {32,64,128,200}` — выбрать для recall@10 ≥ 0.95.
4. **Dual-write** (1-2 недели): `MemoryEmbedder` пишет и в `vec_chunks` (legacy), и в hnswlib. Persist каждые N items + at shutdown.
5. **Shadow read**: env `KRAB_MEMORY_HNSW_SHADOW=1` — параллельный KNN, лог `would_change_top5`, latency delta. 48-72h.
6. **Cutover**: feature flag `MEMORY_VEC_BACKEND=sqlite_vec|hnswlib` (default `sqlite_vec`). Переключить → мониторить 24h.
7. **Decommission**: после 2 недель стабильности — `VACUUM` archive.db без vec_chunks (схема остаётся для rollback ~месяц), снять dual-write.

**Rollback**: flip env обратно → SIGHUP не нужен (per-call gate в `_vector_search`).

## 4. Risks + open questions

- **Persistent storage**: `hnswlib.save_index()` атомарен? Нет встроенного fsync — нужен `tmp + os.replace()` wrapper. Файл-индекс не транзакционен с SQLite → split-brain после crash (chunk inserted, hnsw not flushed). Решение: на startup сравнить `chunks.id MAX` vs `hnsw.element_count`, добивать missing items.
- **Incremental updates**: hnswlib поддерживает `add_items` после load, но `M`/`ef_construction` фиксированы при init. При росте сверх `max_elements` — `resize_index()`. План: пересобирать full index при +50% relative growth.
- **Deletions**: `mark_deleted(label)` — soft delete, увеличивает search overhead. Hard delete = full rebuild. Для Krab: soft delete + ребилд раз в квартал.
- **Thread safety**: hnswlib GIL-free на чтение, writes требуют lock. Текущий dedicated `_embed_executor` (single thread) уже сериализует.
- **Recall regression**: HNSW approximate. Нужен ground-truth набор (200 запросов с brute-force top-10) для CI-проверки `recall@10 ≥ 0.95`.
- **Open**: где хранить index — рядом с archive.db или отдельный путь? backup (rsync vs скрипт)?
- **Open**: per-chat фильтр HNSW pre-filter не имеет — берём `k * 5` и фильтруем post-hoc; на узких чатах recall может просесть. Альтернатива: per-chat indexes (overhead памяти).
- **Open**: совместимость с C7 mismatch guard — ребилд hnsw при смене модели обязателен.

## 5. Estimated effort

**2-3 session-days**:
- Day 1: dump script + offline index builder + bench harness + recall ground truth.
- Day 2: backend abstraction (`VectorBackend` Protocol), hnswlib implementation, dual-write, env flag, обновление `memory_doctor` + Prometheus.
- Day 3 (optional): shadow harness, cutover runbook, rollback drill, обновление CLAUDE.md.

Если шаг 5 (shadow) идёт в фоне без активного дева — реальный actionable дев = 2 days.

---

**Source files for future session:**
- `src/core/memory_retrieval.py` (vec read path, MMR vec cache, C7 guard)
- `src/core/memory_indexer_worker.py` (write path, dedicated `_embed_executor`)
- `src/core/memory_embedder.py` (write API contract)
- `scripts/memory_doctor.py` (alignment checks — переписать под HNSW)
- `src/core/prometheus_metrics.py:67-78` (`krab_vec_query_duration_seconds`)
