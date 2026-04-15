# Track E — Memory Layer для Краба

**Статус:** planning → Phase 0 in progress
**Ветка:** `claude/memory-layer`
**Worktree:** `.claude/worktrees/memory-layer`
**Координация:** основной чат работает на Track B (Dashboard V4, Phase 7) — не трогает memory-* файлы.

## Цель

Двухуровневая (на самом деле трёх-) система retrieval поверх персонального Telegram-архива:

1. **Лексический слой** — SQLite FTS5 (BM25).
2. **Семантический слой** — Model2Vec эмбеддинги (256-dim, CPU-only) в `sqlite-vec`.
3. **Кураторский слой** — существующие `.remember/*.md`, `MEMORY.md` (не трогаем).

Гибридное ранжирование — Reciprocal Rank Fusion, `k=60`. Для Краба это отдельный **archive layer**, поверх уже существующего **facts layer** (`src/memory_engine.py` на ChromaDB, не трогаем).

## Privacy-first contract

Источник данных **только yung_nagato архив** в Фазе 1 (bot-аккаунт Краба — но экспорт идёт из Telegram Desktop `p0lrd` с whitelist, поскольку yung_nagato это Pyrogram-session, не самостоятельный клиент).

Whitelist чатов для Фазы 1 (решение owner'а):

| Включить | Исключить |
|----------|-----------|
| Dev chats: How2AI, 🐝 Krab Swarm (все топики), Track B/C/D чаты | Family DM |
| DM: yung_nagato, Дашко (от краба), Power Of The Mind | Финансовые / crypto wallet DM |
| | NDA рабочие группы |
| | Старые личные переписки |

Mandatory для любой индексации:

1. **PII scrubber** (`src/core/memory_pii_redactor.py`) — regex redaction перед insert'ом в БД:
   - Bank cards (Visa/MC/Amex/Maestro), Luhn check
   - Crypto addresses: BTC (Base58/Bech32), ETH (0x...), SOL (Base58), TRX (T...)
   - API keys: OpenAI `sk-...`, Anthropic `sk-ant-...`, Google `AIza...`, generic 40+ char hex/base64
   - Phone numbers (E.164, RU/international)
   - Emails (others, не owner)
   - Passport/ID numbers (RU 4+6 digits, generic patterns)

2. **Whitelist-based ingestion** (`src/core/memory_whitelist.py`) — что индексировать.
3. **Encrypt at rest** — filesystem-level: `chmod 600 archive.db`, `chmod 700` директория, поверх FileVault.
4. **Access control** — `!archive` owner-only scope, никаких broadcast/tool invocations.
5. **Raw text не хранится** — только `text_redacted` после PII scrubber.

## Стек (принят)

| Компонент | Версия/модель | Назначение |
|-----------|---------------|------------|
| SQLite FTS5 | builtin | BM25 keyword search |
| sqlite-vec | latest (`pip install sqlite-vec`) | vector search extension, mmap lazy load |
| Model2Vec | `minishlab/M2V_multilingual_output`, 256-dim, ~45MB | static embeddings, CPU-only |
| pymorphy3 | latest | RU лемматизация (Phase 2, optional) |
| Reciprocal Rank Fusion | `k=60` | fusion FTS5 ∪ vec results |
| Adaptive decay | `1/(1 + α·age_days)`, mode ∈ {none, gentle, aggressive, auto} | recency weighting |

## Файловая структура (только создавать новые)

```
src/core/
  memory_archive.py          — главный indexer + writer
  memory_retrieval.py        — HybridRetriever + RRF + decay
  memory_chunking.py         — reply_to + time-gap chunks
  memory_pii_redactor.py     — regex scrubber  ← Phase 0 deliverable
  memory_whitelist.py        — allowed chats config
  memory_indexer_worker.py   — async bootstrap worker (в aux_tasks)
src/handlers/
  memory_commands.py         — !archive, !memory stats, !arc alias
scripts/
  bootstrap_memory.py        — one-shot CLI для разового прогона JSON экспорта
tests/unit/
  test_memory_pii_redactor.py
  test_memory_chunking.py
  test_memory_retrieval.py
  test_memory_whitelist.py
docs/
  PLAN_TRACK_E_MEMORY_LAYER.md  ← этот файл
```

## ⛔ Файлы, которые не трогаем

- `src/memory_engine.py` — ChromaDB facts layer (команды `!remember`/`!recall`)
- `src/handlers/command_handlers.py` — 175+ команд основного чата
- `src/userbot/llm_flow.py` — context injection делает Track B после MVP
- `src/userbot_bridge.py` — hook на incoming message добавит Track B, вызовет наш `indexer.ingest_message(msg)`
- `src/web/v4/*`, `src/modules/web_app.py` — Dashboard V4 основного чата
- `requirements.txt` — менять буду, но только добавляя (sqlite-vec, model2vec, pymorphy3), с явным commit

## API контракт (для Track B интеграций)

```python
# src/core/memory_retrieval.py

@dataclass(frozen=True)
class SearchResult:
    message_id: int
    chat_id: int
    chat_title: str | None
    sender_id: int | None
    timestamp: datetime
    text_redacted: str              # raw НЕ отдаём
    score: float                    # fused RRF score × decay
    context_before: list[str]       # до N соседних chunks
    context_after: list[str]


class HybridRetriever:
    def search(
        self,
        query: str,
        chat_id: int | None = None,
        top_k: int = 10,
        context: int = 2,
        decay_mode: Literal["none", "gentle", "aggressive", "auto"] = "auto",
        owner_only: bool = True,
    ) -> list[SearchResult]: ...
```

`owner_only=True` по умолчанию — доступ только из owner-scope userbot команд. Track B при LLM context injection будет вызывать с теми же дефолтами.

## Фазы

### Phase 0 — инфра и privacy primitives (0.5 дня, в работе)

- [x] worktree `.claude/worktrees/memory-layer` на ветке `claude/memory-layer`
- [x] `docs/PLAN_TRACK_E_MEMORY_LAYER.md`
- [ ] `src/core/memory_pii_redactor.py` + `__main__` self-check
- [ ] `tests/unit/test_memory_pii_redactor.py` с синтетическими векторами
- [ ] initial commit

### Phase 1 — индексация (3 дня)

- `scripts/bootstrap_memory.py` — парсер Telegram Export JSON (`~/Downloads/tg_export_for_krab/p0lrd_whitelist/result.json`)
- `src/core/memory_whitelist.py` — allow/deny лист чатов
- `src/core/memory_chunking.py` — reply_to chains + time-gap fallback (>5 мин = новый chunk)
- `src/core/memory_archive.py` — схема БД, batch insert (размер 1024 сообщений/batch), PII scrubber в pipeline
- Model2Vec загрузка с lazy-init, 256-dim эмбеддинги
- `sqlite-vec` virtual table `vec_messages` для векторов
- Dry-run: 100 сообщений, показать redacted output без секретов

### Phase 2 — retrieval API (1 день)

- `src/core/memory_retrieval.py` — `HybridRetriever.search()`
- FTS5 BM25 + vector similarity → RRF (k=60)
- Adaptive decay через `detect_decay_mode(query)`
- Expand context (±N соседних chunks) с метаданными

### Phase 3 — userbot команды (2 дня)

- `src/handlers/memory_commands.py` — `!archive`, `!memory stats`, `!arc` alias
- Owner-only ACL через существующий `src/core/access_control.py`
- `!memory stats` — count/size/coverage per chat
- `!archive <q>` — текст редактед, показываем score, timestamp, chat_title

### Phase 4 — incremental indexer (1 день)

- `src/core/memory_indexer_worker.py` — async worker в `aux_tasks`
- `indexer.ingest_message(msg)` — API для Track B hook
- Watermark таблица, инвалидация эмбеддингов на edit

### Privacy hardening (2 дня, параллельно с Phase 1-3)

- `chmod 600` на `archive.db`, `chmod 700` на `~/.openclaw/krab_memory/`
- Unit-тест: попытка чтения без owner scope → raises
- Dry-run tool: `scripts/dry_run_redaction.py <input.txt>` — показывает что бы заредактилось

## Decay — финальная спецификация

```python
DECAY_MODES = {
    "none":       lambda age: 1.0,
    "gentle":     lambda age: 1 / (1 + 0.01 * age),  # halflife ~100 days
    "aggressive": lambda age: 1 / (1 + 0.05 * age),  # halflife ~20 days
}

HISTORICAL_MARKERS_RU = [
    "раньше", "тогда", "в прошлом", "год назад", "два года",
    "в 2023", "в 2024", "когда-то", "давно", "ранее",
]
HISTORICAL_MARKERS_EN = [
    "before", "ago", "last year", "previously", "earlier",
    "in 2023", "in 2024", "back in", "long ago",
]
RECENT_MARKERS = ["сейчас", "today", "на этой неделе", "this week"]

def detect_decay_mode(query: str) -> str:
    q = query.lower()
    if any(m in q for m in HISTORICAL_MARKERS_RU + HISTORICAL_MARKERS_EN):
        return "none"
    if any(m in q for m in RECENT_MARKERS):
        return "aggressive"
    return "gentle"
```

## Coordination points (сигналы для основного чата)

| Этап | Сигнал |
|------|--------|
| End Phase 0 | commit на `claude/memory-layer` с названием `feat(memory): phase 0 — PII redactor + plan` |
| End Phase 1 dry-run | commit + комментарий в чат: объём, примеры redacted |
| End Phase 2 | commit + первые примеры `!archive` результатов |
| End Phase 4 | PR на main с privacy checklist |

## Замечания по baseline

На момент создания worktree в `main` был untracked `src/core/silence_schedule.py` при закоммиченном импорте в `src/userbot_bridge.py`. Результат: baseline pytest в worktree падает на collection через `tests/unit/conftest.py` → `KraabUserbot` → `silence_schedule` missing.

Не блокер для Track E: модули Memory Layer самостоятельны, тесты PII redactor запускаем без conftest (standalone subprocess). Основному чату стоит закоммитить `silence_schedule.py` + `bookmark_service.py` + остальные untracked новые модули, чтобы baseline восстановился.

## Risk register

| Риск | Митигация |
|------|-----------|
| Коллизия с `!recall` | Взяли namespace `!archive`. Решено в Q1. |
| sqlite-vec brute-force на 100K+ | Измерить на boostrap dry-run. Если >200мс — переехать на `usearch` + mmap. |
| Model2Vec качество на русском | multilingual модель покрывает. pymorphy3 lemma — Phase 2 если recall низкий. |
| PII regex ложные позитивы | Unit-тесты на golden set. Коэффициент over-redact acceptable, под-redact — нет. |
| Инкрементальная индексация нагрузка | Batching + debounce, worker с priority-throttle. Phase 4. |
| Export JSON слишком большой | Streaming JSON parser (`ijson`), не full load. |
| Baseline pytest сломан | Не блокер для Phase 0. Координируем с основным чатом. |

## Open questions (не блокеры)

- Нужно ли индексировать стикеры / caption у media? (Phase 1 решение)
- Per-chat физические отдельные FTS для "горячих" чатов — в Phase 2 или позже?
- Multi-lingual query — пробовать оба языка параллельно или определять язык query → использовать ту же таблицу?

---

*Этот план — living document. Обновляется в конце каждой фазы.*
