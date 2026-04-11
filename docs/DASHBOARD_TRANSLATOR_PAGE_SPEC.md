# Dashboard: Translator Status Page — Spec для Gemini 3.1 Pro

> Для реализации frontend через Gemini 3.1 Pro API
> Backend API endpoint уже готов: нужно добавить

## Назначение

Страница `/translator` на owner panel (`:8080`) — статус переводчика в реальном времени.

## API Endpoints (уже существуют или нужно добавить)

### Существующие
- `GET /api/health/lite` → содержит `voice_gateway_configured`

### Нужно добавить (backend, ~20 строк)
```
GET /api/translator/status
```
Response:
```json
{
  "ok": true,
  "profile": {
    "language_pair": "es-ru",
    "translation_mode": "bilingual",
    "voice_strategy": "voice-first",
    "ordinary_calls_enabled": true,
    "internet_calls_enabled": true
  },
  "session": {
    "session_status": "active|idle|paused",
    "translation_muted": false,
    "active_chats": [],
    "last_language_pair": "es-ru",
    "last_translated_original": "Buenos días...",
    "last_translated_translation": "Доброе утро...",
    "last_event": "translation_completed",
    "stats": {
      "total_translations": 15,
      "total_latency_ms": 45000
    }
  }
}
```

## UI Layout

### Header
- Заголовок: "🔄 Translator"
- Статус badge: "Active" (зелёный) / "Idle" (серый) / "Paused" (жёлтый)

### Секция: Profile
Карточка с текущими настройками:
- Language pair: `es-ru`
- Mode: `bilingual`
- Voice strategy: `voice-first`
- Ordinary calls: ON/OFF
- Internet calls: ON/OFF

### Секция: Session
- Status: active/idle
- Muted: yes/no
- Active chats: список или "все"
- Stats: X переводов, средняя latency Y ms

### Секция: Last Translation
- Original (в рамке): "Buenos días..."
- Translation (в рамке): "Доброе утро..."
- Direction badge: `es→ru`
- Timestamp

### Стиль
- Как остальные страницы panel (/, /stats, /costs, /swarm)
- Навбар сверху (sticky)
- Тёмная тема
- Responsive

## Gemini Prompt

```
Создай HTML страницу для owner dashboard Krab.
Страница: /translator — статус переводчика.

Данные берутся из API: GET /api/translator/status (JSON, формат описан ниже).

Layout:
1. Навбар: ← / | Stats | Inbox | Costs | Swarm | Translator (текущая, active)
2. Status badge (Active/Idle/Paused)
3. Profile card: language_pair, mode, voice_strategy, calls enabled
4. Session card: status, muted, active_chats, stats (total translations, avg latency)
5. Last Translation card: original → translation, direction badge, timestamp

Стиль: тёмная тема (#1a1a2e фон, #16213e карточки), sans-serif шрифт,
как у уже существующих страниц. Используй fetch для загрузки данных при открытии.

JSON response format:
{profile: {language_pair, translation_mode, voice_strategy, ...},
 session: {session_status, translation_muted, active_chats, stats, last_translated_original, last_translated_translation, ...}}
```
