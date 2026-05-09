# Inbox Triage — Session 6 (2026-04-12)

Источник данных: GET /api/inbox/items (снимок ~00:00 UTC 12.04)
Панель :8080 на момент триажа недоступна (Connection refused) — данные из кэша MCP krab_status.
Krab runtime: **up**, telegram: running, scheduler: enabled.

---

## Итого: 10 open items, 3 attention

---

## Все open items с рекомендациями

### ОШИБОЧНЫЕ (warning) — требуют внимания

| # | item_id | Job | Последний запуск | Рекомендация |
|---|---------|-----|-----------------|--------------|
| 1 | `0796229920b6` | **Transcription Check: Дашуля.m4a** | 2026-04-03T19:37 UTC — `error` | **ACK** |
| 2 | `562e1bd3b11f` | **Daily Morning Report** | 2026-03-08T07:00 UTC — `error` | **FIX или ACK** |
| 3 | `b3577a717b96` | **Obsidian Librarian Daily Indexing** | 2026-04-11T07:00 UTC — `error` | **FIX** |

### УСПЕШНЫЕ (info) — давно не acknowledged

| # | item_id | Job | Последний запуск | Статус | Рекомендация |
|---|---------|-----|-----------------|--------|--------------|
| 4 | `c48cf1eb2849` | Mercadona Restock Reminder | 2026-03-07T17:22 UTC | `ok` | **ACK** |
| 5 | `22a843d06fe1` | Dirty Joke Reminder | 2026-03-06T16:08 UTC | `ok` | **ACK** |
| 6 | `81e1bf2a95c3` | jokes_70plus | 2026-03-20T04:18 UTC | `ok` | **ACK** |
| 7 | `46f2b4f39769` | news-digest-15 | 2026-04-11T13:00 UTC | `ok` | **ACK** |
| 8 | `f2e903257527` | news-digest-23 | 2026-04-11T21:00 UTC | `ok` | **ACK** |
| 9 | `e2eb2a367d7f` | email-monitor | 2026-04-11T23:48 UTC | `ok` | **ACK** |
| 10 | `00a39e8977fe` | email-monitor | 2026-04-11T16:04 UTC | `ok` | **ACK** |

---

## Анализ ошибочных jobs

### 1. Transcription Check: Дашуля.m4a (f3174b14)
- **Тип**: one-shot job (`schedule.kind = "at"`, дата в прошлом — 2026-04-03)
- **Статус**: `enabled = false`, `deleteAfterRun = true` (не удалился)
- **Причина ошибки**: `"Channel is required when multiple channels are configured: telegram, discord, slack, signal, imessage, bluebubbles"`
  — у job нет `delivery.channel`, а у агента несколько каналов → доставка не определилась
- **Вывод**: job выполнилась однократно 9 дней назад, уже неактуальна (транскрипция Дашуля.m4a либо готова, либо нет — отдельная история)
- **Рекомендация**: **ACK item**, в openclaw удалить job вручную (она не удалилась из-за ошибки)

### 2. Daily Morning Report (9891cf94)
- **Тип**: ежедневный cron (`0 8 * * *` Madrid)
- **Статус**: `enabled = false`, ошибка с 2026-03-08 (!), не запускалась >35 дней
- **Причина ошибки**: `"Delivering to WhatsApp requires target <E.164|group JID>"`
  — job настроена на доставку в WhatsApp без указания получателя
- **Вывод**: job отключена (`enabled=false`), давно сломана
- **Рекомендация**: **ACK item** (job disabled — inbox шум без смысла). Если нужен ежедневный отчёт — пересоздать job с `delivery.channel = "telegram"` и `delivery.to = "312322764"`

### 3. Obsidian Librarian Daily Indexing (6b8e0ade) — КРИТИЧНО
- **Тип**: ежедневный cron (`0 9 * * *` Madrid)
- **Статус**: `enabled = true`, **7 consecutiveErrors**, последний запуск 11.04 в 09:00
- **Причина ошибки**: `"Channel is required when multiple channels are configured: discord, signal, slack, telegram, whatsapp, bluebubbles"`
  — у delivery нет `channel`, агент имеет >2 каналов
- **Вывод**: Job активна, будет завтра запущена снова и снова падать. Это единственный по-настоящему сломанный активный job.
- **Рекомендация**: **FIX** — добавить `delivery.channel = "telegram"` и `delivery.to = "312322764"` в конфиг job через openclaw

---

## Статус всех cron jobs

| Job | Enabled | Last Status | consecutiveErrors | Примечание |
|-----|---------|-------------|-------------------|-----------|
| Daily Morning Report | **false** | error | 1 | WhatsApp target не указан |
| Mercadona Restock Reminder | false | ok | 0 | disabled, норм |
| Dirty Joke Reminder | false | ok | 0 | disabled, норм |
| jokes_70plus | false | ok | 0 | disabled, норм |
| Transcription Check: Дашуля.m4a | false | error | 1 | одноразовая, устаревшая |
| **Obsidian Librarian Daily Indexing** | **true** | **error** | **7** | **ТРЕБУЕТ FIX** |
| news-digest-15 | true | ok | 0 | работает нормально |
| news-digest-23 | true | ok | 0 | работает нормально |
| email-monitor | true | ok | 0 | работает нормально |
| Nightly Self-Diagnostics | true | never | 0 | не запускался ещё |

---

## Системные наблюдения

- **Панель :8080**: недоступна в момент триажа — возможно Krab перезапустился или был остановлен между сессиями. Данные получены через MCP krab_status (порт работал ранее).
- **Логи openclaw.log**: последние строки от 2026-04-10 — нет свежих данных по cron-ошибкам в логах (job errors хранятся в jobs.json, не в openclaw.log).
- **swarm_recurring_jobs.json**: пустой (`"jobs": []`) — это норма, сварм-шедулер отдельно от openclaw cron.
- **Паттерн повторных upsert**: все inbox items upsert-ились ~12 раз каждый 11.04.2026 — это нормальное поведение proactive_watch при каждом heartbeat Краба.

---

## Рекомендуемые действия (приоритет)

1. **FIX Obsidian Librarian** (единственный активный сломанный job):
   ```
   # В openclaw UI или через API патч jobs.json:
   job 6b8e0ade → delivery.channel = "telegram", delivery.to = "312322764"
   ```

2. **ACK 7 info-items** (успешные старые runs) — items 4-10 в таблице выше

3. **ACK + clean** Transcription Check Дашуля.m4a — устаревшая one-shot job, удалить из cron

4. **Решить судьбу Daily Morning Report**: либо ACK + оставить отключённой, либо пересоздать с правильным delivery в Telegram

5. **Проверить доступность :8080** при следующем запуске (возможно Krab не запущен)
