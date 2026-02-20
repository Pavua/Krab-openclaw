# Krab Ear Launch Map (без путаницы)

## Какие кнопки использовать
1. Backend Krab Ear Agent (основной режим):
   - `./start_krab_ear_backend.command`
2. Остановка backend:
   - `./stop_krab_ear_backend.command`
3. Проверка статуса backend:
   - `./status_krab_ear_backend.command`

## Что НЕ путать с backend
- `./krab_ear.command` — это **FILE MODE**:
  ручная транскрибация отдельного аудиофайла через `voice_bridge.py`.
  Этот скрипт не поднимает backend и не должен использоваться для постоянной фоновой работы Krab Ear Agent.

## Быстрый безопасный сценарий
1. `./stop_krab_ear_backend.command`
2. `./start_krab_ear_backend.command`
3. `./status_krab_ear_backend.command`

Так мы гарантируем, что одновременно работает только один каноничный экземпляр backend-а.
