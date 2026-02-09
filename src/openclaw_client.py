"""
OpenClaw Client - Клиент для взаимодействия с OpenClaw Gateway
"""
import asyncio
import json
from typing import AsyncIterator, Optional, Dict, Any, List

import httpx
import structlog

from .config import config

logger = structlog.get_logger(__name__)


class OpenClawClient:
    """Клиент для OpenClaw Gateway API"""
    
    def __init__(self):
        self.base_url = config.OPENCLAW_URL
        self.token = config.OPENCLAW_TOKEN
        self._http_client = httpx.AsyncClient(
            timeout=300.0,  # 5 минут на ответ (для reasoning моделей)
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
        )
        self._sessions: Dict[str, list] = {}  # chat_id -> history
        self._usage_stats = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    async def health_check(self) -> bool:
        """Проверка доступности OpenClaw"""
        try:
            response = await self._http_client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except Exception as e:
            logger.error("openclaw_health_check_failed", error=str(e))
            return False
    async def wait_for_healthy(self, timeout: int = 15) -> bool:
        """Ожидает доступности OpenClaw (polling)"""
        start_time = asyncio.get_running_loop().time()
        while (asyncio.get_running_loop().time() - start_time) < timeout:
            if await self.health_check():
                logger.info("openclaw_healthy_verified")
                return True
            await asyncio.sleep(1.0)
        
        logger.warning("openclaw_wait_timeout", timeout=timeout)
        return False


    async def send_message_stream(
        self, 
        message: str, 
        chat_id: str, 
        system_prompt: Optional[str] = None,
        images: Optional[List[str]] = None
    ) -> AsyncIterator[str]:
        """
        Отправляет сообщение и получает потоковый ответ
        """
        # Инициализация сессии если нет
        if chat_id not in self._sessions:
            self._sessions[chat_id] = []
            if system_prompt:
                self._sessions[chat_id].append({"role": "system", "content": system_prompt})
        
        if images:
            # Vision payload
            content_parts = [{"type": "text", "text": message}]
            for img_b64 in images:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                })
            self._sessions[chat_id].append({"role": "user", "content": content_parts})
        else:
            # Standard text payload
            self._sessions[chat_id].append({"role": "user", "content": message})
        
        model_id = getattr(config, "MODEL", "google/gemini-2.0-flash")
        
        payload = {
            "messages": self._sessions[chat_id],
            "stream": True,
            "model": model_id
        }
        
        full_response = ""
        logger.info("openclaw_stream_start", chat_id=chat_id, model=payload["model"])
        
        try:
            async with self._http_client.stream(
                "POST", 
                f"{self.base_url}/v1/chat/completions", 
                json=payload
            ) as response:
                
                logger.info("openclaw_response_status", status=response.status_code)
                if response.status_code != 200:
                    error_text = await response.aread() # Using aread() to be safe in AsyncResponse
                    logger.error("openclaw_api_error", status=response.status_code, body=error_text.decode('utf-8', errors='ignore'))
                    yield f"Error: {response.status_code} - {error_text.decode('utf-8', errors='ignore')}"
                    return

                async for line in response.aiter_lines():
                    if not line:
                        continue
                        
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            
                            # Capture Usage if available
                            if "usage" in data:
                                usage = data["usage"]
                                self._usage_stats["input_tokens"] += usage.get("prompt_tokens", 0)
                                self._usage_stats["output_tokens"] += usage.get("completion_tokens", 0)
                                self._usage_stats["total_tokens"] += usage.get("total_tokens", 0)
                                
                            delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if delta:
                                full_response += delta
                                yield delta
                        except json.JSONDecodeError:
                            continue
                            
            # Сохраняем ответ ассистента в историю
            if full_response:
                self._sessions[chat_id].append({"role": "assistant", "content": full_response})
            
            # Ограничиваем историю (последние 20 сообщений)
            if len(self._sessions[chat_id]) > 20:
                self._sessions[chat_id] = self._sessions[chat_id][-20:]
                
        except Exception as e:
            logger.error("openclaw_stream_error", error=str(e))
            
            # FALLBACK TO LM STUDIO
            if config.LM_STUDIO_URL:
                logger.info("falling_back_to_lm_studio")
                yield "⚠️ OpenClaw Error. Falling back to LM Studio...\n\n"
                try:
                    async with httpx.AsyncClient(base_url=f"{config.LM_STUDIO_URL}/v1", timeout=120) as lm_client:
                        payload = {
                            "messages": self._sessions[chat_id],
                            "stream": False,  # Non-stream fallback for simplicity
                            "model": "local"
                        }
                        resp = await lm_client.post("/chat/completions", json=payload)
                        if resp.status_code == 200:
                            data = resp.json()
                            content = data["choices"][0]["message"]["content"]
                            self._sessions[chat_id].append({"role": "assistant", "content": content})
                            yield content
                            return
                        else:
                            yield f"❌ Both OpenClaw and LM Studio failed: {resp.status_code}"
                except Exception as lme:
                    yield f"❌ Critical failure: {str(lme)}"
            else:
                yield f"Error: {str(e)}"

    def clear_session(self, chat_id: str):
        """Очищает историю чата"""
        if chat_id in self._sessions:
            del self._sessions[chat_id]
            logger.info("session_cleared", chat_id=chat_id)

    def get_usage_stats(self) -> Dict[str, int]:
        """Возвращает статистику использования токенов"""
        return self._usage_stats


openclaw_client = OpenClawClient()
