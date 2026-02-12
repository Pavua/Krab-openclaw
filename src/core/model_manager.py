# -*- coding: utf-8 -*-
"""
Model Manager (Router) для Krab v6.5.
Отвечает за выбор оптимальной модели (Cloud vs Local).

Стратегия: Local First → Cloud Fallback.
- При доступности LM Studio/Ollama — используем их (приватность + скорость)
- При ошибке или недоступности — автоматический fallback на Gemini Cloud
- RAG и Tool Orchestration работают на КАЖДЫЙ запрос
"""

import os
import time
import asyncio
import aiohttp
from typing import Literal, Optional, Dict, Any, List
# from src.core.rag_engine import RAGEngine # Deprecated

# Настройка логгера
import structlog
logger = structlog.get_logger("ModelRouter")

# Gemini SDK (New v1.0+)
try:
    from google import genai
    from google.genai import types
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False
    genai = None

class ModelRouter:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.lm_studio_url = config.get("LM_STUDIO_URL", "http://localhost:1234/v1")
        self.ollama_url = config.get("OLLAMA_URL", "http://localhost:11434/api")
        self.gemini_key = config.get("GEMINI_API_KEY")

        # Статусы доступности
        self.is_local_available = False
        self.local_engine = None  # 'lm-studio' or 'ollama'
        self.active_local_model = None

        # Кеш для health-check (чтобы не дёргать API на каждый запрос)
        self._health_cache_ts = 0
        self._health_cache_ttl = 30  # секунд

        # Gemini SDK — конфигурируем ОДИН РАЗ
        self.gemini_client = None
        if _GENAI_AVAILABLE and self.gemini_key:
            try:
                self.gemini_client = genai.Client(api_key=self.gemini_key)
                logger.info("☁️ Gemini SDK (google-genai) configured successfully")
            except Exception as e:
                logger.error(f"Failed to init Gemini Client: {e}")

        # RAG Engine (Deprecated, use OpenClaw)
        self.rag = None # RAGEngine()

        # Persona Manager (назначается в main.py)
        self.persona = None
        self.tools = None  # Назначается в main.py (ToolHandler)

        # Пул моделей — читаем из .env, дефолты как fallback
        self.models = {
            "chat": config.get("GEMINI_CHAT_MODEL", "gemini-2.5-flash"),
            "thinking": config.get("GEMINI_THINKING_MODEL", "gemini-2.0-flash"),
            "pro": config.get("GEMINI_PRO_MODEL", "gemini-2.5-pro"),
            "coding": config.get("GEMINI_CODING_MODEL", "gemini-2.5-flash"),
        }

        # Счётчики (для диагностики)
        self._stats = {
            "local_calls": 0,
            "cloud_calls": 0,
            "local_failures": 0,
            "cloud_failures": 0,
        }

        # Fallback модели (для Gemini Quota Handling)
        self.fallback_models = [
            "gemini-2.0-flash-lite-preview-02-05", # Flash Lite (User requested)
            "gemini-2.0-flash",         # Если 2.5 занят
            "gemini-2.0-flash-001",     # Стабильная версия
            "gemini-flash-latest",      # Алиас на актуальную flash
            "gemini-pro-latest"         # Алиас на актуальную pro
        ]
        
        # Режим работы: 'auto', 'force_local', 'force_cloud'
        self.force_mode = "auto"

    def set_force_mode(self, mode: Literal['auto', 'local', 'cloud']) -> str:
        """Переключает режим работы роутера."""
        if mode not in ['auto', 'local', 'cloud']:
            return "❌ Неверный режим. Используй: auto, local, cloud"
        
        old = self.force_mode
        if mode == 'local':
            self.force_mode = 'force_local'
        elif mode == 'cloud':
            self.force_mode = 'force_cloud'
        else:
            self.force_mode = 'auto'
            
        return f"Режим изменен: {old} -> {self.force_mode}"

    async def check_local_health(self, force: bool = False) -> bool:
        """
        Проверяет, запущен ли LM Studio или Ollama.
        """
        now = time.time()
        if not force and (now - self._health_cache_ts) < self._health_cache_ttl:
            return self.is_local_available

        self._health_cache_ts = now

        # 1. Сначала проверяем LM Studio (приоритет)
        # Проверяем ТОЛЬКО /v1 endpoint, чтобы не спамить в логи LM Studio ошибками доступа к корню
        candidates = []
        if self.lm_studio_url.endswith("/v1"):
            candidates.append(self.lm_studio_url)
        else:
            candidates.append(f"{self.lm_studio_url}/v1")
            
        # Убрали fallback на root URL, так как 99% OpenAI-compatible серверов живут на /v1

        for base_url in candidates:
            try:
                # [NEW] Auto-correct loaded model if needed (via lms CLI)
                if force:
                    await self._ensure_chat_model_loaded()

                # Увеличен таймаут до 3 сек для надежности
                timeout = aiohttp.ClientTimeout(total=3)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(f"{base_url}/models") as response:
                        if response.status == 200:
                            data = await response.json()
                            models = data.get('data', [])
                            if models:
                                self.active_local_model = models[0]['id']
                                
                                # Check if it's an embedding model
                                if "embed" in self.active_local_model.lower():
                                     if force:
                                         logger.warning(f"⚠️ Text Embedding model detected ({self.active_local_model}). Attempting to switch to Chat model...")
                                         if await self._ensure_chat_model_loaded():
                                             continue # Retry probe
                                     else:
                                         logger.warning(f"⚠️ Warning: Active model '{self.active_local_model}' appears to be an embedding model!")

                                self.local_engine = 'lm-studio'
                                self.is_local_available = True
                                self.lm_studio_url = base_url
                                logger.info(f"Local AI Available (LM Studio): {self.active_local_model} at {base_url}")
                                return True
            except Exception:
                continue

    async def _ensure_chat_model_loaded(self) -> bool:
        """
        Пытается загрузить Chat-модель через 'lms' CLI, если она не загружена.
        """
        lms_path = os.path.expanduser("~/.lmstudio/bin/lms")
        if not os.path.exists(lms_path):
            return False

        try:
            # 1. Проверяем текущую загруженную модель
            proc = await asyncio.create_subprocess_exec(
                lms_path, "ps",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode()
            
            # Если есть 'Text Embedding', выгружаем
            if "embed" in output.lower():
                # Парсим ID (упрощенно: берем первое слово или ищем идентификатор)
                model_id = output.split()[0] if output.split() else "all"
                if "LOADED" not in output: # No models loaded
                     pass
                else:
                    logger.info("🔄 Unloading Embedding Model...")
                    await asyncio.create_subprocess_exec(lms_path, "unload", "--all")
                    await asyncio.sleep(2) # Wait for unload

            # 2. Проверяем снова ps, если пусто - грузим
            proc_ps = await asyncio.create_subprocess_exec(
                lms_path, "ps",
                stdout=asyncio.subprocess.PIPE
            )
            out_ps, _ = await proc_ps.communicate()
            if "LOADED" in out_ps.decode() and "embed" not in out_ps.decode().lower():
                return True # Уже загружена Chat модель

            # 3. Ищем доступные
            proc_ls = await asyncio.create_subprocess_exec(
                lms_path, "ls",
                stdout=asyncio.subprocess.PIPE
            )
            out_ls, _ = await proc_ls.communicate()
            available = out_ls.decode().splitlines()
            
            # Ищем что-то похожее на Chat/Instruct
            chat_candidate = None
            for line in available:
                lower = line.lower()
                if ("instruct" in lower or "chat" in lower or "llama" in lower or "qwen" in lower) and "embed" not in lower:
                     # lms ls output: "slug   SIZE   ARCH..."
                     # We need the slug (first column)
                     parts = line.split()
                     if parts:
                        chat_candidate = parts[0]
                        break
            
            if chat_candidate:
                logger.info(f"🚀 Auto-Loading Local Model: {chat_candidate}")
                await asyncio.create_subprocess_exec(lms_path, "load", chat_candidate, "--gpu", "auto")
                await asyncio.sleep(5) # Wait for load
                return True
            else:
                logger.warning("⚠️ No Chat models found in 'lms ls'.")
                return False

        except Exception as e:
            logger.error(f"❌ Auto-load failed: {e}")
            return False

    async def list_local_models(self) -> List[str]:
        """Сканирует доступные локальные модели (lms ls)."""
        lms_path = os.path.expanduser("~/.lmstudio/bin/lms")
        if not os.path.exists(lms_path):
            return ["Ошибка: lms CLI не найден"]

        try:
            proc = await asyncio.create_subprocess_exec(
                lms_path, "ls",
                stdout=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            models = []
            for line in stdout.decode().splitlines():
                if not line.strip() or "SIZE" in line: continue
                parts = line.split()
                if parts:
                    models.append(parts[0])
            return models
        except Exception as e:
            return [f"Ошибка сканирования: {e}"]

    async def list_cloud_models(self) -> List[str]:
        """Сканирует доступные Cloud модели (Gemini)."""
        if not self.gemini_client:
            return ["Ошибка: Gemini клиент не инициализирован"]
        
        try:
            # Используем list_models из v1 SDK
            # client.models.list(config={'page_size': 100}) - check iterator
            models = []
            async for m in await asyncio.to_thread(self.gemini_client.models.list):
                if "generateContent" in m.supported_generation_methods:
                    models.append(m.name.split("/")[-1]) # models/gemini-1.5 -> gemini-1.5
            return sorted(models)
        except Exception as e:
            # Fallback for old SDK logic or errors
            logger.error(f"Cloud scan error: {e}")
            return [f"Ошибка API: {e}"]

        # 2. Затем проверяем Ollama
        try:
            timeout = aiohttp.ClientTimeout(total=2)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.ollama_url.replace('/api', '/v1')}/models") as response:
                    if response.status == 200:
                        data = await response.json()
                        models = data.get('data', [])
                        if models:
                            self.active_local_model = models[0]['id']
                            self.local_engine = 'ollama'
                            self.is_local_available = True
                            logger.info(f"Local AI Available (Ollama): {self.active_local_model}")
                            return True
        except Exception:
            pass

        self.is_local_available = False
        self.local_engine = None
        self.active_local_model = None
        return False

    async def _call_local_llm(self, prompt: str, context: list = None, chat_type: str = "private", is_owner: bool = False) -> str:
        """
        Вызов локальной модели через прямой HTTP запрос (aiohttp).
        """
        try:
            # Динамический System Prompt для локалки
            system_msg = "You are a helpful assistant."
            if self.persona:
                system_msg = self.persona.get_current_prompt(chat_type, is_owner)

            # Выбираем URL в зависимости от движка
            base_url = self.lm_studio_url if self.local_engine == 'lm-studio' else \
                       self.ollama_url.replace('/api', '/v1')

            # Формируем payload
            messages = [{"role": "system", "content": system_msg}]
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
                        # debug log removed to reduce noise
                        
                        choices = data.get('choices')
                        if choices and len(choices) > 0:
                            content = choices[0].get('message', {}).get('content')
                            if content:
                                return content
                        
                        return None 
                    else:
                        error_text = await response.text()
                        logger.error(f"Local LLM HTTP {response.status}: {error_text}")
                        return None 

        except Exception as e:
            self._stats["local_failures"] += 1
            return None  

    async def route_query(self,
                          prompt: str,
                          task_type: Literal['coding', 'chat', 'reasoning', 'creative'] = 'chat',
                          context: list = None,
                          chat_type: str = "private",
                          is_owner: bool = False,
                          use_rag: bool = True):
        """
        Главный метод маршрутизации запроса с Auto-Fallback и RAG.
        """
        
        # 0. RAG Lookup
        # 0. RAG Lookup
        if use_rag and self.rag:
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
            return await self._call_gemini(prompt, self.models["thinking"], context, chat_type, is_owner)

        # === FORCE CLOUD MODE ===
        if self.force_mode == 'force_cloud':
            model_name = self.models.get(task_type, self.models["chat"])
            return await self._call_gemini(prompt, model_name, context, chat_type, is_owner)

        # === LOCAL MODE (Auto or Forced) ===
        await self.check_local_health() 

        use_local = False
        if self.force_mode == 'force_local':
            if self.is_local_available:
                use_local = True
            else:
                return "❌ Режим 'Force Local' включен, но локальная модель недоступна (LM Studio/Ollama offline)."
        elif self.is_local_available and task_type in ['chat', 'coding']:
            use_local = True

        if use_local:
            logger.info("Routing to LOCAL", model=self.active_local_model)
            response = await self._call_local_llm(prompt, context, chat_type, is_owner)

            if response:
                self._stats["local_calls"] += 1
                return response
            
            if self.force_mode == 'force_local':
                return "❌ Ошибка генерации локальной модели (Force Local active)."
                
            logger.warning("Local LLM failed. Falling back to CLOUD.")

        # === CLOUD FALLBACK ===
        model_name = self.models.get(task_type, self.models["chat"])
        return await self._call_gemini(prompt, model_name, context, chat_type, is_owner)

    async def _call_gemini(self, prompt: str, model_name: str, context: list = None,
                           chat_type: str = "private", is_owner: bool = False, max_retries: int = 2) -> str:
        """
        Вызов Google Gemini через google-genai SDK (v1.0+).
        """
        if not self.gemini_client:
            return "❌ Ошибка: Gemini SDK не инициализирован. Проверь `GEMINI_API_KEY` в `.env`."

        # Динамический System Prompt
        from src.core.prompts import get_system_prompt
        # Нам не нужен старый get_system_prompt(is_private) если у нас есть PersonaManager
        # Но для совместимости оставим как базу или заменим
        base_instructions = get_system_prompt(chat_type == "private")

        persona_prompt = ""
        if self.persona:
            persona_prompt = self.persona.get_current_prompt(chat_type, is_owner)

        system_instructions = f"{persona_prompt}\n\n{base_instructions}"

        # Формируем историю для контекста (если есть)
        # В новом SDK контекст лучше передавать через contents, но пока упростим:
        full_content = prompt
        if context:
            history_str = "\n".join(
                [f"{msg.get('role', 'user')}: {msg.get('text', '')}" for msg in context]
            )
            full_content = f"History:\n{history_str}\n\nCurrent Request: {prompt}"

        # Конфигурируем запрос
        # В новом SDK system_instruction передается в config
        config = types.GenerateContentConfig(
            system_instruction=system_instructions,
            temperature=0.7
        )

        for attempt in range(max_retries + 1):
            try:
                # Асинхронный вызов через to_thread (SDK v1.0 кажется синхронный, или имеет async методы?)
                # Клиент SDK v1.0 имеет .aio.Client для асинхронности, но мы сейчас инициализируем синхронный Client.
                # Поэтому используем asyncio.to_thread для неблокирующего вызова.
                
                response = await asyncio.to_thread(
                    self.gemini_client.models.generate_content,
                    model=model_name,
                    contents=full_content,
                    config=config
                )

                if not response or not response.text:
                    return "❌ AI вернул пустой ответ."

                self._stats["cloud_calls"] += 1
                return response.text

            except Exception as e:
                error_str = str(e)
                
                # Quota Check (429)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                     self._stats["cloud_failures"] += 1
                     logger.error("Gemini Quota Exhausted", error=error_str, model=model_name)
                     
                     if model_name in self.fallback_models:
                         fallback_idx = self.fallback_models.index(model_name)
                         if fallback_idx + 1 < len(self.fallback_models):
                             next_model = self.fallback_models[fallback_idx + 1]
                             logger.warning(f"Falling back to {next_model} due to quota limit")
                             return await self._call_gemini(prompt, next_model, context, chat_type, is_owner, max_retries=1)

                     if model_name not in self.fallback_models and self.fallback_models:
                         next_model = self.fallback_models[0]
                         return await self._call_gemini(prompt, next_model, context, chat_type, is_owner, max_retries=1)

                     return f"❌ Квота Gemini исчерпана."

                logger.warning(f"Gemini Attempt {attempt+1} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                
                self._stats["cloud_failures"] += 1
                return f"❌ Ошибка Gemini: {e}"

    async def route_query_stream(self,
                          prompt: str,
                          task_type: Literal['coding', 'chat', 'reasoning', 'creative'] = 'chat',
                          context: list = None,
                          chat_type: str = "private",
                          is_owner: bool = False,
                          use_rag: bool = True):
        """
        Версия route_query с поддержкой стриминга (пока только для Cloud).
        """
        # 1. Сначала делаем всю подготовку (RAG, Tools) - такая же как в route_query
        if use_rag and self.rag:
            rag_context = self.rag.query(prompt)
            if rag_context:
                prompt = f"### ДОПОЛНИТЕЛЬНЫЕ ДАННЫЕ ИЗ ТВОЕЙ ПАМЯТИ (RAG):\n{rag_context}\n\n### ТЕКУЩИЙ ЗАПРОС:\n{prompt}"

        if self.tools:
            tool_data = await self.tools.execute_tool_chain(prompt)
            if tool_data:
                prompt = f"### ДАННЫЕ ИЗ ИНСТРУМЕНТОВ:\n{tool_data}\n\n### ТЕКУЩИЙ ЗАПРОС:\n{prompt}"

        # 2. Стриминг пока только для Gemini
        model_name = self.models.get(task_type, self.models["chat"])
        
        # Если включен Force Local, стриминг может не работать стандартно, 
        # поэтому для простоты в Force Local отдаем полный ответ через обычный route_query
        if self.force_mode == 'force_local' or (self.is_local_available and task_type in ['chat', 'coding']):
             full_res = await self.route_query(prompt, task_type, context, chat_type, is_owner, use_rag=False) # rag already applied
             yield full_res
             return

        async for chunk in self._call_gemini_stream(prompt, model_name, context, chat_type, is_owner):
            yield chunk

    async def _call_gemini_stream(self, prompt: str, model_name: str, context: list = None,
                                  chat_type: str = "private", is_owner: bool = False):
        """
        Генератор для стриминга ответов из Gemini.
        """
        if not self.gemini_client:
            yield "❌ Ошибка: Gemini SDK не инициализирован."
            return

        from src.core.prompts import get_system_prompt
        system_instructions = f"{self.persona.get_current_prompt(chat_type, is_owner) if self.persona else ''}\n\n{get_system_prompt(chat_type == 'private')}"

        full_content = prompt
        if context:
            history_str = "\n".join([f"{msg.get('role', 'user')}: {msg.get('text', '')}" for msg in context])
            full_content = f"History:\n{history_str}\n\nCurrent Request: {prompt}"

        config = types.GenerateContentConfig(system_instruction=system_instructions, temperature=0.7)

        try:
            # Используем генератор из SDK
            # В новом SDK aio.Client.models.generate_content_stream вернет асинхронный итератор
            # Но мы инициализировали синхронный Client. 
            # Для асинхронного стриминга лучше использовать aio клиент.
            
            # Переключимся на асинхронный вызов если возможно, или используем обычный цикл
            # Так как мы в Ralph Mode, я подправлю __init__ позже если нужно, 
            # но пока используем синхронный стрим через to_thread (неэффективно)
            # ЛУЧШЕ: Создать временный aio клиент
            
            async_client = genai.Client(api_key=self.gemini_key, http_options={'api_version': 'v1alpha'}) # or v1
            
            response_stream = await async_client.aio.models.generate_content_stream(
                model=model_name,
                contents=full_content,
                config=config
            )
            
            full_text = ""
            async for chunk in response_stream:
                if chunk.text:
                    full_text += chunk.text
                    yield full_text
            
            self._stats["cloud_calls"] += 1
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"❌ Ошибка стриминга: {e}"

    async def diagnose(self) -> dict:
        """
        Полная диагностика всех подсистем.
        """
        result = {}

        # 1. Локальные модели
        local_ok = await self.check_local_health(force=True)
        result["Local AI"] = {
            "ok": local_ok,
            "status": f"{self.local_engine}: {self.active_local_model}" if local_ok else "Offline",
        }

        # 2. Gemini Cloud
        gemini_ok = self.gemini_client is not None
        result["Gemini Cloud"] = {
            "ok": gemini_ok,
            "status": f"Ready ({self.models['chat']})" if gemini_ok else "No API Key",
        }

        # 3. RAG Engine
        if self.rag:
            try:
                rag_count = self.rag.get_total_documents()
                result["RAG Engine"] = {"ok": True, "status": f"{rag_count} documents"}
            except Exception as e:
                result["RAG Engine"] = {"ok": False, "status": str(e)}
        else:
             result["RAG Engine"] = {"ok": True, "status": "Disabled (OpenClaw)"}

        # 4. Статистика вызовов
        result["Call Stats"] = {
            "ok": True,
            "status": (
                f"Local: {self._stats['local_calls']} ok / {self._stats['local_failures']} fail, "
                f"Cloud: {self._stats['cloud_calls']} ok / {self._stats['cloud_failures']} fail"
            ),
        }

        # 5. RAM
        ram_info = self.get_ram_usage()
        if "error" not in ram_info:
            result["System RAM"] = {
                "ok": ram_info["percent"] < 90,
                "status": f"{ram_info['used_gb']}/{ram_info['total_gb']} GB ({ram_info['percent']}%)",
            }
        else:
            result["System RAM"] = {"ok": True, "status": "N/A"}

        return result

    def get_model_info(self) -> dict:
        """Возвращает информацию о текущих моделях для команды !model."""
        return {
            "cloud_models": self.models.copy(),
            "local_engine": self.local_engine,
            "local_model": self.active_local_model,
            "local_available": self.is_local_available,
            "stats": self._stats.copy(),
            "force_mode": self.force_mode,
            "fallback_models": self.fallback_models
        }

    def get_ram_usage(self) -> dict:
        """
        Проверка RAM через SystemMonitor.
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
            return {"error": str(e), "can_load_heavy": True}