# Memory Layer — User Guide

Hybrid retrieval поверх Telegram-архива: FTS5 BM25 + Model2Vec embeddings в SQLite,
через `sqlite-vec`. Слиты Reciprocal Rank Fusion (k=60) с adaptive decay. Доступ
owner-only через userbot команды `!archive` / `!memory stats`.

Этот guide — минимальный путь от нулевого состояния до работающего поиска
по собственным сообщениям. Предполагается что Track E уже в `main`.

---

## 0. Установка зависимостей

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
venv/bin/pip install -r requirements.txt
```

Три ключевых новых пакета: `sqlite-vec`, `model2vec`, `pymorphy3`.

Smoke-check что всё подгружается:

```bash
venv/bin/python -c "
import sqlite_vec, model2vec
print('sqlite-vec', sqlite_vec.__version__ if hasattr(sqlite_vec, '__version__') else 'loaded')
print('model2vec OK')
"
```

---

## 1. Telegram Export из p0lrd

Экспорт делается из **основного аккаунта** (`p0lrd`) в Telegram Desktop.
`yung_nagato` — это Pyrogram-session Краба, не клиент, экспорт из него не идёт.

**Шаги в Telegram Desktop:**

1. Settings → Advanced → **Export Telegram data**
2. Что включить:
   - ✅ Personal chats (если нужны DM)
   - ✅ Private groups
   - ✅ Private channels you created / joined
3. **Media — выключить все галочки** (фото, видео, voice, stickers):
   индексируем только текст, media даёт +10× объём без пользы для retrieval.
4. Format: **Machine-readable JSON**
5. Period: **All time** (можно сузить если нужно)
6. Download path: `~/Downloads/tg_export_for_krab/p0lrd_whitelist/`

Итог — папка с файлом `result.json` (текст-only, обычно 5–50 MB на 10–100K сообщений).

> **Блок 24h после логина**: Telegram блокирует Export на 24 часа после свежего
> логина аккаунта в Desktop как anti-abuse. Если видишь ошибку «Please try later» —
> просто подожди. На уже залогиненном `p0lrd` блока нет.

---

## 2. Whitelist — что индексировать

Конфиг: `~/.openclaw/krab_memory/whitelist.json`.

**Privacy-by-default**: если файла нет или он пуст — НИ ОДИН чат не
индексируется. Нужно явно разрешить.

Пример минимального whitelist:

```json
{
  "allow_all": false,
  "allow": {
    "ids": ["-1003703978531"],
    "title_regex": ["Krab Swarm", "How2AI", "Track [BCD]"]
  },
  "deny": {
    "title_regex": ["(?i)family", "wallet", "personal", "NDA"]
  }
}
```

Правила:

- `allow.ids` — точные chat_id (Telegram негативные id для групп/каналов).
- `allow.title_regex` — регулярки по названию чата (case-insensitive).
- `deny.*` — всегда перекрывает allow (explicit exclusion выигрывает).
- `allow_all: true` + `deny.*` — разрешить всё кроме deny-list (не рекомендуется для первой индексации).

Проверка что конфиг валиден без реального запуска bootstrap:

```bash
venv/bin/python -c "
from pathlib import Path
from src.core.memory_whitelist import MemoryWhitelist
wl = MemoryWhitelist()
print('allow_ids:', sorted(wl.config.allow_ids))
print('allow_patterns:', [p.pattern for p in wl.config.allow_title_regex])
print('deny_patterns:', [p.pattern for p in wl.config.deny_title_regex])
"
```

После первого запуска bootstrap permissions выставляются автоматически:
`chmod 600` на файл, `chmod 700` на директорию.

---

## 3. Bootstrap — разовая индексация

Dry-run сначала (безопасно, в БД не пишет, показывает что будет):

```bash
venv/bin/python scripts/bootstrap_memory.py --dry-run
```

Вывод покажет:
- Сколько сообщений прочитано / пропущено (service, empty, media-only)
- Сколько чатов прошли whitelist / отброшены (с reason'ами)
- Сколько chunks будет создано
- Сколько PII redactions по категориям (card, phone, email, crypto_*)
- Первые 10 chunks с уже применёнными `[REDACTED:*]` плейсхолдерами

Если выглядит разумно — production run:

```bash
venv/bin/python scripts/bootstrap_memory.py
```

Полезные флаги:

| Флаг | Когда |
|------|-------|
| `--limit 1000` | Smoke-test на первой тысяче сообщений |
| `--verbose` | Подробный лог каждого batch'а |
| `--export PATH` | Если JSON в другой папке |
| `--db PATH` | Если хочешь отдельный `archive.db` (dev) |
| `--allow-all` | **DEV ONLY**, обходит whitelist |

Идемпотентно: повторный запуск на том же JSON не дублирует. Для чанков —
delete-then-insert per chat.

---

## 4. Embedder — векторный слой

Bootstrap наполняет FTS5 (keyword search работает сразу). Векторный путь
требует отдельного прогона Model2Vec:

```bash
venv/bin/python -c "
from src.core.memory_embedder import MemoryEmbedder
stats = MemoryEmbedder().embed_all_unindexed()
print(stats)
"
```

Первый запуск скачает `minishlab/M2V_multilingual_output` (~45 MB, ~2 мин через
HuggingFace). Дальше — мгновенно. Batch=1024 эмбедится за ~22ms на M4 Max.

Идемпотентно: повторный вызов найдёт только неиндексированные chunks (через
`LEFT JOIN vec_chunks WHERE v.rowid IS NULL`).

---

## 5. Использование в Telegram

Команды **owner-only** — вызываются из любого чата с правами owner.

### `!archive <query>`

Гибридный поиск. Примеры:

```
!archive dashboard redesign
```
Найдёт chunks где встречается хотя бы одно из «dashboard»/«redesign» через FTS5,
плюс семантически близкие через векторы. RRF объединит ранжирования.

```
!archive что раньше обсуждали про deployment
```
Слова «раньше», «в прошлом году», «в 2023» → auto-detect **historical** mode →
decay отключается (старые сообщения не понижаются в выдаче).

```
!archive на этой неделе про translator
```
«Сейчас», «today», «на этой неделе», «yesterday» → **recent** mode → aggressive
decay (старые сообщения сильно понижаются).

Формат ответа (MarkdownV2):

```
🧠 Memory archive · запрос: «dashboard redesign»

1. 2026-04-01 · chat -1003703978531 · score 1.00
   обсуждали dashboard redesign mobile...
   ⤴ кофе в чате про frontend
   ⤵ продолжили про a11y

2. 2026-04-02 · chat -1003703978531 · score 0.73
   dashboard metrics и layout grid...
```

### `!arc <query>`

Алиас для `!archive`.

### `!memory stats`

Быстрая сводка по индексу:

```
🧠 Memory Layer · статистика

• Chats indexed: 7
• Messages:      12 438
• Chunks:        1 842
• Vectors:       1 842
• DB size:       85.3 MB
```

**Как читать:**

- `Chats indexed` — кол-во чатов прошедших whitelist и попавших в БД.
- `Messages` — сырые сообщения (после PII redaction).
- `Chunks` — группы связанных сообщений (reply-chain или ±5 мин). Именно по
  chunk'ам работает поиск; соотношение messages/chunks обычно 5–10.
- `Vectors` — сколько chunks уже проэмбеждено. Если < chunks — запусти
  `embed_all_unindexed()` (см. выше).
- `Vectors: (sqlite-vec не подключён)` — extension не подгрузилась в runtime.
  Проверь `pip install sqlite-vec` в venv Краба.
- `DB size` — физический размер `archive.db` (FTS5 + vec_chunks inclusive).

---

## 6. Чего НЕ делает Memory Layer

- **Не индексирует инкрементально** — новые сообщения Telegram не добавляются
  автоматически. Нужен явный повторный `bootstrap_memory.py` (идемпотентен)
  или Phase 4 воркер (TBD в Session 9).
- **Не переводит запросы между языками** — индекс multilingual, запрос
  работает на любом, но тексты разных языков выдают по-своему. Cross-lingual
  similarity Model2Vec ≈ 0.93 на парах ru-en.
- **Не сохраняет raw text** — только `text_redacted` после PII scrubber.
  Если нужно восстановить оригинал — используй сам Telegram или свежий
  Export JSON.
- **Не имеет GUI** — работа через userbot команды и CLI. Dashboard-страница
  Memory Layer — задача Session 9+ (делегируется Gemini).

---

## 7. Troubleshooting

| Симптом | Причина | Что делать |
|---------|---------|------------|
| `!memory stats` → `chats=0` после bootstrap | Whitelist пустой или все чаты в deny | Проверь `whitelist.json`, запусти с `--verbose` |
| `Vectors: (sqlite-vec не подключён)` | `sqlite-vec` не установлен в текущий venv | `venv/bin/pip install sqlite-vec>=0.1.6` |
| `Vectors: 0` | Embedder ещё не запускали | Прогон `MemoryEmbedder().embed_all_unindexed()` |
| `!archive` → «ничего не найдено» | Проверь `!memory stats`, возможно БД пустая или запрос не совпадает ни с одним chunk | Попробуй шире/общее слово |
| Bootstrap падает на больших JSON | Лимит памяти (stdlib `json.load`) | Предел ~500MB JSON, выше — нужно переходить на `ijson` (не MVP) |
| Model2Vec качает 124s при первом запуске | Cold HF download | Одноразово, кэшируется в `~/.cache/huggingface/` |

---

## 8. Дальше

- **Session 9 TBD**: Phase 4 incremental indexer worker (`ingest_message(msg)`
  hook в `userbot_bridge.py`), watermark invalidation на edit.
- **Production-tuning после первого bootstrap** на реальных данных: chunking
  параметры, decay константы, per-chat namespaces для «горячих» чатов.
- **Query expansion на `pymorphy3`** (Phase 2.5): лемматизация русских
  запросов перед FTS5 MATCH для recall по морфологическим формам.

---

*Memory Layer — Track E, merged in Session 8. Maintainer: owner of Krab.*
