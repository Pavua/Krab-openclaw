"""
OpenClaw Client - Клиент для взаимодействия с OpenClaw Gateway

Фаза 2.1: использует единый модуль ошибок роутинга (auth, quota, network, timeout).
Fail-fast для некорректируемых ошибок (auth, quota) — без retry и без fallback на LM Studio.
Фаза 6: скользящее окно истории диалога (sliding window) + логирование операций с памятью.
"""
import asyncio
import json
from typing import AsyncIterator, Optional, Dict, Any, List

import httpx

from .cache_manager import history_cache, HISTORY_CACHE_TTL
from .config import config
from .core.exceptions import ProviderAuthError, ProviderError
from .core.logger import get_logger
from .core.lm_studio_health import is_lm_studio_available
from .core.routing_errors import (
    RouterError,
    RouterQuotaError,
)

logger = get_logger(__name__)


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

    @staticmethod
    def _messages_size(messages: List[Dict[str, Any]]) -> int:
        """Суммарное количество символов в истории (для лимита по размеру)."""
        total = 0
        for m in messages:
            content = m.get("content")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += len(part.get("text", ""))
        return total

    def _apply_sliding_window(self, chat_id: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Скользящее окно: оставляет последние N сообщений и/или обрезает по суммарному размеру.
        Логирует обрезку через structlog.
        """
        max_msgs = getattr(config, "HISTORY_WINDOW_MESSAGES", 50)
        max_chars = getattr(config, "HISTORY_WINDOW_MAX_CHARS", None)
        if len(messages) <= max_msgs and (max_chars is None or self._messages_size(messages) <= max_chars):
            return messages

        # Сохраняем первый элемент (system) если есть
        out = []
        if messages and messages[0].get("role") == "system":
            out.append(messages[0])
            rest = messages[1:]
            slot_for_tail = max_msgs - 1  # чтобы итого было ровно max_msgs с учётом system
        else:
            rest = messages
            slot_for_tail = max_msgs

        # Берём последние slot_for_tail не-системных сообщений (или все rest если меньше)
        tail = rest[-slot_for_tail:] if len(rest) > slot_for_tail else rest
        if max_chars is not None:
            current = 0
            new_tail = []
            for m in reversed(tail):
                sz = self._messages_size([m])
                if current + sz > max_chars and new_tail:
                    break
                new_tail.append(m)
                current += sz
            tail = list(reversed(new_tail))
        out.extend(tail)

        dropped = len(messages) - len(out)
        total_chars = self._messages_size(messages)
        window_chars = self._messages_size(out)
        logger.info(
            "history_trimmed",
            chat_id=chat_id,
            dropped_messages=dropped,
            before_count=len(messages),
            after_count=len(out),
            before_chars=total_chars,
            after_chars=window_chars,
        )
        return out

    async def health_check(self) -> bool:
        """Проверка доступности OpenClaw"""
        try:
            response = await self._http_client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except (httpx.RequestError, httpx.ConnectError, httpx.TimeoutException, OSError) as e:
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
        images: Optional[List[str]] = None,
        force_cloud: bool = False,
    ) -> AsyncIterator[str]:
        """
        Отправляет сообщение и получает потоковый ответ.
        force_cloud: если True, локальный путь (fallback на LM Studio) не используется —
        при ошибке облака возвращается сообщение о деградации с подсказкой !model local.
        """
        # Инициализация сессии: из памяти, из кэша (после рестарта) или новая
        if chat_id not in self._sessions:
            cached = history_cache.get(f"chat_history:{chat_id}")
            if cached:
                try:
                    self._sessions[chat_id] = json.loads(cached)
                    logger.info("history_restored_from_cache", chat_id=chat_id, messages=len(self._sessions[chat_id]))
                except (json.JSONDecodeError, TypeError):
                    self._sessions[chat_id] = []
            else:
                self._sessions[chat_id] = []
            if system_prompt and not self._sessions[chat_id]:
                self._sessions[chat_id].append({"role": "system", "content": system_prompt})
            elif system_prompt and self._sessions[chat_id][0].get("role") != "system":
                self._sessions[chat_id].insert(0, {"role": "system", "content": system_prompt})
        
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

        # Скользящее окно: в API уходят только последние N сообщений (и/или по размеру)
        messages_to_send = self._apply_sliding_window(chat_id, self._sessions[chat_id])
        payload = {
            "messages": messages_to_send,
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
                    error_text = await response.aread()
                    body_str = error_text.decode("utf-8", errors="ignore")
                    logger.error("openclaw_api_error", status=response.status_code, body=body_str)
                    # 401/403 — ошибка авторизации API, не повторять
                    if response.status_code in (401, 403):
                        raise ProviderAuthError(
                            message=f"status={response.status_code} body={body_str[:500]}",
                            user_message="Ошибка авторизации API",
                        )
                    if response.status_code == 429:
                        raise RouterQuotaError(
                            user_message="Квота исчерпана. Попробуй позже или переключись на локальную модель (!model local).",
                            details={"status": 429},
                        )
                    # 5xx и прочие — провайдер временно недоступен (retryable)
                    if response.status_code >= 500:
                        raise ProviderError(
                            message=f"status={response.status_code} body={body_str[:500]}",
                            user_message="Провайдер временно недоступен",
                            retryable=True,
                        )
                    # Остальные ошибки (4xx кроме 401/403/429) — одно сообщение без fallback
                    yield f"Ошибка API: {response.status_code}. {body_str[:300]}"
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

            # Скользящее окно: обрезаем историю в памяти (логирование внутри _apply_sliding_window)
            self._sessions[chat_id] = self._apply_sliding_window(chat_id, self._sessions[chat_id])
            try:
                history_cache.set(
                    f"chat_history:{chat_id}",
                    json.dumps(self._sessions[chat_id], ensure_ascii=False),
                    ttl=HISTORY_CACHE_TTL,
                )
            except Exception as e:
                logger.warning("history_cache_set_failed", chat_id=chat_id, error=str(e))

        except RouterError:
            # Ошибка роутинга (например RouterQuotaError) — пробрасываем
            raise
        except (ProviderError, ProviderAuthError):
            # Иерархия провайдера — пробрасываем
            raise
        except httpx.TimeoutException as e:
            logger.error("openclaw_stream_error", error=str(e))
            raise ProviderError(
                message=str(e),
                user_message="Провайдер временно недоступен",
                retryable=True,
            )
        except (httpx.ConnectError, httpx.RequestError) as e:
            logger.error("openclaw_stream_error", error=str(e))
            raise ProviderError(
                message=str(e),
                user_message="Провайдер временно недоступен",
                retryable=True,
            )
        except (httpx.HTTPError, OSError, ValueError, KeyError) as e:
            logger.error("openclaw_stream_error", error=str(e))
            # force_cloud: не использовать локальный путь (Фаза 2.2) — сообщение о деградации облака
            if force_cloud:
                yield (
                    "❌ Облачный сервис временно недоступен. Попробуй позже или переключись на локальную модель: !model local."
                )
                return
            # Только для непредвиденных ошибок и не force_cloud пробуем fallback на LM Studio (Фаза 2.3: общая проверка доступности)
            if config.LM_STUDIO_URL and await is_lm_studio_available(config.LM_STUDIO_URL, timeout=5.0):
                logger.info("falling_back_to_lm_studio")
                yield "⚠️ OpenClaw Error. Falling back to LM Studio...\n\n"
                try:
                    async with httpx.AsyncClient(base_url=f"{config.LM_STUDIO_URL}/v1", timeout=120) as lm_client:
                        lm_messages = self._apply_sliding_window(chat_id, self._sessions[chat_id])
                        payload = {
                            "messages": lm_messages,
                            "stream": False,
                            "model": "local",
                        }
                        resp = await lm_client.post("/chat/completions", json=payload)
                        if resp.status_code == 200:
                            data = resp.json()
                            content = data["choices"][0]["message"]["content"]
                            self._sessions[chat_id].append({"role": "assistant", "content": content})
                            self._sessions[chat_id] = self._apply_sliding_window(chat_id, self._sessions[chat_id])
                            try:
                                history_cache.set(
                                    f"chat_history:{chat_id}",
                                    json.dumps(self._sessions[chat_id], ensure_ascii=False),
                                    ttl=HISTORY_CACHE_TTL,
                                )
                            except Exception as e:
                                logger.warning("history_cache_set_failed", chat_id=chat_id, error=str(e))
                            yield content
                            return
                        yield "❌ OpenClaw и LM Studio вернули ошибку. Попробуй позже или !model local."
                except (httpx.HTTPError, OSError, json.JSONDecodeError, KeyError, IndexError) as lme:
                    yield f"❌ Критическая ошибка: {str(lme)}"
            else:
                yield "❌ Ошибка облака. Попробуй позже или переключись на локальную модель: !model local."

    def clear_session(self, chat_id: str):
        """Очищает историю чата (память и кэш)."""
        if chat_id in self._sessions:
            del self._sessions[chat_id]
        history_cache.delete(f"chat_history:{chat_id}")
        logger.info("session_cleared", chat_id=chat_id)

    def get_usage_stats(self) -> Dict[str, int]:
        """Возвращает статистику использования токенов"""
        return self._usage_stats


openclaw_client = OpenClawClient()
