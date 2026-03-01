# -*- coding: utf-8 -*-
"""
OpenClaw Client - клиент взаимодействия с OpenClaw Gateway.

Ключевые задачи:
- Стриминг ответов и управление сессиями.
- Семантическая валидация ответов (защита от ложных 200 OK с текстом ошибки).
- Автоматический recovery policy: free -> paid -> openai -> local.
- Диагностика cloud runtime для web-панели.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .cache_manager import HISTORY_CACHE_TTL, history_cache
from .config import config
from .core.cloud_key_probe import (
    CloudProbeResult,
    default_openclaw_models_path,
    get_google_api_key_from_models,
    is_ai_studio_key,
    mask_secret,
    probe_gemini_key,
)
from .core.exceptions import ProviderAuthError, ProviderError
from .core.lm_studio_health import is_lm_studio_available
from .core.logger import get_logger
from .core.openclaw_secrets_runtime import reload_openclaw_secrets
from .core.routing_errors import RouterError, RouterQuotaError

logger = get_logger(__name__)


class OpenClawClient:
    """Клиент OpenClaw Gateway API."""

    def __init__(self):
        self.base_url = config.OPENCLAW_URL.rstrip("/")
        self.token = config.OPENCLAW_TOKEN
        self._http_client = httpx.AsyncClient(
            timeout=300.0,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        self._sessions: Dict[str, list] = {}
        self._usage_stats = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        # Source-of-truth по моделям/ключам OpenClaw (решение проекта: ~/.openclaw)
        self._models_path = default_openclaw_models_path()

        self.gemini_tiers = {
            "free": str(os.getenv("GEMINI_API_KEY_FREE", "") or "").strip(),
            "paid": str(os.getenv("GEMINI_API_KEY_PAID", "") or "").strip(),
        }
        self.active_tier = self._detect_initial_tier()

        self._cloud_tier_state: dict[str, Any] = {
            "active_tier": self.active_tier,
            "switches": 0,
            "last_switch_at": None,
            "last_error_code": None,
            "last_error_message": "",
            "last_provider_status": "unknown",
            "last_recovery_action": "none",
            "last_probe_at": None,
        }

    @staticmethod
    def _messages_size(messages: List[Dict[str, Any]]) -> int:
        total = 0
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += len(part.get("text", ""))
        return total

    def _apply_sliding_window(self, chat_id: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        max_msgs = getattr(config, "HISTORY_WINDOW_MESSAGES", 50)
        max_chars = getattr(config, "HISTORY_WINDOW_MAX_CHARS", None)
        if len(messages) <= max_msgs and (max_chars is None or self._messages_size(messages) <= max_chars):
            return messages

        out: list[dict[str, Any]] = []
        if messages and messages[0].get("role") == "system":
            out.append(messages[0])
            rest = messages[1:]
            slot_for_tail = max_msgs - 1
        else:
            rest = messages
            slot_for_tail = max_msgs

        if slot_for_tail == 0:
            tail = []
        elif len(rest) > slot_for_tail:
            tail = rest[-slot_for_tail:]
        else:
            tail = rest

        if max_chars is not None:
            current = 0
            new_tail = []
            for message in reversed(tail):
                size = self._messages_size([message])
                if current + size > max_chars and new_tail:
                    break
                new_tail.append(message)
                current += size
            tail = list(reversed(new_tail))

        out.extend(tail)
        logger.info(
            "history_trimmed",
            chat_id=chat_id,
            dropped_messages=len(messages) - len(out),
            before_count=len(messages),
            after_count=len(out),
            before_chars=self._messages_size(messages),
            after_chars=self._messages_size(out),
        )
        return out

    def _detect_initial_tier(self) -> str:
        """Определяет активный tier по ключу в OpenClaw models.json."""
        current_key = get_google_api_key_from_models(self._models_path)
        if current_key and current_key == self.gemini_tiers.get("paid"):
            return "paid"
        if current_key and current_key == self.gemini_tiers.get("free"):
            return "free"
        # Фолбэк по умолчанию — free
        return "free"

    def _read_models_json(self) -> dict[str, Any]:
        if not self._models_path.exists():
            return {"providers": {}}
        try:
            return json.loads(self._models_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"providers": {}}

    def _write_models_json(self, payload: dict[str, Any]) -> bool:
        try:
            self._models_path.parent.mkdir(parents=True, exist_ok=True)
            self._models_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return True
        except OSError as exc:
            logger.error("openclaw_models_write_failed", error=str(exc), path=str(self._models_path))
            return False

    def _set_google_key_in_models(self, key_value: str) -> bool:
        data = self._read_models_json()
        providers = data.setdefault("providers", {})
        google = providers.setdefault("google", {})
        google["apiKey"] = key_value
        return self._write_models_json(data)

    def _detect_semantic_error(self, text: str) -> dict[str, str] | None:
        """Детектор ложных успехов, когда backend вернул 200 с текстом ошибки."""
        payload = (text or "").strip()
        low = payload.lower()
        if not payload:
            return {"code": "empty_response", "message": "Пустой ответ от модели"}

        semantic_patterns = [
            ("no models loaded", "model_not_loaded", "Локальная модель не загружена"),
            ("quota", "quota_exceeded", "Квота облачного ключа исчерпана"),
            ("429", "quota_exceeded", "Квота облачного ключа исчерпана"),
            ("api keys are not supported", "unsupported_key_type", "Неверный тип облачного ключа"),
            ("unauthenticated", "auth_invalid", "Ошибка авторизации облачного ключа"),
            ("invalid api key", "auth_invalid", "Ошибка авторизации облачного ключа"),
            ("forbidden", "auth_invalid", "Ошибка авторизации облачного ключа"),
            ("timeout", "provider_timeout", "Таймаут облачного провайдера"),
        ]
        for pattern, code, message in semantic_patterns:
            if pattern in low:
                return {"code": code, "message": message}
        return None

    async def _switch_cloud_tier(self, tier: str, *, reason: str) -> dict[str, Any]:
        """Переключает active tier ключа в OpenClaw models.json и делает secrets reload."""
        target_tier = "paid" if tier == "paid" else "free"
        key_value = self.gemini_tiers.get(target_tier, "")
        if not key_value:
            return {"ok": False, "error": f"missing_{target_tier}_key"}
        if not is_ai_studio_key(key_value):
            return {"ok": False, "error": f"invalid_{target_tier}_key_type"}

        if not self._set_google_key_in_models(key_value):
            return {"ok": False, "error": "models_json_write_failed"}

        reload_result = await reload_openclaw_secrets()
        if not reload_result.get("ok"):
            return {
                "ok": False,
                "error": "secrets_reload_failed",
                "reload": reload_result,
            }

        previous = self.active_tier
        self.active_tier = target_tier
        self._cloud_tier_state["active_tier"] = target_tier
        self._cloud_tier_state["switches"] = int(self._cloud_tier_state.get("switches", 0)) + 1
        self._cloud_tier_state["last_switch_at"] = int(time.time())
        self._cloud_tier_state["last_recovery_action"] = f"switch_to_{target_tier}"

        logger.info(
            "cloud_tier_switched",
            previous_tier=previous,
            new_tier=target_tier,
            reason=reason,
        )
        return {
            "ok": True,
            "previous_tier": previous,
            "new_tier": target_tier,
            "reload": reload_result,
        }

    def _resolve_provider_api_key(self, provider: str) -> tuple[str, str]:
        """Совместимый helper для модулей, которым нужен ключ провайдера."""
        provider_low = provider.strip().lower()
        if provider_low == "google":
            key = self.gemini_tiers.get(self.active_tier, "")
            src = f"env:GEMINI_API_KEY_{self.active_tier.upper()}"
            return key, src
        if provider_low == "openai":
            key = str(os.getenv("OPENAI_API_KEY", "") or "").strip()
            return key, "env:OPENAI_API_KEY"
        return "", "missing"

    async def _openclaw_completion_once(
        self,
        *,
        model_id: str,
        messages_to_send: list[dict[str, Any]],
    ) -> str:
        """Один запрос к OpenClaw (stream=true) с буферизацией ответа."""
        payload = {
            "messages": messages_to_send,
            "stream": True,
            "model": model_id,
        }

        full_response = ""
        async with self._http_client.stream(
            "POST",
            f"{self.base_url}/v1/chat/completions",
            json=payload,
        ) as response:
            logger.info("openclaw_response_status", status=response.status_code, model=model_id)

            if response.status_code != 200:
                body_bytes = await response.aread()
                body_str = body_bytes.decode("utf-8", errors="ignore")
                logger.error("openclaw_api_error", status=response.status_code, body=body_str)
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
                if response.status_code >= 500:
                    raise ProviderError(
                        message=f"status={response.status_code} body={body_str[:500]}",
                        user_message="Провайдер временно недоступен",
                        retryable=True,
                    )
                raise ProviderError(
                    message=f"status={response.status_code} body={body_str[:500]}",
                    user_message=f"Ошибка API: {response.status_code}",
                    retryable=False,
                )

            async for line in response.aiter_lines():
                if not line:
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    usage = data.get("usage") or {}
                    self._usage_stats["input_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
                    self._usage_stats["output_tokens"] += int(usage.get("completion_tokens", 0) or 0)
                    self._usage_stats["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
                    delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if delta:
                        full_response += delta
                else:
                    # Иногда прокси может вернуть единый JSON без SSE префикса
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    maybe_content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
                    if maybe_content:
                        full_response += maybe_content

        return full_response.strip()

    async def _resolve_local_model_for_retry(self, model_manager: Any, preferred: str) -> str | None:
        """Подбирает локальную модель для аварийного retry."""
        if model_manager.is_local_model(preferred):
            return preferred
        preferred_local = await model_manager.resolve_preferred_local_model()
        if preferred_local:
            return preferred_local
        if not model_manager._models_cache:
            await model_manager.discover_models()
        for model_id, info in model_manager._models_cache.items():
            if str(getattr(info.type, "value", "")).startswith("local"):
                return model_id
        return None

    async def _direct_lm_fallback(
        self,
        *,
        chat_id: str,
        messages_to_send: list[dict[str, Any]],
        model_hint: str,
    ) -> str | None:
        """Прямой fallback в LM Studio (минуя OpenClaw)."""
        if not config.LM_STUDIO_URL:
            return None
        if not await is_lm_studio_available(config.LM_STUDIO_URL, timeout=5.0):
            return None

        try:
            async with httpx.AsyncClient(base_url=f"{config.LM_STUDIO_URL}/v1", timeout=120) as client:
                payload = {
                    "messages": messages_to_send,
                    "stream": False,
                    "model": model_hint if model_hint else "local",
                }
                resp = await client.post("/chat/completions", json=payload)
                if resp.status_code != 200:
                    return None
                data = resp.json()
                content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
                semantic = self._detect_semantic_error(content)
                if semantic:
                    return None
                return content
        except (httpx.HTTPError, OSError, ValueError, KeyError, IndexError):
            return None

    async def health_check(self) -> bool:
        """Проверка доступности OpenClaw."""
        try:
            response = await self._http_client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except (httpx.RequestError, httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            logger.error("openclaw_health_check_failed", error=str(exc))
            return False

    async def wait_for_healthy(self, timeout: int = 15) -> bool:
        """Ожидает доступности OpenClaw (polling)."""
        started = asyncio.get_running_loop().time()
        while (asyncio.get_running_loop().time() - started) < timeout:
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
        Отправляет сообщение в OpenClaw с recovery policy.

        Recovery policy:
        1) текущий маршрут,
        2) при quota free -> попытка switch paid,
        3) fallback на openai/gpt-4o-mini,
        4) fallback на локальную модель,
        5) прямой LM Studio fallback (если force_cloud=False).
        """
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
            content_parts = [{"type": "text", "text": message}]
            for img_b64 in images:
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                    }
                )
            self._sessions[chat_id].append({"role": "user", "content": content_parts})
        else:
            self._sessions[chat_id].append({"role": "user", "content": message})

        from .model_manager import model_manager  # lazy import

        has_photo = bool(images)
        selected_model = await model_manager.get_best_model(has_photo=has_photo)
        if not force_cloud and model_manager.is_local_model(selected_model):
            await model_manager.ensure_model_loaded(selected_model)

        messages_to_send = self._apply_sliding_window(chat_id, self._sessions[chat_id])

        logger.info(
            "openclaw_stream_start",
            chat_id=chat_id,
            model=selected_model,
            has_photo=has_photo,
            force_cloud=force_cloud,
        )

        tried_paid = False
        tried_openai = False
        tried_local = False
        final_response = ""
        attempt_model = selected_model

        try:
            for attempt in range(4):
                logger.info("openclaw_attempt", attempt=attempt + 1, model=attempt_model)
                final_response = await self._openclaw_completion_once(
                    model_id=attempt_model,
                    messages_to_send=messages_to_send,
                )
                semantic = self._detect_semantic_error(final_response)
                if not semantic:
                    break

                self._cloud_tier_state["last_error_code"] = semantic["code"]
                self._cloud_tier_state["last_error_message"] = semantic["message"]
                logger.warning(
                    "openclaw_semantic_error_detected",
                    code=semantic["code"],
                    message=semantic["message"],
                    model=attempt_model,
                )

                # 1) free quota -> paid
                if semantic["code"] == "quota_exceeded" and not tried_paid:
                    tried_paid = True
                    switch_result = await self._switch_cloud_tier("paid", reason="quota_exceeded")
                    if switch_result.get("ok"):
                        continue

                # 2) auth/key type -> openai fallback
                if semantic["code"] in {"auth_invalid", "unsupported_key_type", "quota_exceeded"} and not tried_openai:
                    tried_openai = True
                    attempt_model = "openai/gpt-4o-mini"
                    self._cloud_tier_state["last_recovery_action"] = "switch_to_openai"
                    continue

                # 3) любые критичные cloud ошибки -> local autoload (если не force_cloud)
                if semantic["code"] in {
                    "model_not_loaded",
                    "auth_invalid",
                    "unsupported_key_type",
                    "quota_exceeded",
                    "provider_timeout",
                    "provider_error",
                } and not force_cloud and not tried_local:
                    tried_local = True
                    local_model = await self._resolve_local_model_for_retry(model_manager, attempt_model)
                    if local_model:
                        loaded = await model_manager.ensure_model_loaded(local_model)
                        if loaded:
                            attempt_model = local_model
                            self._cloud_tier_state["last_recovery_action"] = "switch_to_local"
                            continue

                # Больше стратегий нет
                break

            semantic_after = self._detect_semantic_error(final_response)
            if semantic_after:
                # Последняя защита: прямой LM fallback
                if not force_cloud:
                    lm_text = await self._direct_lm_fallback(
                        chat_id=chat_id,
                        messages_to_send=messages_to_send,
                        model_hint=attempt_model,
                    )
                    if lm_text:
                        final_response = lm_text
                        semantic_after = None

            if semantic_after:
                code = semantic_after["code"]
                if code == "quota_exceeded":
                    user_text = "❌ Квота облачных ключей исчерпана. Переключись на локальную модель: !model local"
                elif code in {"auth_invalid", "unsupported_key_type"}:
                    user_text = "❌ Облачный ключ невалиден для текущего API. Проверь Gemini ключ формата AIza..."
                elif code == "model_not_loaded":
                    user_text = "❌ Локальная модель не загружена. Загрузи её в LM Studio или командой !model load <name>."
                else:
                    user_text = "❌ Облачный сервис временно недоступен. Попробуй позже или !model local."
                yield user_text
                return

            if not final_response:
                final_response = "❌ Модель не вернула ответ."

            self._sessions[chat_id].append({"role": "assistant", "content": final_response})
            self._sessions[chat_id] = self._apply_sliding_window(chat_id, self._sessions[chat_id])
            try:
                history_cache.set(
                    f"chat_history:{chat_id}",
                    json.dumps(self._sessions[chat_id], ensure_ascii=False),
                    ttl=HISTORY_CACHE_TTL,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("history_cache_set_failed", chat_id=chat_id, error=str(exc))

            yield final_response

        except RouterError:
            raise
        except (ProviderError, ProviderAuthError):
            raise
        except httpx.TimeoutException as exc:
            logger.error("openclaw_stream_timeout", error=str(exc))
            raise ProviderError(
                message=str(exc),
                user_message="Провайдер временно недоступен",
                retryable=True,
            )
        except (httpx.ConnectError, httpx.RequestError) as exc:
            logger.error("openclaw_stream_connect_error", error=str(exc))
            raise ProviderError(
                message=str(exc),
                user_message="Провайдер временно недоступен",
                retryable=True,
            )
        except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
            logger.error("openclaw_stream_error", error=str(exc))
            if force_cloud:
                yield "❌ Облачный сервис временно недоступен. Попробуй позже или переключись на !model local."
                return
            lm_text = await self._direct_lm_fallback(
                chat_id=chat_id,
                messages_to_send=messages_to_send,
                model_hint=attempt_model,
            )
            if lm_text:
                yield lm_text
                return
            yield "❌ Ошибка облака. Попробуй позже или переключись на локальную модель: !model local."

    def clear_session(self, chat_id: str):
        """Очищает историю чата (память и кэш)."""
        if chat_id in self._sessions:
            del self._sessions[chat_id]
        history_cache.delete(f"chat_history:{chat_id}")
        logger.info("session_cleared", chat_id=chat_id)

    def get_usage_stats(self) -> Dict[str, int]:
        """Возвращает статистику использования токенов."""
        return self._usage_stats

    def get_token_info(self) -> dict[str, Any]:
        """Маскированный отчет по ключам/tier (для UI)."""
        return {
            "active_tier": self.active_tier,
            "tiers": {
                "free": {
                    "is_configured": bool(self.gemini_tiers.get("free")),
                    "masked_key": mask_secret(self.gemini_tiers.get("free")),
                    "is_aistudio_key": is_ai_studio_key(self.gemini_tiers.get("free")),
                },
                "paid": {
                    "is_configured": bool(self.gemini_tiers.get("paid")),
                    "masked_key": mask_secret(self.gemini_tiers.get("paid")),
                    "is_aistudio_key": is_ai_studio_key(self.gemini_tiers.get("paid")),
                },
            },
            "current_google_key_masked": mask_secret(get_google_api_key_from_models(self._models_path)),
            "last_error_code": self._cloud_tier_state.get("last_error_code"),
        }

    async def get_cloud_provider_diagnostics(self, providers: list[str] | None = None) -> dict[str, Any]:
        """Диагностика cloud-провайдеров в безопасном формате."""
        providers_list = providers or ["google"]
        report: dict[str, Any] = {"ok": True, "providers": {}, "checked": providers_list}

        for provider in providers_list:
            provider_low = provider.lower().strip()
            if provider_low != "google":
                report["providers"][provider_low] = {
                    "ok": False,
                    "error_code": "provider_not_supported",
                    "summary": "Провайдер пока не поддерживается диагностикой",
                }
                report["ok"] = False
                continue

            tier = self.active_tier
            key, source = self._resolve_provider_api_key("google")
            probe: CloudProbeResult = await probe_gemini_key(
                key,
                key_source=source,
                key_tier=tier,
            )
            report["providers"]["google"] = {
                "ok": probe.provider_status == "ok",
                "provider_status": probe.provider_status,
                "error_code": probe.semantic_error_code,
                "summary": probe.detail[:220] if probe.detail else probe.provider_status,
                "key_source": probe.key_source,
                "key_tier": probe.key_tier,
                "recovery_action": probe.recovery_action,
                "http_status": probe.http_status,
            }
            if probe.provider_status != "ok":
                report["ok"] = False

            self._cloud_tier_state["last_provider_status"] = probe.provider_status
            self._cloud_tier_state["last_error_code"] = probe.semantic_error_code if probe.provider_status != "ok" else None
            self._cloud_tier_state["last_recovery_action"] = probe.recovery_action
            self._cloud_tier_state["last_probe_at"] = int(time.time())

        return report

    async def get_cloud_runtime_check(self) -> dict[str, Any]:
        """Расширенный runtime-check для web-панели."""
        free_probe = await probe_gemini_key(
            self.gemini_tiers.get("free"),
            key_source="env:GEMINI_API_KEY_FREE",
            key_tier="free",
        )
        paid_probe = await probe_gemini_key(
            self.gemini_tiers.get("paid"),
            key_source="env:GEMINI_API_KEY_PAID",
            key_tier="paid",
        )
        return {
            "ok": free_probe.provider_status == "ok" or paid_probe.provider_status == "ok",
            "active_tier": self.active_tier,
            "provider": "google",
            "free": free_probe.to_dict(),
            "paid": paid_probe.to_dict(),
            "current_google_key_masked": mask_secret(get_google_api_key_from_models(self._models_path)),
            "tier_state": self.get_tier_state_export(),
        }

    def get_tier_state_export(self) -> dict[str, Any]:
        """Экспорт внутреннего состояния cloud tier без секретов."""
        return {
            "active_tier": self._cloud_tier_state.get("active_tier", self.active_tier),
            "switches": int(self._cloud_tier_state.get("switches", 0)),
            "last_switch_at": self._cloud_tier_state.get("last_switch_at"),
            "last_error_code": self._cloud_tier_state.get("last_error_code"),
            "last_error_message": self._cloud_tier_state.get("last_error_message", ""),
            "last_provider_status": self._cloud_tier_state.get("last_provider_status"),
            "last_recovery_action": self._cloud_tier_state.get("last_recovery_action"),
            "last_probe_at": self._cloud_tier_state.get("last_probe_at"),
            "tiers_configured": {
                "free": bool(self.gemini_tiers.get("free")),
                "paid": bool(self.gemini_tiers.get("paid")),
            },
        }

    async def reset_cloud_tier(self) -> dict[str, Any]:
        """Ручной сброс active tier в free."""
        return await self._switch_cloud_tier("free", reason="manual_reset")

    async def switch_cloud_tier(self, tier: str) -> dict[str, Any]:
        """Публичный метод переключения tier (для web endpoint)."""
        return await self._switch_cloud_tier(tier, reason="manual_switch")

    async def get_health_report(self) -> dict[str, Any]:
        """Короткий health-отчет для web API."""
        return {
            "gateway_ok": await self.health_check(),
            "base_url": self.base_url,
            "tier_state": self.get_tier_state_export(),
            "usage": self.get_usage_stats(),
        }

    async def get_deep_health_report(self) -> dict[str, Any]:
        """Расширенный health-отчет c cloud runtime-check."""
        return {
            "health": await self.get_health_report(),
            "cloud_runtime": await self.get_cloud_runtime_check(),
        }

    async def get_remediation_plan(self) -> dict[str, Any]:
        """План восстановления на основе текущего состояния tier/ошибок."""
        state = self.get_tier_state_export()
        actions: list[str] = []
        if state.get("last_error_code") in {"auth_invalid", "unsupported_key_type"}:
            actions.append("Проверь и замени paid/free ключ на AI Studio API key формата AIza...")
            actions.append("Запусти sync_openclaw_models.command и затем check_cloud_chain.command")
        elif state.get("last_error_code") == "quota_exceeded":
            actions.append("Переключи tier на paid и перезагрузи secrets (через web endpoint или CLI)")
            actions.append("Если paid недоступен — включи local fallback (!model local)")
        elif state.get("last_error_code") == "model_not_loaded":
            actions.append("Загрузи локальную модель в LM Studio и повтори запрос")
        else:
            actions.append("Проверь доступность OpenClaw и LM Studio")
            actions.append("Запусти check_cloud_chain.command для автоматической диагностики")
        return {
            "state": state,
            "actions": actions,
        }


openclaw_client = OpenClawClient()
