# -*- coding: utf-8 -*-
"""
Model Manager (Router) для Krab v2.0.
Отвечает за выбор оптимальной модели (Cloud vs Local).
"""

import os
import aiohttp
import logging
from typing import Literal, Optional, Dict, Any, List
from src.core.rag_engine import RAGEngine

# Настройка логгера
import structlog
logger = structlog.get_logger("ModelRouter")

class ModelRouter:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.lm_studio_url = config.get("LM_STUDIO_URL", "http://localhost:1234/v1")
        self.ollama_url = config.get("OLLAMA_URL", "http://localhost:11434/api")
        self.gemini_key = config.get("GEMINI_API_KEY")

        # Статусы доступности
        self.is_local_available = False
        self.local_engine = None # 'lm-studio' or 'ollama'
        self.active_local_model = None
        
        # RAG Engine
        self.rag = RAGEngine()
        
        # Persona Manager (назначается в main.py)
        self.persona = None 
        self.tools = None  # Назначается в main.py (ToolHandler)        
        # Пул моделей — читаем из .env, дефолты как fallback
        # Причина: хардкод gemini-2.0-flash мешал обновлению моделей без правки кода
        self.models = {
            "chat": config.get("GEMINI_CHAT_MODEL", "gemini-2.0-flash"),
            "thinking": config.get("GEMINI_THINKING_MODEL", "gemini-2.0-flash-thinking-exp"),
            "pro": config.get("GEMINI_PRO_MODEL", "gemini-2.0-pro-exp"),
            "coding": config.get("GEMINI_CODING_MODEL", "gemini-2.0-flash"),
        }

    async def check_local_health(self):
        """Проверяет, запущен ли LM Studio или Ollama."""
        # 1. Сначала проверяем LM Studio (приоритет)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.lm_studio_url}/models", timeout=2) as response:
                    if response.status == 200:
                        data = await response.json()
                        models = data.get('data', [])
                        if models:
                            self.active_local_model = models[0]['id']
                            self.local_engine = 'lm-studio'
                            self.is_local_available = True
                            logger.info(f"Local AI Available (LM Studio): {self.active_local_model}")
                            return
        except Exception:
            pass

        # 2. Затем проверяем Ollama
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.ollama_url.replace('/api', '/v1')}/models", timeout=2) as response:
                    if response.status == 200:
                        data = await response.json()
                        models = data.get('data', [])
                        if models:
                            self.active_local_model = models[0]['id']
                            self.local_engine = 'ollama'
                            self.is_local_available = True
                            logger.info(f"Local AI Available (Ollama): {self.active_local_model}")
                            return
        except Exception:
            pass

        self.is_local_available = False
        self.local_engine = None
        self.active_local_model = None

    async def _call_local_llm(self, prompt: str, context: list = None, is_private: bool = True) -> str:
        """
        Вызов локальной модели через прямой HTTP запрос (aiohttp).
        """
        try:
            # Выбираем URL в зависимости от движка
            base_url = self.lm_studio_url if self.local_engine == 'lm-studio' else \
                       self.ollama_url.replace('/api', '/v1')

            # Формируем payload
            messages = []
            if context:
                messages.extend(context)
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": self.active_local_model or "local-model",
                "messages": messages,
                "temperature": 0.7
            }

            headers = {"Content-Type": "application/json"}
            
            # Таймаут побольше для локалки
            timeout = aiohttp.ClientTimeout(total=60)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{base_url}/chat/completions", 
                    json=payload, 
                    headers=headers
                ) as response:
                    
                    if response.status == 200:
                        data = await response.json()
                        # LOGGING RAW RESPONSE FOR DEBUGGING
                        logger.info(f"Local LLM Raw Response: {data}")
                        
                        # Защита от NoneType errors
                        choices = data.get('choices')
                        if choices and len(choices) > 0:
                            content = choices[0].get('message', {}).get('content')
                            if content:
                                return content
                        
                        logger.error(f"Local LLM Invalid Response: {data}")
                        return None # Return None to trigger fallback
                    else:
                        error_text = await response.text()
                        logger.error(f"Local LLM HTTP {response.status}: {error_text}")
                        return None # Return None to trigger fallback

        except Exception as e:
            import traceback
            logger.error(f"Local LLM Connection Error: {e}\n{traceback.format_exc()}")
            return None # Return None to trigger fallback

    async def route_query(self,
                          prompt: str,
                          task_type: Literal['coding', 'chat', 'reasoning', 'creative'] = 'chat',
                          context: list = None,
                          is_private: bool = True,
                          use_rag: bool = True):
        """
        Главный метод маршрутизации запроса с Auto-Fallback и RAG.
        """
        
        # 0. RAG Lookup
        if use_rag:
            rag_context = self.rag.query(prompt)
            if rag_context:
                prompt = f"### ДОПОЛНИТЕЛЬНЫЕ ДАННЫЕ ИЗ ТВОЕЙ ПАМЯТИ (RAG):\n{rag_context}\n\n### ТЕКУЩИЙ ЗАПРОС:\n{prompt}"

        # 0.1. Tool Orchestration (Phase 6)
        if self.tools:
            tool_data = await self.tools.execute_tool_chain(prompt)
            if tool_data:
                prompt = f"### ДАННЫЕ ИЗ ИНСТРУМЕНТОВ:\n{tool_data}\n\n### ТЕКУЩИЙ ЗАПРОС:\n{prompt}"

        # 0.5. Reasoning Mode (Thinker)
        if task_type == 'reasoning':
            logger.info("🧠 Thinking mode activated...")
            return await self._call_gemini(prompt, self.models["thinking"], context, is_private)

        await self.check_local_health() 

        # 1. Пытаемся локально (если доступно и задача простая/чат)
        if self.is_local_available and task_type in ['chat', 'coding']:
            logger.info("Routing to LOCAL", model=self.active_local_model)
            response = await self._call_local_llm(prompt, context, is_private)
            
            if response: # Если успешно
                return response
            
            logger.warning("Local LLM failed. Falling back to CLOUD.")

        # 2. Fallback или сложные задачи -> Gemini Cloud
        model_name = self.models.get(task_type, self.models["chat"])
        logger.info("Routing to CLOUD", model=model_name)

        return await self._call_gemini(prompt, model_name, context, is_private)

    async def _call_gemini(self, prompt: str, model_name: str, context: list = None, is_private: bool = True) -> str:
        """Вызов Google Gemini через Generative AI SDK."""
        try:
            import google.generativeai as genai
            
            if not self.gemini_key:
                return "❌ Ошибка: Не задан `GEMINI_API_KEY` в `.env`."

            genai.configure(api_key=self.gemini_key)
            
            # Динамический System Prompt на основе личности (Persona)
            from src.core.prompts import get_system_prompt
            base_instructions = get_system_prompt(is_private)
            
            persona_prompt = ""
            if self.persona:
                persona_prompt = self.persona.get_current_prompt()
            
            system_instructions = f"{persona_prompt}\n\n{base_instructions}"
            
            # Передаем system instruction
            model = genai.GenerativeModel(model_name, system_instruction=system_instructions)
            
            # Формируем историю
            full_prompt = prompt
            if context:
                history_str = "\n".join([f"{msg.get('role', 'user')}: {msg.get('text', '')}" for msg in context])
                full_prompt = f"History:\n{history_str}\n\nCurrent Request: {prompt}"

            response = await model.generate_content_async(full_prompt)
            
            if not response or not response.text:
                return "❌ AI вернул пустой ответ (или заблокировал контент)."
                
            return response.text
        except Exception as e:
            logger.error("Gemini API Error", error=str(e))
            return f"❌ Ошибка Gemini: {e}"

    def get_ram_usage(self) -> dict:
        """
        Проверка RAM через SystemMonitor.
        Используется перед загрузкой тяжёлых моделей (Flux, Whisper Large)
        чтобы не крашнуть систему.
        """
        try:
            from src.utils.system_monitor import SystemMonitor
            snapshot = SystemMonitor.get_snapshot()
            return {
                "total_gb": round(snapshot.ram_total_gb, 1),
                "used_gb": round(snapshot.ram_used_gb, 1),
                "available_gb": round(snapshot.ram_available_gb, 1),
                "percent": snapshot.ram_percent,
                "can_load_heavy": SystemMonitor.can_load_heavy_model()
            }
        except Exception as e:
            logger.warning(f"RAM check failed: {e}")
            return {"error": str(e), "can_load_heavy": True}  # При ошибке — разрешаем