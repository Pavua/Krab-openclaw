import os
import logging
from typing import Dict, Any
from openai import OpenAI
from utils.logger import setup_logger

logger = setup_logger("Coder")

class CoderAgent:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.name = config.get("name", "Кодер")
        
        # Coder always prefers Local LLM effectively (or Gemini via adapter if configured)
        # But we'll follow the same pattern
        self.provider = os.getenv("LLM_PROVIDER", "gemini") 
        self.local_url = os.getenv("LOCAL_LLM_URL", "http://localhost:1234/v1")
        
        if self.provider == "local":
             self.client = OpenAI(base_url=self.local_url, api_key="lm-studio")
        else:
             # Fallback to local or raise warning? For now, we assume Coder needs an LLM.
             # If provider is gemini, we can't easily use 'client.chat.completions' without adapter.
             # So for simplicity in this MVP, Coder uses Local if available, else dummy.
             self.client = OpenAI(base_url=self.local_url, api_key="lm-studio")

        logger.info(f"{self.name} инициализирован.")

    async def generate_code(self, task: str) -> str:
        logger.info(f"Генерация кода по задаче: {task}")
        
        prompt = f"""
        Ты — Опытный Python Разработчик.
        Напиши код для решения следующей задачи:
        {task}
        
        Верни ТОЛЬКО код внутри блока ```python ... ```. Не добавляй лишних объяснений.
        """
        
        try:
            response = self.client.chat.completions.create(
                model="local-model",
                messages=[
                    {"role": "system", "content": "Ты пишешь чистый, рабочий код на Python."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Ошибка генерации кода: {e}")
            return f"Произошла ошибка при генерации кода: {e}. Убедитесь, что LM Studio запущен в режиме Server."
