
import asyncio
import os
import subprocess
import edge_tts
from structlog import get_logger

logger = get_logger(__name__)

VOICE_OUTPUT_DIR = "voice_cache"
os.makedirs(VOICE_OUTPUT_DIR, exist_ok=True)

# Russian voices: ru-RU-DmitryNeural, ru-RU-SvetlanaNeural
# English: en-US-ChristopherNeural, en-US-JennyNeural
DEFAULT_VOICE = "ru-RU-DmitryNeural"

async def text_to_speech(text: str, filename: str = "voice.ogg", speed: float = 1.5) -> str:
    """
    Generates speech from text, applies speed up, and converts to OGG Opus.
    Returns the path to the OGG file.
    """
    temp_mp3 = os.path.join(VOICE_OUTPUT_DIR, "temp.mp3")
    output_ogg = os.path.join(VOICE_OUTPUT_DIR, filename)
    
    try:
        # 1. Generate MP3
        # Note: edge-tts rate option is like "+50%", so 1.5x is +50%
        rate_str = f"+{int((speed - 1) * 100)}%"
        
        communicate = edge_tts.Communicate(text, DEFAULT_VOICE, rate=rate_str)
        await communicate.save(temp_mp3)
        
        # 2. Convert to OGG Opus for Telegram Voice Message
        # Telegram prefers OGG Opus.
        # We can also use ffmpeg to ensure the speed is correct if edge-tts isn't enough,
        # but edge-tts 'rate' is usually good quality.
        # Let's just convert format.
        
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
            stderr=subprocess.DEVNULL
        )
        await process.wait()
        
        if os.path.exists(output_ogg):
            return output_ogg
        else:
            logger.error("ffmpeg_failed", path=output_ogg)
            return ""
            
    except Exception as e:
        logger.error("tts_error", error=str(e))
        return ""
    finally:
        if os.path.exists(temp_mp3):
            os.remove(temp_mp3)
