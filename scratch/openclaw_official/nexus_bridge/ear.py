
import os
import sys
import logging
import requests
import json
import time
import subprocess
import numpy as np
from dotenv import load_dotenv

# MLX Whisper (Apple Silicon Optimized)
import mlx_whisper

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [EAR] - %(levelname)s - %(message)s')
logger = logging.getLogger("Ear")

class Ear:
    def __init__(self):
        load_dotenv()
        self.gateway_url = os.getenv("OPENCLAW_URL", "http://127.0.0.1:18789/v1/chat/completions") # Use 127.0.0.1 explicitly
        self.api_key = os.getenv("OPENCLAW_GATEWAY_TOKEN", "sk-nexus-bridge")
        
        # M4 Max Optimization: Use mlx-community/whisper-large-v3-turbo (faster) or large-v3
        # We'll use large-v3-turbo for speed + accuracy on M4 Max.
        self.model_path = "mlx-community/whisper-large-v3-turbo" 
        
        logger.info(f"👂 Loading MLX Whisper ({self.model_path}) on M4 Max...")
        # MLX loads lazily, but let's do a warm-up
        try:
            # Warmup with silence
            logger.info("🔥 Warming up Neural Engine...")
            self.transcribe_audio(np.zeros(16000, dtype=np.float32), warmup=True)
            logger.info("✅ MLX Model Ready.")
        except Exception:
            # First run might download model, that's fine.
            pass

    def transcribe_audio(self, audio_data, warmup=False):
        """
        Transcribe raw float32 audio data using MLX.
        """
        try:
            # mlx_whisper.transcribe handles numpy arrays directly (16kHz mono)
            start = time.time()
            result = mlx_whisper.transcribe(
                audio_data, 
                path_or_hf_repo=self.model_path,
                verbose=False
            )
            
            text = result['text'].strip()
            
            if not warmup:
                logger.info(f"⚡ Transcribed in {time.time()-start:.2f}s: '{text}'")
            
            return text
            
        except Exception as e:
            if not warmup:
                logger.error(f"Transcription Error: {e}")
            return ""

    def ask_brain(self, text):
        if not text: return ""
        
        logger.info(f"🧠 Sending to Brain: {text}")
        payload = {
            "model": "google/gemini-2.0-flash-exp", # Fast model
            "messages": [
                {"role": "system", "content": "You are Krab, a concise AI assistant. Answer in 1 short sentence."},
                {"role": "user", "content": text}
            ]
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        try:
            response = requests.post(self.gateway_url, json=payload, headers=headers, timeout=10)
            if response.status_code == 200:
                answer = response.json()['choices'][0]['message']['content']
                logger.info(f"🤖 Brain: {answer}")
                return answer
            else:
                logger.error(f"Brain Error: {response.status_code}")
                return "⚠️ Brain Offline"
        except Exception as e:
            logger.error(f"Brain Connection: {e}")
            return "⚠️ Connection Error"

    def speak(self, text):
        # Local TTS
        subprocess.run(["say", "-r", "180", text])

if __name__ == "__main__":
    print("Ear Module Loaded.")
