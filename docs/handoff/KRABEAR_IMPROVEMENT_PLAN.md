# KrabEar — план улучшений (отдельный scope)

Текущее состояние: KrabEar уже поддерживает `transcribe_paths` через IPC,
используя `mlx-whisper` с Metal GPU (whisper-large-v3-turbo и large-v3).
MCP сервер подключён к нему и использует его как основной транскрибатор.

---

## Задача 1: Диаризация спикеров

**IPC метод:** `transcribe_paths_with_diarization`

**Что делает:** транскрибирует аудио + определяет кто говорит (Speaker 1, Speaker 2, ...)
с таймстампами для каждого фрагмента.

**Реализация:**
- pyannote.audio 3.1 (уже есть PoC: `/Krab Ear/poc_diarization/diarize_test.py`)
- Схема: pyannote.audio разбивает на сегменты по спикерам →
  каждый сегмент отрезается из аудио →
  mlx-whisper транскрибирует каждый сегмент →
  результаты склеиваются с метками спикеров

**Требования:**
- `pyannote.audio>=3.1` + HuggingFace token (модель `pyannote/speaker-diarization-3.1`)
- Отдельное Python-окружение (torch + torchaudio + pyannote конфликтуют с MLX-окружением)
- Или: запускать pyannote в subprocess, mlx — в основном процессе

**Примерный API ответа:**
```json
{
  "result": {
    "segments": [
      {"speaker": "SPEAKER_00", "start": 0.0, "end": 3.5, "text": "Привет, как дела?"},
      {"speaker": "SPEAKER_01", "start": 3.8, "end": 7.2, "text": "Всё отлично, спасибо."}
    ],
    "full_text": "SPEAKER_00: Привет, как дела?\nSPEAKER_01: Всё отлично, спасибо."
  }
}
```

**Полезно для:** совещания, интервью, многоспикерные записи.

---

## Задача 2: Пакетная транскрипция директории

**IPC метод:** `batch_transcribe_directory`

**Что делает:** рекурсивно обходит директорию, находит аудиофайлы (`.m4a`, `.mp3`, `.ogg`,
`.wav`, `.opus`), транскрибирует каждый, сохраняет `.md` рядом с файлом (или в Obsidian vault).

**Параметры:**
```json
{
  "directory": "/Users/pablito/Downloads/voice_notes",
  "extensions": [".m4a", ".ogg", ".mp3"],
  "after_date": "2026-01-01",
  "output_dir": "/Users/pablito/Documents/Obsidian/Transcriptions",
  "format": "markdown"
}
```

**Выход `.md`:**
```markdown
# Транскрипция: voice_note_2026-03-21.m4a
**Дата:** 2026-03-21 14:35
**Длительность:** 2m 14s
**Модель:** whisper-large-v3-turbo

---

Текст транскрипции здесь...
```

**Полезно для:** iPhone Voice Memos, пакетная обработка накопленных голосовых.

---

## Задача 3: Статус очереди транскрипции

**IPC метод:** `get_transcription_queue`

**Что делает:** возвращает прогресс активных и завершённых задач транскрипции.

**Примерный ответ:**
```json
{
  "result": {
    "active": [
      {"id": "batch_001", "file": "meeting.m4a", "progress": 0.67, "eta_seconds": 45}
    ],
    "completed_today": 12,
    "total_duration_today_minutes": 38.5
  }
}
```

**Нужно для:** batch_transcribe_directory — чтобы МСР-клиент мог опрашивать прогресс
без блокировки на долгих задачах.

---

## Ключевые файлы KrabEar для следующего диалога

```
/Users/pablito/Antigravity_AGENTS/Krab Ear/KrabEar/backend/service.py
  → основная логика IPC-сервера, все существующие методы

/Users/pablito/Antigravity_AGENTS/Krab Ear/KrabEar/backend/
  → директория с остальными модулями backend

/Users/pablito/Antigravity_AGENTS/Krab Ear/poc_diarization/diarize_test.py
  → PoC диаризации (pyannote + mlx-whisper), уже написан

/Users/pablito/Antigravity_AGENTS/Krab Ear/CLAUDE.md  (если есть)
  → или AGENTS.md — архитектура проекта
```

## Что НЕ нужно прикладывать для работы над KrabEar

- `Краб/docs/handoff/SESSION_HANDOFF.md` — операционный документ Краба, не нужен
- `Краб/docs/MASTER_PLAN_VNEXT_RU.md` — план Краба, не KrabEar
- `Краб/CLAUDE.md` — можно не прикладывать, если задача только в KrabEar
- Любые файлы из `Krab Voice Gateway/` — отдельный проект, другой scope
