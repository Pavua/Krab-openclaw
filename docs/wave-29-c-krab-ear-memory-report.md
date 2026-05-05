# Wave 29-C: Krab Ear Memory Investigation Report

**Дата:** 2026-05-05  
**Версия анализа:** 1.0  
**Инструмент:** psutil + coexistence_monitor.log (50+ снимков)  
**Статус:** READ-ONLY (код не менялся)

---

## 1. Обнаруженные процессы

### Стабильные 4 процесса (постоянно живы)

| PID | Компонент | RSS (пик) | RSS (idle) | Uptime | Описание |
|-----|-----------|-----------|------------|--------|----------|
| 51323 | `KrabEarAgent` (app bundle) | 59 MB | 53 MB | ~38 мин | Swift агент из `Krab Ear.app`, запущен пользователем |
| 68157 | `KrabEarAgent` (launchd) | 42 MB | 42 MB | ~21 мин | Swift агент из `native/runtime/`, запущен launchd |
| 68194 | `service.py` (Python 3.14) | **2024 MB** | **448 MB** | ~21 мин | IPC backend — основной Python сервис |
| 68274 | `gigaam_worker.py` (Python 3.12) | **1622 MB** | **1206 MB** | ~21 мин | GigaAM subprocess worker (отдельный venv) |

**Итого реальных процессов Krab Ear: 4**  
**Combined RSS (peak): ~3.75 GB**  
**Combined RSS (idle после page-out): ~1.75 GB**

### Пятый и далее процессы — FALSE POSITIVES

Coexistence monitor сообщал о 5-10 процессах с суммарным RSS до 5.05 GB.

Это **ложные срабатывания**: `swift-frontend` и `swift-driver` при сборке `KrabEarAgent`
попадают в паттерн `EAR_PATTERNS` (`'KrabEar'`), потому что их cmdline содержит путь
`/Krab Ear/native/KrabEarAgent/Sources/...`.

За последние 50 снимков монитора зафиксировано **98 ephemeral PIDs** — это и есть
Swift компилятор (пики RSS у swift-frontend до 809 MB за одну компиляцию).

---

## 2. Роль каждого процесса

### PID 51323 — KrabEarAgent (Krab Ear.app bundle)
**Роль:** UI Swift агент, запущенный пользователем через `.app`.  
**RSS:** 53–59 MB — нормально для Swift GUI приложения.  
**Проблема:** Параллельно с launchd-версией (PID 68157) — двойной экземпляр.

### PID 68157 — KrabEarAgent (launchd, `--launched-by-launchd`)
**Роль:** Тот же Swift агент, запущен через `ai.krab.ear.backend` LaunchAgent (или аналогичный).  
**RSS:** 41–42 MB — нормально.  
**Бинарник:** `native/runtime/KrabEarAgent` (5.14 MB) — почти идентичен бандл-версии (5.14 MB).  
**Проблема:** Два экземпляра агента работают одновременно (см. раздел 4).

### PID 68194 — service.py (Python 3.14, `~/.venv_krab_ear`)
**Роль:** Основной IPC backend — 241 JSON-RPC метод (записи, транскрипция, история, перевод).  
**RSS:** 448–2024 MB.  
**Диапазон объясняется:** macOS compressed memory + реальная загрузка всех модулей при старте.  
`service.py` импортирует **>80 компонентов** при запуске (`ModelCacheManager`, `HotwordDetector`,
`SemanticSearcher`, `TranscriptionQueue` и т.д.) — всё это занимает память.  
При idle macOS вытесняет холодные страницы → RSS падает до ~450 MB.  
**Несоответствие venv:** plist ссылается на `.venv_krab_ear/bin/python3` (Python 3.14),
а в ps aux виден `/opt/homebrew/Cellar/python@3.14/...` — это одно и то же (симлинк).

### PID 68274 — gigaam_worker.py (Python 3.12, `~/.venv_krab_ear_gigaam`)
**Роль:** Изолированный subprocess для GigaAM RNNT STT модели. Запущен `service.py`.  
**RSS:** 1206–1622 MB.  
**Причина высокого RSS:** задокументирована в `docs/audit/gigaam-worker-memory-2026-05-05.md`:
- GigaAM RNNT v2 веса: ~400–600 MB
- PyTorch MPS Metal buffer pool после warm-up: ~700–1000 MB
- Итого: 1.2–1.6 GB — **архитектурный baseline, не утечка**

---

## 3. Таблица RSS

| Процесс | RSS min | RSS max | Δ за 30s | Тип |
|---------|---------|---------|----------|-----|
| KrabEarAgent (app) | 53 MB | 59 MB | -5 MB | macOS page-out |
| KrabEarAgent (launchd) | 41 MB | 42 MB | -0.2 MB | стабильный |
| service.py | 448 MB | **2024 MB** | -14 MB | macOS page-out |
| gigaam_worker | 1206 MB | **1622 MB** | **-275 MB** | macOS page-out |
| **ИТОГО (4 реальных)** | **1748 MB** | **3747 MB** | **-295 MB** | |

За 30-секундный тест суммарный RSS **упал на 295 MB** без какого-либо вмешательства —
это macOS memory compressor, не утечка.

---

## 4. Ожидаемая архитектура vs Наблюдаемая

### Ожидаемая (из CLAUDE.md проекта):
```
KrabEarAgent (Swift) ←IPC→ Python Backend (service.py)
                              └─ gigaam_worker.py (subprocess)
```
2–3 процесса всего.

### Наблюдаемая:
```
KrabEarAgent (app bundle, PID 51323)    ← лишний!
KrabEarAgent (launchd, PID 68157)       ← правильный
service.py (Python 3.14, PID 68194)     ← OK
gigaam_worker.py (Python 3.12, PID 68274)  ← OK (дочерний от service.py)
```

**Аномалия 1:** Два экземпляра Swift агента.  
PID 51323 запущен **за 17 минут до** PID 68157 (36m vs 21m uptime при замере).
Вероятно: пользователь открыл `Krab Ear.app` вручную, затем launchd тоже запустил агент.  
Оба агента работают параллельно — это дублирование, 51323 не нужен при живом 68157.

**Аномалия 2:** Мониторинг ловит Swift компилятор как "Krab Ear процессы".  
Паттерн `'KrabEar'` срабатывает на путь в аргументах `swift-frontend`.

---

## 5. Вердикт по Memory Leak

**ВЕРДИКТ: Memory leak НЕ обнаружен.**

### Доказательства:

1. **RSS за 30 секунд уменьшился на 295 MB** без каких-либо действий.
   Реальная утечка даёт монотонный рост, не снижение.

2. **gigaam_worker RSS варьируется 1.2–1.6 GB** — это MPS Metal buffer pool
   (documented in `gigaam-worker-memory-2026-05-05.md`). После загрузки модели
   pool стабилизируется. Это **architectural constant**, не ratchet-leak.

3. **service.py RSS варьируется 448–2024 MB** — широкий диапазон объясняется
   macOS memory pressure. При активной работе все модули в RAM, при idle
   macOS вытесняет cold pages в compressed swap. Swap = 7–16 GB (видно в логе) —
   macOS агрессивно использует compressed swap на Apple Silicon, это нормально.

4. **Динамика из лога (50+ снимков):** ear_rss колеблется 0.18–5.05 GB,
   но экстремальные значения объясняются либо Swift компиляцией (false positive),
   либо активной транскрипцией. Нет монотонного роста между снимками.

---

## 6. OOM Events — Возможная причина

Несмотря на отсутствие leak, **3 reboot в день реальны** по другим причинам:

### 6.1 Пиковое потребление при транскрипции
При активной записи:
- service.py: ~2.0 GB
- gigaam_worker: ~1.6 GB
- KrabEarAgents (x2): ~0.1 GB
- **Krab userbot**: ~0.5–1.5 GB (не был активен во время замера)
- **LM Studio + модели** (если загружен): 4–12 GB

Суммарный пик при полной нагрузке: **8–16 GB RSS + 8–15 GB swap**.  
На M4 Max 36 GB это создаёт реальный memory pressure и потенциальный OOM.

### 6.2 Два экземпляра KrabEarAgent
Дублирование агента добавляет ~100 MB и создаёт race condition на Unix socket IPC.

### 6.3 Swift компиляция во время работы
Каждый `swift build` добавляет 800–1000 MB RSS на время компиляции (swift-frontend).
Если компиляция идёт параллельно с транскрипцией — пиковое потребление резко растёт.

---

## 7. Рекомендации

### 7.1 СРОЧНО: Исправить coexistence_monitor.py (false positives)
**Проблема:** Паттерн `'KrabEar'` матчит Swift компилятор.  
**Фикс:** Добавить фильтр на `swift` в exclude list или уточнить паттерн:
```python
# Текущий паттерн (ловит swift-frontend):
EAR_PATTERNS = ["KrabEar", "krab.ear", "krab_ear"]

# Предлагаемый фикс — исключать Swift toolchain:
EXCLUDE_PATTERNS = ["swift-frontend", "swift-driver", "swiftc", "Xcode"]
```
Без этого мониторинг даёт ложные алерты и завышенный ear_rss.

### 7.2 СРОЧНО: Устранить двойной KrabEarAgent
PID 51323 (`Krab Ear.app`) и PID 68157 (launchd) работают параллельно.  
**Действие:** Не открывать `Krab Ear.app` вручную если launchd уже держит агент.  
Или отключить launchd autostart и использовать только app bundle.

### 7.3 АРХИТЕКТУРНО: gigaam_worker baseline 1.2–1.6 GB
Уже задокументировано в `gigaam-worker-memory-2026-05-05.md`. Код имеет:
- `_free_mps_pool()` с `torch.mps.empty_cache()` — уже добавлено
- `gc.collect()` — добавлено
- `KRAB_EAR_DISABLE_MPS_POOL_FREE=1` для A/B тестирования

**Потенциальное снижение:** 300–700 MB при idle если `empty_cache()` вызывается периодически.  
Для OOM protection: рассмотреть subprocess restart каждые N=50 транскрипций.

### 7.4 МОНИТОРИНГ: Добавить baseline в coexistence_monitor
Записать текущие значения как reference baseline для сравнения:
```json
{
  "baseline_date": "2026-05-05",
  "gigaam_worker_idle_rss_mb": 1206,
  "service_idle_rss_mb": 448,
  "combined_stable_4_rss_mb": 1748,
  "combined_stable_4_peak_mb": 3748
}
```

### 7.5 OOM Prevention: координация с LM Studio
Если LM Studio запускает модель параллельно с gigaam_worker — суммарный RSS может
превысить 8 GB. Рекомендуется:
- `LM Studio: ONE AT A TIME` (уже в CLAUDE.md)
- Или отключить gigaam STT при активном LM Studio

---

## 8. Baseline для будущих сравнений

| Метрика | Значение (2026-05-05, idle) |
|---------|-----------------------------|
| Количество реальных процессов | 4 (в норме 3 — без дубля agent) |
| gigaam_worker RSS (idle) | ~1.2 GB |
| service.py RSS (idle) | ~450 MB |
| KrabEarAgents суммарно | ~95 MB |
| Combined RSS (4 proc, idle) | ~1.75 GB |
| Combined RSS (4 proc, peak) | ~3.75 GB |
| Swap при нормальной работе | 7–10 GB (macOS compressed, норма) |
| Swap при stress | 12–16 GB (attention zone) |

---

## Файлы по теме

- `/Users/pablito/Antigravity_AGENTS/Krab Ear/docs/audit/gigaam-worker-memory-2026-05-05.md` — глубокий анализ gigaam_worker
- `/Users/pablito/Antigravity_AGENTS/Krab Ear/KrabEar/core/workers/gigaam_worker.py` — воркер с opt-in tracing
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/krab_ear_coexistence_monitor.py` — монитор (требует фикс false positives)
- `~/.openclaw/krab_runtime_state/coexistence_monitor.log` — история снимков
