# Memory Phase 2 Activation Plan

**Status:** Diagnosed, partial mini-win applied (on-the-fly cosine MMR), full Phase 2 pending.

## Real root cause (не Model2Vec!)

Agent investigation (2026-04-24, sonnet) показала что **Model2Vec полностью рабочий**:
- Пакет установлен в venv ✓
- Модель `minishlab/M2V_multilingual_output` в HF cache (`~/.cache/huggingface/hub/`) ✓
- Lazy loader `HybridRetriever._ensure_model()` работает (`memory_retrieval.py:474`) ✓

**Настоящий blocker — 2 места в `src/core/memory_retrieval.py`:**

1. **`_vector_search()` line 547-569 — STUB, возвращает `[]`**
   ```python
   # Пока no-op … После Phase 2 индексации
   return []
   ```
   → Vector retrieval в prod вообще не работает. RRF делается только по FTS5.

2. **`_materialize_results()` line 635-655 — всегда Jaccard**
   ```python
   if mmr_is_enabled() and len(final) > 1:
       ordered_ids = mmr_rerank_texts(  # ← всегда текстовый
           query="",  # ← пустая строка!
           ...
       )
   ```
   → Даже если cosine был бы вызван, query embedding бесполезен (empty).

## Tests MMR (7 тестов)

| Type | Tests | Status |
|---|---|---|
| Cosine path (`mmr_rerank`) | 3 | passing, но код не вызывается в prod |
| Jaccard path (`mmr_rerank_texts`) | 2 | passing + используется в prod |
| Config (env flags) | 2 | passing |

Cosine tests — dead code coverage (unit passes, prod never calls).

## Full Phase 2 (~60-80 LOC, требует Krab restart)

1. **Реализовать `_vector_search()`:**
   - `vec_chunks MATCH serialize_f32(q_vec) ORDER BY distance LIMIT N`
   - Fallback на empty если vec0 malformed (sqlite-vec Session 13 blocker тоже)

2. **Bootstrap hook для indexer:**
   - В `userbot_bridge._sync_scheduler_runtime`: проверить, запущен ли `MemoryEmbedder.embed_all_unindexed()`
   - Если нет — запустить background task один раз при старте

3. **Cosine MMR в `_materialize_results()`:**
   - Сохранять `self._last_query` в `search()` для использования в MMR
   - Передавать `self._model` + query + doc_texts в `mmr_rerank`
   - Debug-log `mmr_mode=cosine|jaccard`

## Mini-win (25-30 LOC, применён сегодня)

В `_materialize_results()` — если модель доступна и query не пустой, encode on-the-fly и вызвать cosine MMR. Иначе fallback на Jaccard (существующий код).

Это НЕ решает Phase 2 blocker (vector search всё ещё stub), но улучшает diversity ranking когда модель есть. Существующие тесты не сломаны.

## Dependencies

- sqlite-vec malformed (Session 13) — блокирует `vec_chunks` как virtual table
- MemoryEmbedder embedder worker — существует в `src/core/memory_indexer_worker.py`, но вероятно не заполняет `vec_chunks` (либо другое хранилище)

## Next steps

- [x] docs/MEMORY_PHASE2_ACTIVATION.md (этот файл)
- [x] Mini-win: cosine MMR когда model available
- [ ] Полная Phase 2: _vector_search impl + embedder bootstrap + wiring (отдельная сессия)
- [ ] sqlite-vec malformed fix (agent `afd5cbbc…` в работе)
