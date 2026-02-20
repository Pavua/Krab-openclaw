# -*- coding: utf-8 -*-
"""
Krab Voice Bridge (MacWhisper Analog).
Standalone transcription tool using MLX Whisper.
"""

import os
import sys
import time
import logging
import asyncio
import json
import re
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("VoiceBridge")

def _parse_hotwords(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _parse_replace_map(raw: str) -> dict[str, str]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except Exception:
        logger.warning("STT_REPLACE_JSON некорректен, пропускаю пользовательские замены")
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in payload.items():
        src = str(key or "").strip()
        dst = str(value or "").strip()
        if src and dst:
            result[src] = dst
    return result


def _build_stt_prompt(hotwords: list[str]) -> str:
    base_prompt = (
        "Транскрибируй русскую речь точно, с корректной пунктуацией, "
        "заглавными буквами и абзацами по смыслу. Не добавляй лишних слов."
    )
    if not hotwords:
        return base_prompt
    return f"{base_prompt} Важные термины: {', '.join(hotwords[:40])}."


def _apply_custom_replacements(text: str, replace_map: dict[str, str]) -> str:
    fixed = text
    for raw_src, raw_dst in replace_map.items():
        src = re.escape(raw_src)
        fixed = re.sub(rf"(?<!\w){src}(?!\w)", raw_dst, fixed, flags=re.IGNORECASE)
    return fixed


def _capitalize_sentences(text: str) -> str:
    chars = list(text)
    need_upper = True
    for idx, ch in enumerate(chars):
        if need_upper and ch.isalpha():
            chars[idx] = ch.upper()
            need_upper = False
        if ch in ".!?":
            need_upper = True
        elif not ch.isspace() and ch not in "\"'«»()[]{}":
            need_upper = False
    return "".join(chars)


def _postprocess_transcript(raw_text: str, replace_map: dict[str, str]) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return ""
    if text.startswith(("!", "/")):
        return text
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,.;:!?])(?=[^\s\"')\]»}])", r"\1 ", text)
    text = re.sub(r"([!?.,])\1{2,}", r"\1", text)
    text = text.strip()
    if len(text.split()) >= 5 and text[-1] not in ".!?":
        text += "."
    text = _capitalize_sentences(text)
    text = _apply_custom_replacements(text, replace_map)
    return text


async def transcribe_file(file_path: str, model_name: str = "mlx-community/whisper-large-v3-turbo"):
    if not os.path.exists(file_path):
        print(f"❌ Error: File {file_path} not found.")
        return

    try:
        import mlx_whisper
        stt_language = str(os.getenv("STT_LANGUAGE", "ru")).strip() or "ru"
        stt_temperature = float(os.getenv("STT_TEMPERATURE", "0.0"))
        stt_beam_size = int(os.getenv("STT_BEAM_SIZE", "7"))
        stt_best_of = int(os.getenv("STT_BEST_OF", "5"))
        stt_patience = float(os.getenv("STT_PATIENCE", "1.0"))
        stt_condition_on_previous_text = str(
            os.getenv("STT_CONDITION_ON_PREVIOUS_TEXT", "1")
        ).strip().lower() in {"1", "true", "yes", "on"}
        stt_no_speech_threshold = float(os.getenv("STT_NO_SPEECH_THRESHOLD", "0.45"))
        stt_compression_ratio_threshold = float(os.getenv("STT_COMPRESSION_RATIO_THRESHOLD", "2.4"))
        stt_hotwords = _parse_hotwords(os.getenv("STT_HOTWORDS", ""))
        stt_replace_map = _parse_replace_map(os.getenv("STT_REPLACE_JSON", ""))

        print(f"🎤 Transcribing: {os.path.basename(file_path)}")
        print(f"🧠 Model: {model_name}")
        
        start_ts = time.time()

        primary_kwargs = {
            "initial_prompt": _build_stt_prompt(stt_hotwords),
            "language": stt_language,
            "temperature": stt_temperature,
            "beam_size": stt_beam_size,
            "best_of": stt_best_of,
            "patience": stt_patience,
            "condition_on_previous_text": stt_condition_on_previous_text,
            "no_speech_threshold": stt_no_speech_threshold,
            "compression_ratio_threshold": stt_compression_ratio_threshold,
            "verbose": False,
        }
        fallback_kwargs = {
            "initial_prompt": _build_stt_prompt(stt_hotwords),
            "language": stt_language,
            "temperature": stt_temperature,
            "verbose": False,
        }
        try:
            result = await asyncio.to_thread(
                mlx_whisper.transcribe,
                file_path,
                path_or_hf_repo=model_name,
                **primary_kwargs,
            )
        except TypeError as exc:
            logger.warning("mlx_whisper не поддерживает полный профиль STT, откат на базовый: %s", exc)
            result = await asyncio.to_thread(
                mlx_whisper.transcribe,
                file_path,
                path_or_hf_repo=model_name,
                **fallback_kwargs,
            )

        text = _postprocess_transcript(result.get("text", ""), stt_replace_map)
        duration = time.time() - start_ts
        
        print(f"\n✅ Done in {duration:.2f}s!")
        print("-" * 30)
        print(text)
        print("-" * 30)
        
        # Copy to clipboard if on macOS
        try:
            import subprocess
            process = subprocess.Popen('pbcopy', env={'LANG': 'en_US.UTF-8'}, stdin=subprocess.PIPE)
            process.communicate(text.encode('utf-8'))
            print("📋 Text copied to clipboard.")
        except:
            pass
            
        return text

    except ImportError:
        print("❌ Error: mlx-whisper not installed. Run: pip install mlx-whisper")
    except Exception as e:
        print(f"❌ Transcription failed: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python voice_bridge.py <audio_file_path>")
        sys.exit(1)
        
    audio_file = sys.argv[1]
    asyncio.run(transcribe_file(audio_file))
