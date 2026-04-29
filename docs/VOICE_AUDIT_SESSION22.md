# Voice Pipeline Audit — Session 22

**Дата:** 25.04.2026  
**Ветка:** `fix/daily-review-20260421`  
**Скоуп:** TTS + STT path, voice profile, per-chat blocklist, quick wins.

## TTS engine + settings

- **Engine:** `edge-tts` (Microsoft Neural voices) → ffmpeg → OGG/Opus.
  Файл: `src/voice_engine.py` (122 строки, тонкая обёртка).
- **Default voice:** `ru-RU-DmitryNeural` (RU мужской). Альтернативы в комментах:
  `ru-RU-SvetlanaNeural`, `en-US-ChristopherNeural`, `en-US-JennyNeural`.
- **Speed default:** `1.5` (`config.VOICE_REPLY_SPEED`), clamp `0.75..2.5` в
  `VoiceProfileMixin._normalize_voice_reply_speed`. Уже «живой» темп — поднимать
  ещё не надо, скорее давать opt-out для медленнее.
- **Delivery default:** `text+voice` (есть режим `voice-only` для будущего live).
- **Cap длины текста:** 600 символов (`_TTS_MAX_CHARS`). Длиннее — ffmpeg/edge-tts
  возвращают `NoAudioReceived` или дают рваное аудио.
- **Audio format:** Opus VBR 32 kbps → **поднято до 48 kbps mono 24 kHz** (этот
  audit, см. quick wins).
- **Параллельный TTS (macOS `say`):** есть отдельный путь `!tts` через
  `command_handlers.handle_tts` (строки 13164+). Использует `say` + ffmpeg.
  Не влияет на основной voice-reply поток.

## STT chain + fallbacks

Файл: `src/modules/perceptor.py`. Цепочка:

1. **Voice Gateway** `http://127.0.0.1:8090/stt` (multipart upload). Timeout 30s.
   Health check: `/health` 2s timeout перед запросом не делается — сразу POST.
2. **mlx_whisper local fallback** (Apple Silicon). Default model:
   `mlx-community/whisper-small-mlx`, configurable через `MLX_WHISPER_MODEL`.

**Failure markup:** оба backend упали → `[transcription_failed: voice_gateway=...; mlx_whisper=...]`
- Detection в `voice_profile.py:_transcribe_audio_message` (строки 429–445).
- `KRAB_VOICE_STRICT_MODE=0` (default): LLM получает честный prompt о провале и
  пишет owner текстом «не смог распознать».
- `KRAB_VOICE_STRICT_MODE=1`: немедленный hardcoded reply без LLM call.

**KrabEar client** (`src/integrations/krab_ear_client.py`): pure health-check
клиент, IPC через unix socket `~/Library/Application Support/KrabEar/krabear.sock`,
HTTP fallback. Используется только для ecosystem health, не для самой STT.
Активный STT путь — Voice Gateway.

## Voice profile state

- Mixin: `src/userbot/voice_profile.py` (458 строк).
- Persists через `config.update_setting` → `.env`.
- Поля: `voice_mode`, `voice_reply_speed`, `voice_reply_voice`, `voice_reply_delivery`.
- Per-chat blocklist (`VOICE_REPLY_BLOCKED_CHATS`) — comma-separated chat_id.
  Default уже включает `-1001587432709` (How2AI инцидент 08-09.04). Управление:
  `!voice block/unblock/blocked`, persist в `.env`.
- W21 fix (`!voice block`): механика на месте — `add/remove/get_voice_blocked_chat`,
  read-on-each-call через `getattr(config, ...)`. Мутации идут через
  `config.update_setting`, runtime обновляется без рестарта.

## Latency observations

Реальные замеры не делались (Краб не был запущен с tracing). По коду:
- TTS: edge-tts API call (≈0.8–1.5s для 100 символов) + ffmpeg encode (≈0.2s)
  = ожидаемо 1.0–1.7s для коротких ответов.
- STT Voice Gateway: 30s timeout, обычно 0.5–2s для voice ≤30s.
- mlx_whisper fallback: 2–5s на small-mlx модели, 5–10s на whisper-large.
- **Backlog item B**: добавить `voice_engine_latency_seconds` Prometheus
  histogram + `perceptor_stt_latency_seconds` (см. ниже).

## Quick wins applied

### 1. ffmpeg Opus bitrate 32k → 48k mono 24 kHz (`src/voice_engine.py`)
Заметно чище звук на динамике iPhone (особенно на «c», «s» — sibilants),
overhead на 20-сек voice ≈ +40 КБ. Mono 24 kHz — sweet spot для Opus speech.

### 2. mlx_whisper model env override (`src/modules/perceptor.py`)
**Bug fix:** `_transcribe_mlx_whisper` хардкодил `whisper-turbo`, игнорируя
`self._mlx_model` (который читает `MLX_WHISPER_MODEL` env). Owner не мог сменить
fallback модель без правки кода. Теперь fallback использует тот же конфиг что и
основной mlx путь.

## Backlog (priorized)

### P1 — STT Gateway fallback к Cloud Whisper API
Сейчас при падении Voice Gateway + отсутствии mlx_whisper (или RAM-limited M-серия)
— оба backend дают пусто, owner получает «не смог распознать». Третий tier
(OpenAI Whisper API / Groq Whisper) в `perceptor.transcribe` дал бы ~99%
надёжность. Стоимость пренебрежимо мала ($0.006/min). Добавить как opt-in
через `WHISPER_CLOUD_FALLBACK_API_KEY`.

### P2 — Voice latency telemetry
`krab_voice_tts_duration_seconds` (histogram, label `voice_id`) и
`krab_voice_stt_duration_seconds` (label `backend=gateway|mlx|cloud`).
Без них непонятно почему voice иногда «тормозит» — owner ощущает разницу,
но root cause не виден. Включить алерт `VoiceTTSSlowP95 > 5s`.

### P3 — Per-chat voice profile (voice_id и speed)
Сейчас один глобальный голос/скорость. Логично иметь, например,
ru-RU-Svetlana для groups и Dmitry для DM, или slower speed в official
канале. Расширение `VoiceProfileMixin` с lookup `chat_id → profile_overrides`,
persist в JSON (`~/.openclaw/krab_runtime_state/voice_profiles.json`).
Owner UI: `!voice profile <chat_id> <voice_id> [speed]`.

## Files touched

- `src/voice_engine.py` — Opus 48k mono 24 kHz.
- `src/modules/perceptor.py` — fix mlx fallback ignoring env.
- `docs/VOICE_AUDIT_SESSION22.md` — этот документ.
