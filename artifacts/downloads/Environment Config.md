# Environment Config

## Telegram

- `TELEGRAM_API_ID=12345`
- `TELEGRAM_API_HASH=abcdef`
- `TELEGRAM_SESSION_NAME=krab_v8`

## Cloud AI (Google Gemini)

### Flashlight (самая быстрая и дешевая)

- `GEMINI_CHAT_MODEL=gemini-2.0-flash`

### Thinking / Heavy Logic

- `GEMINI_THINKING_MODEL=gemini-2.0-pro-exp-02-05`
- `GEMINI_API_KEY=твой_ключ_здесь`

## Local AI (LM Studio)

- `LM_STUDIO_URL=http://localhost:1234/v1`

> [!NOTE]
> Ставим 0, чтобы использовать только локально, 1 — чтобы только облако.
> Лучше использовать логику "Auto" в коде.

- `USE_LOCAL_LLM=auto`

## Voice & Media

> [!TIP]
> edge-tts работает локально и бесплатно

- `TTS_ENGINE=edge`
- `TTS_VOICE=ru-RU-SvetlanaNeural`
