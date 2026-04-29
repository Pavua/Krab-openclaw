# -*- coding: utf-8 -*-
"""
Тесты для голосового движка (voice_engine.py) и Perceptor (perceptor.py).

Покрываем:
- Генерацию пути и имени выходного файла TTS
- Нормализацию скорости и параметров TTS
- Обрезку длинного текста перед отправкой в edge_tts
- Успешный сценарий text_to_speech (ffmpeg отрабатывает)
- Сценарий ошибки edge_tts (NoAudioReceived и аналоги)
- Сценарий ошибки ffmpeg (файл не создан)
- Perceptor.transcribe_audio — успешный ответ
- Perceptor.transcribe_audio — HTTP ошибка (возвращает пустую строку)
- Perceptor.transcribe_audio — пустые байты
- Perceptor.transcribe — файл не найден
- Perceptor.transcribe — чтение файла и делегация
- Perceptor.health_check — шлюз доступен
- Perceptor.health_check — шлюз недоступен
- Perceptor.speak — делегация в text_to_speech
- Очистка temp_mp3 после TTS (finally-блок)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src import voice_engine

# ---------------------------------------------------------------------------
# Вспомогательные константы
# ---------------------------------------------------------------------------

SAMPLE_TEXT = "Привет мир, это тест голосового движка."
SAMPLE_LONG_TEXT = "А" * 700  # длиннее _TTS_MAX_CHARS=600


# ---------------------------------------------------------------------------
# Вспомогательный fake ffmpeg-процесс
# ---------------------------------------------------------------------------


class _FakeProcess:
    """Минимальный ffmpeg-процесс: возвращает успешный код без реального запуска."""

    async def wait(self) -> int:
        return 0


# ===========================================================================
# Тесты voice_engine.text_to_speech
# ===========================================================================


@pytest.mark.asyncio
async def test_text_to_speech_accepts_custom_voice_and_returns_ogg_path(tmp_path: Path) -> None:
    """TTS принимает voice= и возвращает правильный путь к OGG-файлу."""
    temp_mp3_written: dict[str, str] = {}

    class _FakeCommunicate:
        def __init__(self, text: str, voice: str, rate: str) -> None:
            temp_mp3_written["text"] = text
            temp_mp3_written["voice"] = voice
            temp_mp3_written["rate"] = rate

        async def save(self, path: str) -> None:
            Path(path).write_bytes(b"mp3")

    async def _fake_exec(*args, **kwargs):  # noqa: ANN002, ANN003
        output_ogg = Path(args[-1])
        output_ogg.write_bytes(b"ogg")
        return _FakeProcess()

    with patch.object(voice_engine, "VOICE_OUTPUT_DIR", str(tmp_path)):
        with patch("src.voice_engine.edge_tts.Communicate", _FakeCommunicate):
            with patch("src.voice_engine.asyncio.create_subprocess_exec", new=_fake_exec):
                result = await voice_engine.text_to_speech(
                    "Привет",
                    filename="reply.ogg",
                    speed=1.25,
                    voice="ru-RU-SvetlanaNeural",
                )

    assert result == str(tmp_path / "reply.ogg")
    assert temp_mp3_written["voice"] == "ru-RU-SvetlanaNeural"
    assert temp_mp3_written["rate"] == "+25%"
    assert (tmp_path / "reply.ogg").exists()


@pytest.mark.asyncio
async def test_tts_returns_empty_string_when_ffmpeg_fails(tmp_path: Path) -> None:
    """text_to_speech возвращает '' если ffmpeg не создал OGG-файл."""

    class _FakeCommunicate:
        def __init__(self, *a, **kw): ...

        async def save(self, path: str) -> None:
            Path(path).write_bytes(b"mp3")

    async def _fake_exec(*args, **kwargs):
        # ffmpeg не создаёт выходной файл — имитируем сбой
        return _FakeProcess()

    with patch.object(voice_engine, "VOICE_OUTPUT_DIR", str(tmp_path)):
        with patch("src.voice_engine.edge_tts.Communicate", _FakeCommunicate):
            with patch("src.voice_engine.asyncio.create_subprocess_exec", new=_fake_exec):
                result = await voice_engine.text_to_speech(SAMPLE_TEXT, filename="missing.ogg")

    assert result == ""


@pytest.mark.asyncio
async def test_tts_returns_empty_string_on_edge_tts_exception(tmp_path: Path) -> None:
    """text_to_speech перехватывает исключение edge_tts и возвращает ''."""

    class _BrokenCommunicate:
        def __init__(self, *a, **kw): ...

        async def save(self, path: str) -> None:
            raise RuntimeError("NoAudioReceived")

    with patch.object(voice_engine, "VOICE_OUTPUT_DIR", str(tmp_path)):
        with patch("src.voice_engine.edge_tts.Communicate", _BrokenCommunicate):
            result = await voice_engine.text_to_speech(SAMPLE_TEXT)

    assert result == ""


@pytest.mark.asyncio
async def test_tts_truncates_long_text(tmp_path: Path) -> None:
    """Текст длиннее _TTS_MAX_CHARS обрезается до ближайшего пробела."""
    captured_text: list[str] = []

    class _CaptureCommunicate:
        def __init__(self, text: str, voice: str, rate: str) -> None:
            captured_text.append(text)

        async def save(self, path: str) -> None:
            Path(path).write_bytes(b"mp3")

    async def _fake_exec(*args, **kwargs):
        Path(args[-1]).write_bytes(b"ogg")
        return _FakeProcess()

    with patch.object(voice_engine, "VOICE_OUTPUT_DIR", str(tmp_path)):
        with patch("src.voice_engine.edge_tts.Communicate", _CaptureCommunicate):
            with patch("src.voice_engine.asyncio.create_subprocess_exec", new=_fake_exec):
                await voice_engine.text_to_speech(SAMPLE_LONG_TEXT)

    # Переданный в edge_tts текст не должен превышать лимит
    assert captured_text, "Communicate не был вызван"
    assert len(captured_text[0]) <= voice_engine._TTS_MAX_CHARS


@pytest.mark.asyncio
async def test_tts_uses_default_voice_when_none_passed(tmp_path: Path) -> None:
    """Когда voice=None, используется DEFAULT_VOICE."""
    used_voice: list[str] = []

    class _CaptureCommunicate:
        def __init__(self, text: str, voice: str, rate: str) -> None:
            used_voice.append(voice)

        async def save(self, path: str) -> None:
            Path(path).write_bytes(b"mp3")

    async def _fake_exec(*args, **kwargs):
        Path(args[-1]).write_bytes(b"ogg")
        return _FakeProcess()

    with patch.object(voice_engine, "VOICE_OUTPUT_DIR", str(tmp_path)):
        with patch("src.voice_engine.edge_tts.Communicate", _CaptureCommunicate):
            with patch("src.voice_engine.asyncio.create_subprocess_exec", new=_fake_exec):
                await voice_engine.text_to_speech(SAMPLE_TEXT, voice=None)

    assert used_voice[0] == voice_engine.DEFAULT_VOICE


@pytest.mark.asyncio
async def test_tts_rate_string_for_speed_1_5(tmp_path: Path) -> None:
    """rate_str для speed=1.5 должен быть '+50%'."""
    used_rate: list[str] = []

    class _CaptureCommunicate:
        def __init__(self, text: str, voice: str, rate: str) -> None:
            used_rate.append(rate)

        async def save(self, path: str) -> None:
            Path(path).write_bytes(b"mp3")

    async def _fake_exec(*args, **kwargs):
        Path(args[-1]).write_bytes(b"ogg")
        return _FakeProcess()

    with patch.object(voice_engine, "VOICE_OUTPUT_DIR", str(tmp_path)):
        with patch("src.voice_engine.edge_tts.Communicate", _CaptureCommunicate):
            with patch("src.voice_engine.asyncio.create_subprocess_exec", new=_fake_exec):
                await voice_engine.text_to_speech(SAMPLE_TEXT, speed=1.5)

    assert used_rate[0] == "+50%"


@pytest.mark.asyncio
async def test_tts_cleanup_temp_mp3_on_exception(tmp_path: Path) -> None:
    """finally-блок удаляет temp_mp3 даже при исключении в edge_tts."""

    class _BrokenCommunicate:
        def __init__(self, *a, **kw): ...

        async def save(self, path: str) -> None:
            # Создаём temp файл, чтобы finally нашёл его
            Path(path).write_bytes(b"partial")
            raise ValueError("внезапная ошибка")

    with patch.object(voice_engine, "VOICE_OUTPUT_DIR", str(tmp_path)):
        with patch("src.voice_engine.edge_tts.Communicate", _BrokenCommunicate):
            await voice_engine.text_to_speech(SAMPLE_TEXT)

    # Все temp_*.mp3 файлы должны быть удалены
    remaining = list(tmp_path.glob("temp_*.mp3"))
    assert remaining == [], f"temp файлы не удалены: {remaining}"


# ===========================================================================
# Тесты Perceptor (src/modules/perceptor.py)
# ===========================================================================


@pytest.mark.asyncio
async def test_perceptor_transcribe_audio_success() -> None:
    """transcribe_audio успешно возвращает текст из JSON-ответа шлюза."""
    from src.modules.perceptor import Perceptor

    p = Perceptor(config={})

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={"text": "Распознанный текст"})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await p.transcribe_audio(b"fake_audio_bytes")

    assert result == "Распознанный текст"


@pytest.mark.asyncio
async def test_perceptor_transcribe_audio_empty_bytes() -> None:
    """transcribe_audio возвращает '' для пустых байт, HTTP-запрос не выполняется."""
    from src.modules.perceptor import Perceptor

    p = Perceptor(config={})

    with patch("httpx.AsyncClient") as mock_cls:
        result = await p.transcribe_audio(b"")

    mock_cls.assert_not_called()
    assert result == ""


@pytest.mark.asyncio
async def test_perceptor_transcribe_audio_http_error() -> None:
    """transcribe_audio возвращает '' при HTTP ошибке, не поднимает исключение."""
    from src.modules.perceptor import Perceptor

    p = Perceptor(config={})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await p.transcribe_audio(b"audio_data")

    assert result == ""


@pytest.mark.asyncio
async def test_perceptor_transcribe_file_not_found() -> None:
    """transcribe возвращает сообщение об ошибке, если файл не найден."""
    from src.modules.perceptor import Perceptor

    p = Perceptor(config={})
    result = await p.transcribe("/nonexistent/path/audio.ogg")
    # Должен содержать сигнал об ошибке, не падать
    assert result != ""
    lower = result.lower()
    # После honest-failure patch (commit 34e06ff) Perceptor возвращает машинно-
    # читаемый диагностический токен `[transcription_failed: ...]` с причиной,
    # например `voice_gateway=file_not_found`. Проверяем либо старый русский
    # префикс, либо новый структурированный.
    assert (
        "не найден" in lower
        or "ошибка" in lower
        or "transcription_failed" in lower
        or "file_not_found" in lower
    )


@pytest.mark.asyncio
async def test_perceptor_transcribe_delegates_to_transcribe_audio(tmp_path: Path) -> None:
    """transcribe читает файл и передаёт байты в transcribe_audio."""
    from src.modules.perceptor import Perceptor

    audio_file = tmp_path / "test.ogg"
    audio_file.write_bytes(b"fake_ogg_data")

    p = Perceptor(config={})
    p.transcribe_audio = AsyncMock(return_value="Тестовый транскрипт")

    result = await p.transcribe(str(audio_file))

    p.transcribe_audio.assert_called_once_with(b"fake_ogg_data")
    assert result == "Тестовый транскрипт"


@pytest.mark.asyncio
async def test_perceptor_health_check_ok() -> None:
    """health_check возвращает True при успешном ответе шлюза."""
    from src.modules.perceptor import Perceptor

    p = Perceptor(config={})

    mock_resp = MagicMock()
    mock_resp.json = MagicMock(return_value={"ok": True})

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await p.health_check()

    assert result is True


@pytest.mark.asyncio
async def test_perceptor_health_check_unavailable() -> None:
    """health_check возвращает False если шлюз недоступен."""
    from src.modules.perceptor import Perceptor

    p = Perceptor(config={})

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await p.health_check()

    assert result is False


@pytest.mark.asyncio
async def test_perceptor_speak_delegates_to_text_to_speech() -> None:
    """speak делегирует синтез в voice_engine.text_to_speech."""
    from src.modules.perceptor import Perceptor

    p = Perceptor(config={})

    # Мокаем text_to_speech напрямую в модуле voice_engine
    with patch(
        "src.voice_engine.text_to_speech", new=AsyncMock(return_value="/tmp/voice.ogg")
    ) as mock_tts:
        result = await p.speak("Привет!", voice_id="ru-RU-DmitryNeural")

    mock_tts.assert_called_once_with("Привет!", voice="ru-RU-DmitryNeural")
    assert result == "/tmp/voice.ogg"


@pytest.mark.asyncio
async def test_perceptor_transcribe_audio_uses_transcript_key() -> None:
    """transcribe_audio работает с полем 'transcript' (альтернативный ключ шлюза)."""
    from src.modules.perceptor import Perceptor

    p = Perceptor(config={})

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={"transcript": "Альтернативный транскрипт"})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await p.transcribe_audio(b"audio_bytes")

    assert result == "Альтернативный транскрипт"
