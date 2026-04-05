"""
Голосовой движок Краба.

Что это:
- тонкая обёртка над `edge_tts` + `ffmpeg` для генерации Telegram voice reply.

Зачем нужен:
- userbot может озвучивать текстовые ответы владельцу;
- web/runtime-команды меняют голос и скорость на лету, поэтому сигнатура должна
  принимать и `speed`, и `voice`, иначе voice-ответы ломаются после переключения профиля.

Связи:
- вызывается из `src/userbot_bridge.py` и частично из модулей voice/perceptor.
"""

import asyncio
import os
import subprocess
from uuid import uuid4

import edge_tts
from structlog import get_logger

from src.core.subprocess_env import clean_subprocess_env

logger = get_logger(__name__)

VOICE_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "voice_cache")
os.makedirs(VOICE_OUTPUT_DIR, exist_ok=True)

# Максимальная длина текста для TTS: edge_tts не справляется с длинными ответами
# (возможен NoAudioReceived). 600 символов ≈ 20-25 секунд речи — оптимальный размер.
_TTS_MAX_CHARS = 600

# Russian voices: ru-RU-DmitryNeural, ru-RU-SvetlanaNeural
# English: en-US-ChristopherNeural, en-US-JennyNeural
DEFAULT_VOICE = "ru-RU-DmitryNeural"


async def text_to_speech(
    text: str,
    filename: str = "voice.ogg",
    speed: float = 1.5,
    voice: str | None = None,
) -> str:
    """
    Генерирует голос из текста и возвращает путь к Telegram-совместимому OGG/Opus.

    Почему здесь есть `voice`:
    - runtime уже хранит выбранный voice-профиль отдельно;
    - раньше `userbot_bridge` передавал `voice=...`, но функция не принимала этот
      аргумент и voice-reply падал до отправки;
    - теперь сигнатура синхронизирована с caller'ом.
    """
    temp_mp3 = os.path.join(VOICE_OUTPUT_DIR, f"temp_{uuid4().hex}.mp3")
    output_ogg = os.path.join(VOICE_OUTPUT_DIR, filename)
    selected_voice = str(voice or DEFAULT_VOICE).strip() or DEFAULT_VOICE
    
    try:
        # edge-tts принимает скорость в процентах относительно baseline.
        rate_str = f"+{int((speed - 1) * 100)}%"

        # Обрезаем слишком длинный текст: edge_tts может вернуть NoAudioReceived
        # на многоабзацных ответах. Голосовое ≤ _TTS_MAX_CHARS символов = ~20-25 сек.
        tts_text = text[:_TTS_MAX_CHARS].rsplit(" ", 1)[0] if len(text) > _TTS_MAX_CHARS else text
        logger.info(
            "tts_generation_started",
            voice=selected_voice,
            speed=speed,
            input_chars=len(text),
            emitted_chars=len(tts_text),
        )

        communicate = edge_tts.Communicate(tts_text, selected_voice, rate=rate_str)
        await communicate.save(temp_mp3)
        
        # Telegram лучше всего переваривает именно OGG/Opus voice message.
        cmd = [
            "ffmpeg", "-y",
            "-i", temp_mp3,
            "-c:a", "libopus",
            "-b:a", "32k",
            "-vbr", "on",
            "-compression_level", "10",
            output_ogg
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=clean_subprocess_env(),
        )
        await process.wait()

        if os.path.exists(output_ogg):
            logger.info(
                "tts_generation_finished",
                path=output_ogg,
                size_bytes=os.path.getsize(output_ogg),
                voice=selected_voice,
            )
            return output_ogg
        else:
            logger.error("ffmpeg_failed", path=output_ogg)
            return ""
            
    except Exception as e:  # noqa: BLE001
        # Ловим всё: edge_tts.exceptions.NoAudioReceived, aiohttp.ClientError, и т.д.
        # не наследуются от OSError/ValueError/RuntimeError, поэтому предыдущий narrow
        # catch пропускал их и крашил весь voice-flow молча.
        logger.error("tts_error", error=str(e), error_type=type(e).__name__)
        return ""
    finally:
        if os.path.exists(temp_mp3):
            os.remove(temp_mp3)
