
import os
import time
import logging
import numpy as np
import mlx_whisper
import requests
from dotenv import load_dotenv

logger = logging.getLogger("Core")

class AudioEngine:
    def __init__(self):
        load_dotenv()
        # Defaults
        # User requested BEST models.
        # Default (Fast-ish) -> Turbo (It's fast enough and smart)
        self.model_fast = "mlx-community/whisper-large-v3-turbo" 
        self.model_hq = "mlx-community/whisper-large-v3-turbo" # The BIG one (Turbo is the open one)
        
        self.current_model = self.model_hq # Default to HQ as requested
        
        self.gateway_url = "http://127.0.0.1:18789/v1/chat/completions" # Force Localhost
        # Hardcode fallback just in case env fails
        env_key = os.getenv("OPENCLAW_GATEWAY_TOKEN")
        self.api_key = env_key if env_key else "sk-nexus-bridge"
        
        logger.info(f"🎧 Engine Init. Default: {self.current_model}")

    def set_model_quality(self, use_max_quality: bool):
        # ... (rest of function unchanged) ...
        # Copied context to ensure alignment
        if use_max_quality:
            # "large-v3" is gated/private. "large-v3-turbo" is open and high quality.
            new_model = "mlx-community/whisper-large-v3-turbo" 
        else:
            new_model = "mlx-community/whisper-large-v3-turbo"
            
        if new_model != self.current_model:
            logger.info(f"🔄 Switching Model: {self.current_model} -> {new_model}")
            self.current_model = new_model
            return True
        return False

    def transcribe(self, audio_data):
        """
        Transcribe audio (numpy array or file path).
        """
        try:
            start = time.time()
            # Punctuation Prompt (Russian)
            prompt = "Привет, я транскрибирую этот текст с правильной пунктуацией, заглавными буквами и форматированием."
            
            result = mlx_whisper.transcribe(
                audio_data, 
                path_or_hf_repo=self.current_model,
                initial_prompt=prompt,
                language="ru", # Force Russian
                temperature=0.0, # REDUCE HALLUCINATIONS
                verbose=False
            )
            text = result['text'].strip()
            dur = time.time() - start
            logger.info(f"⚡ Transcribed ({dur:.2f}s): {text[:50]}...")
            return text
        except Exception as e:
            logger.error(f"Transcription Error: {e}")
            return f"[Error: {e}]"

    def ask_brain(self, text):
        if not text: return ""
        logger.info(f"🧠 Brain Query: {text}")
        logger.info(f"🔐 Auth Token being used: {self.api_key[:5]}...{self.api_key[-3:] if len(self.api_key)>5 else ''}")
        
        payload = {
            "model": "google/gemini-pro-latest",
            "messages": [
                {"role": "system", "content": "Ты — Краб. Отвечай кратко (1 предложение) на русском языке."},
                {"role": "user", "content": text}
            ]
        }
        
        import json
        payload_str = json.dumps(payload)
        logger.info(f"📦 PAYLOAD: {payload_str}")
        pass # Placeholder to avoid indentation error if needed, but actually we need to pass this info to UI?
        # We can't easily pass to UI here without a callback. 
        # But we can print it, causing it to appear in stdout if run from terminal.
        print(f"DEBUG_PAYLOAD: {payload_str}")
        
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        
        try:
            # Increased timeout to 30s for slower models/network
            response = requests.post(self.gateway_url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                answer = response.json()['choices'][0]['message']['content']
                return answer
            
            logger.error(f"Brain Error {response.status_code}: {response.text}")
            return f"⚠️ Error {response.status_code}: {response.text[:20]}" # Show hint in UI
        except Exception as e:
            logger.error(f"Brain fail: {e}")
            return "⚠️ Connection Error"
