# B.2 Translator MVP — Design Doc

> **Created:** 2026-04-10 (session 5)
> **Decision:** Direct pipeline (Agent B), VG отдельно для VoIP
> **Status:** Design approved, ready for implementation

---

## Scope MVP

**Входит:**
- Owner присылает voice note в Telegram → Krab транскрибирует (существующий perceptor/KrabEar) → определяет язык → переводит через OpenClaw LLM (flash model) → отвечает текстом в чат
- Поддержка пар: `es-ru`, `en-ru`, `es-en` (уже в `ALLOWED_LANGUAGE_PAIRS`)
- Активация: `!translator session start` (уже реализовано)
- Per-chat opt-in: translator активен только в чатах где запущена сессия

**НЕ входит в MVP:**
- TTS озвучка перевода (будет позже, через VG или Edge TTS)
- Живые звонки (WhatsApp, FaceTime, VoIP) — Track C через VG
- Auto-detect языка по аудио (детектим по тексту транскрипта)
- Пары DE/FR/IT
- Групповые чаты / forum topics

---

## Архитектура

### Data Flow

```
1. Voice note → _process_message_serialized (userbot_bridge.py)
2. Проверка: session_status == "active" AND has_audio_message
3. _transcribe_audio_message(message) → transcript (уже существует)
4. detect_language(transcript) → src_lang (новый модуль)
5. resolve_target_lang(src_lang, profile.language_pair) → tgt_lang
6. translate_text(transcript, src_lang, tgt_lang) → перевод через OpenClaw
7. Reply: "🔄 {src_lang}→{tgt_lang}\n**{original}**\n_{translation}_"
8. apply_translator_session_update(stats update)
```

### Новые модули

| Путь | Строк | Назначение |
|------|-------|-----------|
| `src/core/language_detect.py` | ~40 | `detect_language(text) -> str` (ISO 639-1), fallback на profile pair |
| `src/core/translator_engine.py` | ~80 | `translate_text(text, src, tgt) -> TranslationResult`, вызов OpenClaw |

### Изменения в существующих модулях

| Модуль | Изменение | Строк |
|--------|-----------|-------|
| `src/userbot/llm_flow.py` | Ветка translator routing перед обычным LLM path | ~30 |
| `src/core/translator_session_state.py` | Добавить `active_chats: list[str]`, `stats` | ~15 |

### Точка вставки в pipeline

В `_process_message_serialized` (llm_flow mixin), **перед** обычным OpenClaw LLM path:

```python
# После transcription, перед LLM:
if has_audio_message and query and self._is_translator_active_for_chat(chat_id):
    translation_result = await self._handle_translator_voice(
        message, query, chat_id
    )
    if translation_result:
        return  # перевод отправлен, не идём в обычный LLM
```

---

## Детали реализации

### 1. `src/core/language_detect.py`

```python
from langdetect import detect, DetectorFactory
DetectorFactory.seed = 0  # детерминизм

def detect_language(text: str, *, confidence_threshold: float = 0.85) -> str:
    """Определяет язык текста, возвращает ISO 639-1 код."""
    if not text or len(text.strip()) < 5:
        return ""
    try:
        return detect(text)
    except Exception:
        return ""

def resolve_translation_pair(
    detected_lang: str,
    profile_pair: str,
) -> tuple[str, str]:
    """
    Резолвит (src_lang, tgt_lang) на основе detected и profile pair.
    
    Profile pair формат: "es-ru" → если detected=es, target=ru; если detected=ru, target=es.
    """
    parts = profile_pair.split("-", 1)
    if len(parts) != 2:
        return detected_lang, "ru"  # fallback
    lang_a, lang_b = parts
    if detected_lang == lang_a:
        return lang_a, lang_b
    elif detected_lang == lang_b:
        return lang_b, lang_a
    else:
        # Язык не в паре — переводим на первый язык пары
        return detected_lang, lang_a
```

### 2. `src/core/translator_engine.py`

```python
@dataclass
class TranslationResult:
    original: str
    translated: str
    src_lang: str
    tgt_lang: str
    latency_ms: int
    model_id: str

async def translate_text(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    openclaw_client: "OpenClawClient",
    model: str = "auto",  # flash tier для скорости
) -> TranslationResult:
    """Переводит текст через OpenClaw LLM."""
    prompt = (
        f"Переведи следующий текст с {src_lang} на {tgt_lang}. "
        f"Верни ТОЛЬКО перевод, без пояснений:\n\n{text}"
    )
    # ... вызов openclaw_client.complete(prompt, model=model)
```

### 3. Mixin метод в llm_flow.py

```python
async def _handle_translator_voice(
    self, message, transcript: str, chat_id: int
) -> bool:
    """Обрабатывает voice note в режиме translator. Возвращает True если обработано."""
    from ..core.language_detect import detect_language, resolve_translation_pair
    from ..core.translator_engine import translate_text
    
    profile = self.get_translator_runtime_profile()
    detected = detect_language(transcript)
    if not detected:
        return False  # не удалось определить язык, идём в обычный LLM
    
    src_lang, tgt_lang = resolve_translation_pair(detected, profile["language_pair"])
    if src_lang == tgt_lang:
        return False  # язык совпадает, не переводим
    
    result = await translate_text(transcript, src_lang, tgt_lang, openclaw_client=self.openclaw)
    
    reply_text = f"🔄 {src_lang}→{tgt_lang}\n**{result.original}**\n_{result.translated}_"
    await message.reply(reply_text)
    
    # Update session stats
    # ...
    return True
```

---

## Concurrency

`asyncio.Semaphore(3)` — максимум 3 параллельных перевода. Для personal userbot достаточно.

## Latency Budget

| Шаг | Время |
|-----|-------|
| Download voice | ~1-2s |
| Transcribe (KrabEar Metal) | ~2-4s |
| Detect language | ~5ms |
| LLM translate (flash) | ~1-3s |
| **Total** | **~4-9s** |

Приемлемо для async voice notes.

---

## Implementation Order

1. `src/core/language_detect.py` + тесты (~30 мин)
2. `src/core/translator_engine.py` + тесты (~30 мин)
3. Mixin integration в `llm_flow.py` (~30 мин)
4. `translator_session_state.py` расширение (active_chats) (~15 мин)
5. E2E тест через Telegram (~15 мин)

**Total estimate:** ~2 часа

---

## Future (post-MVP)

- TTS для перевода (через VG или Edge TTS)
- VoIP live translation (через Voice Gateway)
- Auto-detect по аудио (Whisper language detection)
- Группы / forum topics
- DE/FR/IT пары
