import os
from typing import Dict, Any
import google.generativeai as genai
from openai import OpenAI
from dotenv import load_dotenv
from utils.logger import setup_logger

# Load env vars
load_dotenv(dotenv_path="../.env")

# Configure logging
logger = setup_logger("Analyst")

class AnalystAgent:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.name = config.get("name", "Аналитик")
        self.provider = os.getenv("LLM_PROVIDER", "gemini")
        
        if self.provider == "gemini":
            self.api_key = os.getenv("GEMINI_API_KEY")
            if not self.api_key:
                logger.error("GEMINI_API_KEY не найден в .env")
                raise ValueError("GEMINI_API_KEY отсутствует")
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(config.get("model", "gemini-pro"))
            logger.info(f"{self.name} инициализирован (Gemini Pro).")
            
        elif self.provider == "local":
            self.local_url = os.getenv("LOCAL_LLM_URL", "http://localhost:1234/v1")
            self.client = OpenAI(base_url=self.local_url, api_key="lm-studio")
            logger.info(f"{self.name} инициализирован (Local LLM: {self.local_url}).")
            
        else:
            logger.error(f"Неизвестный провайдер LLM: {self.provider}")
            raise ValueError(f"Unknown LLM_PROVIDER: {self.provider}")

    async def analyze(self, raw_data: str, focus: str = "general") -> str:
        """
        Анализирует сырые данные с помощью LLM для определения настроений.
        """
        logger.info(f"Анализ данных ({self.provider}). Фокус: {focus}")
        
        prompt = f"""
        Ты — Ведущий Крипто-Финансовый Аналитик. 
        Проанализируй следующие данные, собранные агентом-разведчиком.
        
        Тема/Фокус: {focus}
        
        Определи:
        1. Настроение рынка (Бычье/Медвежье/Нейтральное)?
        2. Ключевые риски.
        3. Потенциальные возможности.
        
        Сырые данные:
        {raw_data}
        
        Предоставь краткое резюме на РУССКОМ языке в формате Markdown.
        """
        
        try:
            if self.provider == "gemini":
                response = self.model.generate_content(prompt)
                return response.text
            elif self.provider == "local":
                response = self.client.chat.completions.create(
                    model="local-model", # Usually ignored by LM Studio
                    messages=[
                        {"role": "system", "content": "Ты полезный финансовый помощник."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7
                )
                return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Ошибка анализа ({self.provider}): {e}")
            return f"Ошибка во время анализа: {e}"

# Test
if __name__ == "__main__":
    agent = AnalystAgent({"name": "Analyst", "model": "gemini-pro"})
    import asyncio
    print(asyncio.run(agent.analyze("Bitcoin hit 100k today! Everyone is buying.", "BTC")))
