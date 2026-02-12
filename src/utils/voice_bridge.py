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
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("VoiceBridge")

async def transcribe_file(file_path: str, model_name: str = "mlx-community/whisper-large-v3-turbo"):
    if not os.path.exists(file_path):
        print(f"❌ Error: File {file_path} not found.")
        return

    try:
        import mlx_whisper
        import numpy as np
        
        print(f"🎤 Transcribing: {os.path.basename(file_path)}")
        print(f"🧠 Model: {model_name}")
        
        start_ts = time.time()
        
        result = await asyncio.to_thread(
            mlx_whisper.transcribe,
            file_path,
            path_or_hf_repo=model_name,
            language="ru",
            temperature=0.0,
            verbose=False
        )
        
        text = result.get("text", "").strip()
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
