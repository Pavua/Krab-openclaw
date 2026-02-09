import aiohttp
import logging
from config.settings import Config

logger = logging.getLogger("Nexus.AI")

class AIManager:
    def __init__(self, db_manager):
        self.db = db_manager
        # OpenClaw Gateway controls the model, but we can hint preference via headers or payload if supported.
        # For now, we trust the Gateway's default or configured model.
        self.current_model = self.db.get_setting("current_model", Config.DEFAULT_MODEL)
    
    def set_model(self, model_id):
        self.current_model = model_id
        self.db.set_setting("current_model", model_id)
        logger.info(f"🧠 Model/Persona request set to: {model_id}")

    def get_model(self):
        return self.current_model

    async def ask(self, text, system_prompt):
        """
        Proxies the request to the OpenClaw Gateway.
        """
        payload = {
            "model": self.current_model, # OpenClaw might ignore this if forcing a specific agent
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            "stream": False
        }

        headers = {
            "Authorization": f"Bearer {Config.OPENCLAW_API_KEY}",
            "Content-Type": "application/json"
        }

        # OpenClaw Gateway (Node.js) usually runs on 18789
        target_url = Config.OPENCLAW_API_URL

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(target_url, json=payload, headers=headers, timeout=120) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data['choices'][0]['message']['content']
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ OpenClaw Gateway Error {response.status}: {error_text}")
                        return f"❌ OpenClaw Error: {response.status} - {error_text}"

        except Exception as e:
            logger.error(f"Gateway connection error: {e}")
            return f"❌ Connection Error: Is OpenClaw Gateway running on {target_url}? ({e})"

    async def ask_with_media(self, text, media_path, system_prompt):
        """
        Multimodal proxy.
        """
        import base64
        try:
            with open(media_path, "rb") as f:
                media_data = base64.b64encode(f.read()).decode('utf-8')
        except Exception as e:
            return f"❌ Userbot File Error: {e}"

        payload = {
            "model": self.current_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": text or "Analyze this image."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{media_data}"}} 
                ]}
            ],
            "stream": False
        }

        headers = {
            "Authorization": f"Bearer {Config.OPENCLAW_API_KEY}",
            "Content-Type": "application/json"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(Config.OPENCLAW_API_URL, json=payload, headers=headers, timeout=120) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data['choices'][0]['message']['content']
                    else:
                        return f"❌ OpenClaw Vision Error: {response.status}"
        except Exception as e:
            return f"❌ Connection Error: {e}"
