# Анимированный Telegram-аватар Краба

Документ фиксирует локальный пайплайн сборки аватарки, чтобы следующий агент не
искал заново, как получить файл для Telegram.

## Файлы

- `scripts/generate_krab_telegram_avatar.py` — генератор PNG-кадров и видео.
- `generate_krab_telegram_avatar.command` — macOS-запуск двойным кликом.
- `artifacts/telegram_avatar/krab_telegram_avatar.mp4` — основной файл для Telegram.
- `artifacts/telegram_avatar/krab_telegram_avatar.webm` — web-превью/резерв.
- `artifacts/telegram_avatar/krab_telegram_avatar_poster.png` — постер кадра.

## Параметры

- Размер: `720x720`.
- Длительность: `4s`.
- FPS: `30`.
- Кодек Telegram-файла: H.264, `yuv420p`, `+faststart`.

## Почему так

Для аватарки Telegram важнее читаемый круглый кроп и стабильный MP4 на iPhone,
чем интерактивность HTML. Поэтому генератор рисует 3D-like Краба через Pillow,
а `ffmpeg` собирает короткое зацикленное видео без звука.
