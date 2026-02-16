# ROADMAP_KRAB_VOICE_GATEWAY.md

Обновлено: 2026-02-16  
Статус: Draft (готово к передаче в Antigravity)

## Цель сервиса
`Krab Voice Gateway` — отдельный сервис TTS/voice-routing и телефонии.
Сервис отвечает за озвучку, звонковый пайплайн и телеком-интеграции.

## Почему раздельно
- TTS/телефония имеют другой SLA и риски.
- Можно выпускать обновления voice-части без рестарта `Krab`.
- Удобнее делать отдельный cost-control для минут/каналов.

## Контракт интеграции (минимум)
### REST
- `POST /v1/tts/speak`
  - вход: `text`, `voice`, `style`, `trace_id`, `chat_id`, `message_id`
  - выход: `audio_url|audio_file`, `duration_ms`, `engine`
- `POST /v1/call/translate`
  - вход: `session_id`, `source_lang`, `target_lang`, `mode`
  - выход: `session_state`, `latency_ms`
- `GET /v1/call/diag/{session_id}`
- `GET /health`
- `GET /metrics`

### События (опционально)
- `voice.generated`
- `voice.failed`
- `call.session.updated`

## Фазы
### VG-1 (P0): Стабильность TTS
- [ ] Единый API для локальных и облачных voice engines.
- [ ] Fallback-порядок (local -> cloud -> safe-voice).
- [ ] Нормализация громкости/темпа перед отправкой в Telegram.
- [ ] Rate-limit на авто-голос в группах.

### VG-2 (P0): Наблюдаемость и расходы
- [ ] Ежедневный отчёт cost/minutes по каналам.
- [ ] Алерты на spikes latency/error/cost.
- [ ] Диагностический endpoint для active session.

### VG-3 (P1): Перевод звонков в реальном времени
- [ ] Полный loop: STT -> Translate -> TTS (двусторонне).
- [ ] Профили качества: low-latency / high-accuracy.
- [ ] Буфер jitter и компенсация задержки.

### VG-4 (P1): Remote Access & iOS Companion
- [ ] Вынесенный control API для доступа вне локальной сети.
- [ ] AuthN/AuthZ для удалённых сессий.
- [ ] Подготовка контракта для iOS companion приложения.

## KPI
- Voice generation success rate >= 99%.
- P95 TTS latency <= 4s для текста до 1200 символов.
- Ошибки failover без user-visible падения >= 95%.

## Definition of Done
- Есть отдельный e2e smoke «text -> voice -> telegram delivery».
- Есть интеграционные тесты с `Krab Ear` и `Krab`.
- Есть отдельный чеклист production-готовности.
