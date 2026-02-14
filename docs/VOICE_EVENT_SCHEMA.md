# Voice Event Schema v1.0

**Дата:** 2026-02-12
**Цель:** единый контракт событий между `Krab` и `Krab Voice Gateway`.

## Нормализованная структура

```json
{
  "schema_version": "1.0",
  "session_id": "vs_xxx",
  "event_type": "stt.partial",
  "source": "voice_gateway",
  "severity": "info",
  "latency_ms": 120,
  "ts": "2026-02-12T21:20:08+00:00",
  "data": {}
}
```

## Обязательные поля

1. `schema_version` — текущая версия схемы (`1.0`).
2. `session_id` — ID голосовой сессии.
3. `event_type` — тип события (`stt.partial`, `translation.partial`, `tts.ready`, `call.state`, `call.error`, ...).
4. `source` — источник (`voice_gateway`, `twilio_media`, `krab_ear`, ...).
5. `severity` — уровень (`info`, `low`, `high`).
6. `latency_ms` — задержка этапа (0+).
7. `ts` — timestamp события (если нет в raw, может быть пустой строкой).
8. `data` — payload события.

## Правила совместимости

1. Неизвестные поля в raw payload не должны ломать нормализацию.
2. Отсутствующие необязательные поля заполняются дефолтами.
3. Неизвестный `event_type` допускается и нормализуется как строка.
4. `severity`:
- `high` для `*.error` и `call.error` по умолчанию.
- `info` для штатных stream-событий (`stt.partial`, `translation.partial`, `tts.ready`, `call.state`).
- `low` для прочих.

## Инструменты проверки

1. Скрипт:
```bash
python scripts/check_voice_event_schema.py '{"type":"stt.partial","data":{"session_id":"vs_1","latency_ms":120}}'
```

2. One-click:
- `scripts/check_voice_event_schema.command`

## Реализация в коде

1. Нормализация:
- `src/core/voice_gateway_client.py` -> `VoiceGatewayClient.normalize_stream_event(...)`

2. Тесты:
- `tests/test_voice_event_schema.py`
