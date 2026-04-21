# Memory Doctor — Post-Session 17 Diagnostics

**Timestamp:** 2026-04-21 06:44:59  
**Script:** `scripts/memory_doctor.command` (diagnose only, no `--fix`)  
**Operator:** Automated post-session check

---

## Check Results

| # | Check | Status | Details |
|---|-------|--------|---------|
| 1 | archive.db — наличие и размер | OK | 472.3 МБ (495 312 896 байт) |
| 2 | SQLite integrity_check | OK | integrity_check: OK |
| 3 | Счётчики messages / chats / chunks | OK | 752 712 сообщений, 878 чатов, 72 258 chunks |
| 4 | Encoded ratio (embedded / total) | OK | 100.0% (72 258 / 72 258) |
| 5 | Indexer queue depth (panel :8080) | OK | is_running=True, queue_size=0 |
| 6 | MCP memory_search reachability (:8011) | OK | yung-nagato SSE отвечает |
| 7 | Топ чатов по объёму | OK | Топ чат: chat_id=1467625424 (260 676 msg) |

**Overall: ALL GREEN — Memory Layer здоров, проблем не обнаружено.**

---

## Key Numbers

- **DB size:** 472.3 МБ (значительный рост vs Session 13: было 51 МБ — вероятно, продолжается накопление истории)
- **Messages:** 752 712 (Session 13 baseline: ~43 000 — это 17x рост, либо другая точка отсчёта)
- **Chunks:** 72 258 (все проиндексированы, 100% encoded ratio)
- **Indexer:** работает, очередь пустая
- **MCP endpoint:** доступен

---

## Top 5 Chats by Volume

| chat_id | messages |
|---------|----------|
| 1467625424 | 260 676 |
| 785599281 | 91 577 |
| 1587432709 | 30 922 |
| 875446785 | 21 057 |
| 1358678329 | 19 847 |

---

## Concerns

**Потенциальная:** DB выросла до 472 МБ. Это нормально для production с 750k+ сообщений, но стоит мониторить — при дальнейшем росте может потребоваться VACUUM или партиционирование.

**`processed_total: 0`** у indexer — означает, что с момента последнего старта новые чанки не индексировались (либо счётчик сбрасывается при рестарте). Не критично при 100% encoded ratio.

---

## Recommendations

1. **Никаких срочных действий не требуется** — все проверки зелёные.
2. **Мониторинг размера БД:** при пересечении 1 ГБ запустить `VACUUM` (или `memory_doctor.command --fix`).
3. **vec_chunks consistency** — скрипт не выполнял этот чек в текущей версии; при следующем запуске убедиться, что проверка дублей включена.
4. **Следующий плановый запуск:** post-Session 18.
