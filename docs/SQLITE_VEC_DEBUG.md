# sqlite-vec malformed — root cause и план fix (Session 13 carry-over)

## TL;DR

`sqlite-vec` extension **загружается корректно** (`vec_version()` → `v0.1.9`),
виртуальная таблица `vec_chunks` создана правильно
(`USING vec0(vector float[256] distance_metric=cosine)`), семантические
`MATCH`-запросы **не падают** и возвращают результаты.

Настоящий симптом — **массовое расхождение `vec_chunks.rowid` с `chunks.id`**:
из 72302 записанных векторов только 189 имеют соответствующий chunk. То есть
семантический поиск формально работает, но возвращает rowid'ы «мёртвых»
chunks, у которых уже нет строки в таблице `chunks` → JOIN на `c.id = v.rowid`
отбрасывает 99.7% попаданий, в итоге `_vector_search` в
`memory_retrieval.py:547` фактически бесполезен.

Формулировка «malformed» из Session 13 memo вводит в заблуждение: БД
не битая и не нуждается в `VACUUM/INTEGRITY_CHECK`. Проблема в
**десинхронизации rowid пространства** между `chunks` и `vec_chunks`,
аналогичной той, что исправил `repair_sqlite_vec.py` для FTS5 в Wave 29.

## Диагностика (dry-run, 24.04.2026)

```
archive.db: /Users/pablito/.openclaw/krab_memory/archive.db (506.2 МБ)
  chunks:          72328 строк, id range (1, 198352)
  messages_fts:    72328 (docsize: 72328)                 OK
  vec_chunks:      72302 векторов, rowid range (1, 198352)
  vec.rowid ∩ chunks.id через JOIN:  189                  BROKEN (0.26%)
  chunks без вектора (LEFT JOIN):    72139
```

- `vec_version()` → `v0.1.9` — extension live.
- `SELECT sql FROM sqlite_master WHERE name='vec_chunks'` —
  `CREATE VIRTUAL TABLE vec_chunks USING vec0(vector float[256] distance_metric=cosine)` — OK.
- Semantic `SELECT rowid, distance FROM vec_chunks WHERE vector MATCH ? ORDER BY distance LIMIT 3`
  **отрабатывает** и возвращает непустой результат.

## Дополнительный баг в `scripts/repair_sqlite_vec.py`

`diagnose()` в `scripts/repair_sqlite_vec.py:99-106` считает orphan'ы так:

```python
SELECT COUNT(*) FROM vec_chunks_rowids AS vr
LEFT JOIN chunks AS c ON c.id = vr.id
WHERE c.id IS NULL
```

В `sqlite-vec v0.1.9` shadow-таблица `vec_chunks_rowids` имеет колонку `id`,
которая **всегда NULL** если `vec0` создавалась без явного primary-key
mapping (наш случай — используется только `rowid`). В результате
все 72302 вектора всегда помечаются как orphaned, даже 189 валидных.
Правильный JOIN — на `vr.rowid` (или напрямую `vec_chunks v JOIN chunks c ON c.id = v.rowid`).

Это не критично (repair всё равно нужен), но `[WARN]` метрика в
`memory_doctor` и `health_deep_collector` использует ту же логику — значит
dashboards занижают «здоровье vec».

## Root cause причины десинхронизации

`src/core/memory_embedder.py` создаёт `vec_chunks` через
`INSERT INTO vec_chunks(rowid, vector) VALUES (?, ?)` с `rowid = chunks.id`
(см. `scripts/repair_sqlite_vec.py:199`, `scripts/encode_memory_phase2.py`,
`src/core/memory_indexer_worker.py`). Схема ожидает 1-к-1 соответствие
`chunks.id ↔ vec_chunks.rowid`. Но:

1. Между **Wave 26 и Wave 29** (bootstrap yung_nagato,
   `archive.db.before_yung_nagato_bootstrap` — 1.4 МБ) `chunks` были
   массово удалены и re-inserted с новыми `AUTOINCREMENT` id
   (текущий `MAX(id)=198352`, но `COUNT=72328`).
2. Phase 2 encode (`archive.db.before_phase2_encode` — 44 МБ snapshot
   от 17.04.2026) населил `vec_chunks` под **старый** rowid-набор.
3. FTS5 пересобран Wave 29 repair скриптом — он вытащил content='chunks'
   и синхронизировался с новыми id (FTS orphaned = 0).
4. `vec_chunks` **не был пересобран** — orphan rate остался
   99.7%.

Побочная причина, почему это не заметили раньше:
`_vector_search` в `src/core/memory_retrieval.py:547-569` **до сих пор
возвращает `[]`** (Phase 2 stub, полноценный vector path не активирован).
Поэтому retrieval живёт на FTS5 и никто не страдал от «битого» vec.

## Fix plan

### Minimal (рекомендуется, ~5 мин wall-clock, 0 LOC изменений)

Запустить существующий `scripts/repair_sqlite_vec.py --skip-fts`:

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
venv/bin/python scripts/repair_sqlite_vec.py --skip-fts
```

Что произойдёт:
- backup → `archive.db.pre-repair-YYYYMMDD_HHMMSS` (~506 МБ)
- DROP + CREATE `vec_chunks USING vec0(vector float[256] distance_metric=cosine)`
- re-encode всех 72328 chunks через Model2Vec → `INSERT rowid = chunks.id`
- Результат: `vec.rowid ∩ chunks.id = 72328` (100%).

Risk: низкий — repair идемпотентен, backup автоматический, `--dry-run`
показал только vec-path дефект, FTS не трогаем.

### Полный (рекомендуется отдельным PR, ~40 LOC)

1. **Fix ложной диагностики** в `scripts/repair_sqlite_vec.py:99-106`
   и `src/core/memory_doctor.py` / `src/core/health_deep_collector.py`:
   заменить JOIN `vr.id = c.id` → прямой JOIN
   `vec_chunks v JOIN chunks c ON c.id = v.rowid`.
2. **Активация `_vector_search`** в `src/core/memory_retrieval.py:547-569`
   (Phase 2 TODO) — сейчас stub, после repair есть 100% покрытие и можно
   включить hybrid BM25+vector rerank.
3. **CI-guard**: в `tests/integration/test_memory_layer_full_chain.py`
   добавить assert `vec_orphan_rate < 0.01`, чтобы десинхронизация ловилась
   сразу при пересоздании chunks.

## Рекомендация

Выполнить **minimal** шаг (команда repair) — это штатный recovery-путь,
уже предусмотренный Wave 29. Полный план (п.1-3) оформить отдельным PR
после того, как `_vector_search` будет активирован (это блокер Phase 2 retrieval,
а не sqlite-vec).
