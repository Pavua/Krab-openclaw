# Routines Audit — 2026-04-23

Аудит всех 13 автоматических рутин: 5 launchd + 7 Claude Desktop + 1 chado-sync.
Данные собраны 23.04.2026 (~17:30 UTC).

---

## Итоговая таблица

| Name | Type | Loaded | Last fire | Exit | Value | Action |
|------|------|--------|-----------|------|-------|--------|
| ai.krab.leak-monitor | launchd 30m | ✅ active | 2026-04-23 17:25 | 0 | HIGH | KEEP |
| ai.krab.health-watcher | launchd 15m | ✅ active | 2026-04-23 02:40 | **1** (bug) | HIGH | KEEP + BUG FIXED |
| ai.krab.ear-watcher | launchd 15m | ✅ active | n/a (no log) | 0 | LOW | TUNE |
| ai.krab.backend-log-scanner | launchd 4h | ✅ active | 2026-04-23 14:50 | 0 | MEDIUM | KEEP |
| ai.krab.daily-maintenance | launchd 02:07 | ✅ active | 2026-04-23 02:07 | 0 | HIGH | KEEP |
| krab-openclaw-commit-review | Claude Desktop | installed | unknown | — | MEDIUM | VERIFY |
| krab-openclaw-sentry-digest | Claude Desktop | installed | unknown | — | MEDIUM | VERIFY |
| krab-openclaw-lunch-status | Claude Desktop | installed | unknown | — | LOW | TUNE freq |
| krab-openclaw-linear-sync | Claude Desktop | installed | unknown | — | MEDIUM | KEEP |
| krab-openclaw-evening-cleanup | Claude Desktop | installed | unknown | — | MEDIUM | KEEP |
| krab-openclaw-weekly-recap | Claude Desktop | installed | unknown | — | LOW | TUNE |
| krab-openclaw-monthly-arch | Claude Desktop | installed | unknown | — | LOW | TUNE |
| krab-openclaw-chado-sync | Claude Desktop | **NOT installed** | never | — | VERY LOW | REMOVE |

---

## LaunchD рутины (5 штук)

### 1. ai.krab.leak-monitor — KEEP ✅

- **Интервал:** 30 мин (StartInterval=1800)
- **Статус:** active, exit=0, работает без ошибок
- **Последний запуск:** 23.04.2026 17:25 UTC
- **Что делает:** считает openclaw дочерние процессы; если >18 — warning, если >25 — SIGKILL orphans
- **Доказательства ценности:** три warning-записи сегодня (count=20), потом само упало до 12 — leak self-healed. Мониторинг работает. stderr.log пуст (0 байт) — скрипт чистый.
- **Рекомендация:** KEEP, возможно снизить частоту до 1h если нагрузка на диагностику мала.

---

### 2. ai.krab.health-watcher — KEEP + BUG FIXED ✅

- **Интервал:** 15 мин (StartInterval=900)
- **Статус:** active, exit=**1** (ошибочный), крешился каждые 15 мин с `NameError`
- **Последний успешный запуск (лог):** 23.04.2026 02:40 UTC — после этого перестал писать в лог
- **Баг:** `gemini_ok` и `gemini_status` не были определены в `main()`. Комментарий гласил "Gemini quota check removed", но строки status_summary и условие `if not gateway_ok or not gemini_ok:` забыли удалить.
- **stderr:** 69 KB ошибок (`NameError: name 'gemini_ok' is not defined`)
- **ИСПРАВЛЕНО:** удалены orphan-ссылки на `gemini_ok`/`gemini_status` из `main()`. Тест: `python3 scripts/krab_health_watcher.py` → exit 0 ✅
- **Что делает реально:** мониторит Krab panel :8080 и OpenClaw gateway :18789; автокикстарт gateway при 2+ последовательных падениях; проверяет disk space
- **Рекомендация:** KEEP, работает корректно после фикса.

---

### 3. ai.krab.ear-watcher — TUNE ⚠️

- **Интервал:** 15 мин (StartInterval=900)
- **Статус:** active, exit=0, но **нет state-файла** (`ear_watcher.json/log` не созданы)
- **При ручном запуске:** `OK swift=✅(0) python=❌(273) lc_loaded=True panel=✅(n/a) alerted=False`
- **Что делает:** мониторит Krab Ear Swift agent + Python backend
- **Проблема:** python=❌(273) — Krab Ear Python backend не работает, но алертинг не срабатывает (alerted=False). Ear — on-demand инструмент, не всегда должен быть up. Поэтому скрипт корректно молчит.
- **Отсутствие лога:** скрипт создаёт state-файл только при первом алерте. Это немного скрывает работу рутины.
- **Рекомендация:** TUNE — добавить флаг "запуск подтверждён" в лог при каждом цикле (сейчас невидима).

---

### 4. ai.krab.backend-log-scanner — KEEP ✅

- **Интервал:** 4 ч (StartInterval=14400)
- **Статус:** active, exit=0
- **Последний запуск:** 23.04.2026 14:50 UTC → `lines=0, status=clean`
- **Что делает:** сканирует openclaw.log на ERROR/FATAL/timeout/SIGTERM/FloodWait/LLM-timeout; накапливает digest в `backend_scan.json`
- **Проблема:** `lines_scanned=0` — лог-файл `/Users/pablito/Antigravity_AGENTS/Краб/openclaw.log` либо отсутствует, либо пуст (OpenClaw пишет логи в другое место). Скрипт работает, но по факту сканирует 0 строк.
- **Рекомендация:** KEEP, но **проверить путь к лог-файлу** OpenClaw (возможно нужно `~/.openclaw/gateway.log` или аналог). Частота 4h — оптимальна.

---

### 5. ai.krab.daily-maintenance — KEEP ✅

- **Расписание:** CalendarInterval 02:07 ежедневно
- **Статус:** active, exit=0, СТАБИЛЬНО работает
- **Последние 3 дня:** 21.04 `maintenance_ok`, 22.04 `backup_ok 472MB + maintenance_ok`, 23.04 `backup_ok 472MB + maintenance_ok`
- **Что делает:** backup archive.db + log rotation + cleanup
- **Ценность:** реальные бэкапы 472 МБ каждую ночь. Единственная рутина с доказанной регулярной ценностью
- **Рекомендация:** KEEP, отличная работа.

---

## Claude Desktop рутины (7 + 1 = 8 штук)

**Контекст:** Все рутины требуют работающего Claude Desktop. Нет механизма проверки "когда последний раз запускалась". Нет state-файлов или логов от этих рутин в `~/.openclaw/krab_runtime_state/`. Нет recap-файлов в `.remember/`.

**Вывод:** Claude Desktop рутины, вероятно, **редко или никогда не срабатывали** с момента создания (нет доказательств output).

---

### 6. krab-openclaw-commit-review — VERIFY ⚠️

- **Ожидаемая частота:** Daily weekdays
- **Installed:** ✅ (`~/.claude/scheduled-tasks/`)
- **Evidence of firing:** 0 (нет Linear issues с label "review", нет Telegram сообщений)
- **Что делает:** git log last 24h → проверяет наличие тестов, ruff, bare except → Linear issue
- **Ценность:** MEDIUM — если срабатывает, даёт code quality gate
- **Рекомендация:** VERIFY — добавить probe (e.g., запись в state-файл).

---

### 7. krab-openclaw-sentry-digest — VERIFY ⚠️

- **Ожидаемая частота:** Daily weekdays
- **Installed:** ✅
- **Evidence of firing:** 0
- **Что делает:** Sentry unresolved errors → критичные в Linear, digest в Telegram
- **Ценность:** MEDIUM — Sentry integration полезна при наличии ошибок
- **Рекомендация:** VERIFY — нужен Sentry org `po-zm` с данными.

---

### 8. krab-openclaw-lunch-status — TUNE (lower freq) ⚠️

- **Ожидаемая частота:** Weekdays 12:17
- **Installed:** ✅
- **Evidence of firing:** 0
- **Что делает:** curl health/lite + leak stats + backend anomalies + git commits → Telegram сообщение
- **Ценность:** LOW — дублирует leak-monitor и health-watcher, добавляет только commit count
- **Рекомендация:** TUNE — объединить с linear-sync или убрать как standalone.

---

### 9. krab-openclaw-linear-sync — KEEP ✅

- **Ожидаемая частота:** Daily weekdays
- **Installed:** ✅
- **Что делает:** active/todo/stale issues → digest в Telegram
- **Ценность:** MEDIUM — project tracking полезен для workflow
- **Рекомендация:** KEEP если Linear активно используется.

---

### 10. krab-openclaw-evening-cleanup — KEEP ✅

- **Ожидаемая частота:** Daily 22:23
- **Installed:** ✅
- **Что делает:** auto-close Linear issues матчащие коммиты; stale watchlist
- **Ценность:** MEDIUM — экономит ручное закрытие задач
- **Рекомендация:** KEEP, логика auto-close полезна.

---

### 11. krab-openclaw-weekly-recap — TUNE ⚠️

- **Ожидаемая частота:** Sunday 18:07
- **Installed:** ✅
- **Evidence of firing:** 0 recap-файлов в `.remember/`
- **Что делает:** git stats за неделю + Canva infographic generate + Telegram post
- **Ценность:** LOW — Canva infographic декоративна, git stats можно получить вручную за 10 сек
- **Рекомендация:** TUNE — упростить до текстового recap без Canva (Canva MCP медленный + quota).

---

### 12. krab-openclaw-monthly-arch — TUNE ⚠️

- **Ожидаемая частота:** 1st of month 01:13
- **Installed:** ✅
- **Evidence of firing:** 0
- **Что делает:** собирает метрики (commits, endpoints, archive size) → обновляет Canva design → git commit png
- **Ценность:** LOW — архитектурный документ вручную обновляется при сессионных handoff'ах
- **Рекомендация:** TUNE — заменить Canva на markdown обновление `docs/ARCHITECTURE.md` (надёжнее, без внешних зависимостей).

---

### 13. krab-openclaw-chado-sync — REMOVE ❌

- **Ожидаемая частота:** Sunday 19:07
- **Installed:** ❌ **НЕ УСТАНОВЛЕНА** (есть в `scripts/claude_routines/` но нет в `~/.claude/scheduled-tasks/`)
- **Evidence of firing:** never
- **Что делает:** собирает git log + ecosystem comparison → пишет в How2AI Forum Topic "crossteam" + DM @callme_chado
- **Ценность:** VERY LOW — cross-AI digest концептуально интересен, но зависит от @callme_chado активности, endpoint `/api/ecosystem/comparison` не задокументирован (вероятно отсутствует), Forum Topic "crossteam" требует специального setup
- **Рекомендация:** REMOVE из `scripts/claude_routines/` или отложить до подтверждения что Chado интеграция работает.

---

## Бюджет квоты Claude Desktop routines

| Routine | fires/week | Est. tokens/run | tokens/week |
|---------|-----------|-----------------|-------------|
| commit-review | 5 | ~3k | 15k |
| sentry-digest | 5 | ~2k | 10k |
| lunch-status | 5 | ~1k | 5k |
| linear-sync | 5 | ~2k | 10k |
| evening-cleanup | 7 | ~3k | 21k |
| weekly-recap | 1 | ~5k | 5k |
| monthly-arch | 0.25 | ~4k | 1k |
| **ИТОГО** | **~28.25/week** | | **~67k tokens/week** |

При условии реального срабатывания — умеренно. Если Claude Desktop не запущен ночью — часть рутин не фаерится.

---

## Топ находки

### Баги
1. **health-watcher крешился каждые 15 мин** с `NameError: gemini_ok` — ИСПРАВЛЕНО в этом аудите
2. **backend-log-scanner сканирует 0 строк** — неверный путь к openclaw.log (нужно проверить реальный путь)
3. **chado-sync не установлена** — есть только в git, но не в `~/.claude/scheduled-tasks/`

### Отсутствие доказательств
- Ни одна Claude Desktop рутина не оставила видимых артефактов (нет recap-файлов, нет очевидных Linear issue от auto-create)

---

## Рекомендации

### TOP KEEP (реальная ценность доказана)
1. **daily-maintenance** — ежедневные 472МБ бэкапы, работает идеально
2. **leak-monitor** — поймал утечку openclaw процессов сегодня (20 → 12)
3. **health-watcher** — после фикса бага покрывает panel + gateway + disk

### TOP TUNE/REMOVE
1. **chado-sync** — REMOVE (никогда не запускалась, зависимости не готовы)
2. **weekly-recap** — TUNE (убрать Canva, заменить на markdown)
3. **monthly-arch** — TUNE (убрать Canva + git commit png, заменить на ARCHITECTURE.md update)

### Немедленное действие
- Проверить путь логов OpenClaw для **backend-log-scanner**: найти реальный `gateway.log` и обновить `OPENCLAW_LOG` переменную в скрипте.
