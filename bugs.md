# Краб — Bug Log

## BUG-001: Voice STT regression — Voice Gateway dead, no fallback

**Дата:** 2026-04-21  
**Статус:** FIXED  
**Компонент:** `src/modules/perceptor.py`

### Симптом
Voice messages в @yung_nagato DM (и любом DM) возвращают:
```
❌ Не удалось распознать голосовое сообщение.
```

### Root cause
Voice Gateway (`http://127.0.0.1:8090`) мёртв — Connection refused.  
`Perceptor.transcribe()` делал единственную попытку через Gateway, получал
`'All connection attempts failed'`, логировал `perceptor_transcribe_failed`
и возвращал пустую строку.  
Вызывающий код в `voice_profile.py:_transcribe_audio_message()` при пустом
результате отправлял пользователю generic "❌ Не удалось распознать..."  
без указания что именно упало.

### Что скрывало проблему
`perceptor_transcribe_failed` логировался как WARNING без имени backend,
поэтому в логах было неочевидно что проблема в Gateway, а не в файле/сети.

### Fix
`src/modules/perceptor.py`:
- Добавлен `_gateway_alive()` — быстрая проверка Gateway (timeout 2s)  
- Добавлен `_transcribe_via_mlx()` — fallback через `mlx_whisper` локально,
  выполняется в executor чтобы не блокировать event loop  
- `transcribe()` теперь: (1) проверяет Gateway, (2) если жив — пробует Gateway,
  (3) при падении или смерти Gateway — fallback на mlx_whisper  
- Все пути логируют `backend=` (voice_gateway|mlx_whisper) и причину fallback  
- mlx model настраивается через env `MLX_WHISPER_MODEL`
  (default: `mlx-community/whisper-small-mlx`)

### Как проверить
1. Убедиться что Voice Gateway мёртв: `curl http://127.0.0.1:8090/health`
2. Отправить voice message в @yung_nagato DM
3. Лог должен показать: `perceptor_gateway_dead` + `perceptor_transcribe_ok backend=mlx_whisper`
4. Краб должен ответить на смысл голосового (не ошибку)

### Долгосрочное решение
Поднять Voice Gateway как LaunchAgent (сейчас нет plist для него).
Добавить в `scripts/launchagents/` plist для `ai.krab.voice-gateway`.
