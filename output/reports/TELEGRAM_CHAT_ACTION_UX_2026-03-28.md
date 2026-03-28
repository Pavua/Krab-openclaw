# Telegram Chat Action UX — 2026-03-28

## Контекст
- Цель: сделать Telegram-сигнал во время длинной обработки truthful.
- До фикса voice-path показывал `record_audio` уже на фазе reasoning/tool-flow, хотя голосовой файл ещё не существовал.

## Что изменено
- Long-path keepalive переведён на `typing`.
- Перед фактической отправкой voice/document теперь идёт отдельный delivery-action:
  - `upload_audio`
  - `upload_document`

## Unit verification
- `python3 -m py_compile src/userbot_bridge.py tests/unit/test_userbot_buffered_stream_flow.py`
- `./venv/bin/pytest -q tests/unit/test_userbot_buffered_stream_flow.py tests/unit/test_userbot_message_batching.py tests/unit/test_userbot_stream_timeouts.py -q`
- Результат: `28 passed, 1 warning`

## Live verification
1. Со второго аккаунта `p0lrd` отправлен запрос:
   - `UXVOICE-1774706160 ответь одной короткой строкой по-русски без внутренних шагов.`
2. Userbot прислал immediate ack:
   - `1302581`
   - `🦀 Принял запрос ... ⏳ Задача продолжает выполняться в фоне...`
3. Затем пришёл финальный ответ:
   - `1302582`
   - `UXVOICE-1774706160: Краб на связи, голос в норме! 🦀`

## Вывод
- UX-фаза теперь соответствует реальности: пока Краб думает, Telegram видит `typing`; upload-сигнал появляется только на реальной фазе доставки вложения.
