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
from pathlib import Path
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

AUTH_UNAUTHORIZED_CODE = "openclaw_auth_unauthorized"
LEGACY_AUTH_CODES = {AUTH_UNAUTHORIZED_CODE, "auth_invalid", "unsupported_key_type"}


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
        self._openclaw_runtime_config_path = Path.home() / ".openclaw" / "openclaw.json"

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
        # Последний фактически использованный маршрут ответа (источник истины для web/UI).
        self._last_runtime_route: dict[str, Any] = {}

    @staticmethod
    def _provider_from_model(model_id: str) -> str:
        """Возвращает имя провайдера по идентификатору модели."""
        raw = str(model_id or "").strip()
        if "/" in raw:
            return raw.split("/", 1)[0]
        return "unknown"

    def _set_last_runtime_route(
        self,
        *,
        channel: str,
        model: str,
        route_reason: str,
        route_detail: str = "",
        status: str = "ok",
        error_code: str | None = None,
        force_cloud: bool = False,
    ) -> None:
        """Фиксирует последний runtime-маршрут запроса без секретов."""
        self._last_runtime_route = {
            "timestamp": int(time.time()),
            "channel": channel,
            "provider": self._provider_from_model(model),
            "model": str(model or ""),
            "active_tier": self.active_tier,
            "force_cloud": bool(force_cloud),
            "status": status,
            "error_code": error_code,
            "route_reason": route_reason,
            "route_detail": route_detail,
        }

    def get_last_runtime_route(self) -> dict[str, Any]:
        """Возвращает snapshot последнего фактического маршрута."""
        return dict(self._last_runtime_route)

    def _refresh_gateway_token_from_runtime(self) -> bool:
        """
        Подтягивает gateway token из `~/.openclaw/openclaw.json` и обновляет HTTP headers.

        Зачем:
        - в non-bootstrap среде `.env` часто содержит устаревший `OPENCLAW_API_KEY`;
        - реальный gateway token живёт в runtime-конфиге OpenClaw;
        - при 401 делаем один auto-refresh, чтобы убрать ложные auth-падения.
        """
        cfg_path = self._openclaw_runtime_config_path
        try:
            if not cfg_path.exists():
                return False
            payload = json.loads(cfg_path.read_text(encoding="utf-8"))
            gateway = payload.get("gateway", {}) if isinstance(payload, dict) else {}
            auth = gateway.get("auth", {}) if isinstance(gateway, dict) else {}
            runtime_token = ""
            if isinstance(auth, dict):
                runtime_token = str(auth.get("token", "") or "").strip()
            if not runtime_token and isinstance(gateway, dict):
                runtime_token = str(gateway.get("token", "") or "").strip()
            if not runtime_token or runtime_token == self.token:
                return False
            self.token = runtime_token
            self._http_client.headers["Authorization"] = f"Bearer {runtime_token}"
            logger.warning(
                "openclaw_gateway_token_refreshed_from_runtime",
                config_path=str(cfg_path),
            )
            return True
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(
                "openclaw_gateway_token_refresh_failed",
                config_path=str(cfg_path),
                error=str(exc),
            )
            return False

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
            return {"code": "lm_empty_stream", "message": "Пустой ответ от модели"}

        semantic_patterns = [
            ("no models loaded", "model_not_loaded", "Локальная модель не загружена"),
            ("<empty message>", "lm_empty_stream", "LM Studio вернула пустой поток"),
            ("empty message", "lm_empty_stream", "LM Studio вернула пустой поток"),
            ("stopiteration", "lm_empty_stream", "LM Studio вернула пустой поток"),
            (
                "model has crashed without additional information",
                "lm_model_crash",
                "Локальная модель LM Studio аварийно завершилась",
            ),
            (
                "the model has crashed without additional information",
                "lm_model_crash",
                "Локальная модель LM Studio аварийно завершилась",
            ),
            ("quota", "quota_exceeded", "Квота облачного ключа исчерпана"),
            ("429", "quota_exceeded", "Квота облачного ключа исчерпана"),
            ("api keys are not supported", "unsupported_key_type", "Неверный тип облачного ключа"),
            ("unauthenticated", AUTH_UNAUTHORIZED_CODE, "Ошибка авторизации облачного ключа"),
            ("invalid api key", AUTH_UNAUTHORIZED_CODE, "Ошибка авторизации облачного ключа"),
            ("forbidden", AUTH_UNAUTHORIZED_CODE, "Ошибка авторизации облачного ключа"),
            ("unauthorized", AUTH_UNAUTHORIZED_CODE, "Ошибка авторизации облачного ключа"),
            ("401", AUTH_UNAUTHORIZED_CODE, "Ошибка авторизации облачного ключа"),
            ("timeout", "provider_timeout", "Таймаут облачного провайдера"),
        ]
        for pattern, code, message in semantic_patterns:
            if pattern in low:
                return {"code": code, "message": message}
        return None

    @staticmethod
    def _semantic_from_provider_exception(exc: Exception) -> dict[str, str]:
        """
        Нормализует исключения провайдера в единый semantic error-контракт.

        Это нужно для консистентного fallback-поведения и корректной диагностики
        в `health/lite`/runtime badges даже когда OpenClaw отдал не текст ошибки,
        а HTTP-ошибку/исключение.
        """
        if isinstance(exc, ProviderAuthError):
            return {"code": AUTH_UNAUTHORIZED_CODE, "message": "Ошибка авторизации облачного ключа"}
        if isinstance(exc, ProviderError):
            code = "provider_timeout" if getattr(exc, "retryable", False) else "provider_error"
            return {"code": code, "message": str(exc) or "Ошибка провайдера"}
        return {"code": "transport_error", "message": str(exc) or "Ошибка транспорта"}

    def _build_retry_messages(self, messages_to_send: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Формирует компактный контекст для controlled retry.

        Почему:
        - при `EMPTY MESSAGE`/`model crashed` повтор с полным длинным контекстом
          часто воспроизводит ту же деградацию;
        - сжимаем историю до безопасного ядра: system + последние N сообщений.
        """
        if not messages_to_send:
            return []
        system_message = messages_to_send[0] if messages_to_send and messages_to_send[0].get("role") == "system" else None
        tail_source = messages_to_send[1:] if system_message else messages_to_send
        tail = tail_source[-8:]
        out: list[dict[str, Any]] = []
        if system_message:
            out.append(system_message)
        out.extend(tail)
        return out

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
        allow_auth_retry: bool = True,
    ) -> str:
        """Один запрос к OpenClaw (stream=true) с буферизацией ответа."""
        payload = {
            "messages": messages_to_send,
            "stream": True,
            "model": model_id,
        }

        full_response = ""
        retry_after_token_refresh = False
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
                    if allow_auth_retry and self._refresh_gateway_token_from_runtime():
                        retry_after_token_refresh = True
                    else:
                        raise ProviderAuthError(
                            message=f"status={response.status_code} body={body_str[:500]}",
                            user_message="Ошибка авторизации API",
                        )
                elif response.status_code == 429:
                    raise RouterQuotaError(
                        user_message="Квота исчерпана. Попробуй позже или переключись на локальную модель (!model local).",
                        details={"status": 429},
                    )
                elif response.status_code >= 500:
                    raise ProviderError(
                        message=f"status={response.status_code} body={body_str[:500]}",
                        user_message="Провайдер временно недоступен",
                        retryable=True,
                    )
                else:
                    raise ProviderError(
                        message=f"status={response.status_code} body={body_str[:500]}",
                        user_message=f"Ошибка API: {response.status_code}",
                        retryable=False,
                    )

            if retry_after_token_refresh:
                logger.warning(
                    "openclaw_retry_after_gateway_token_refresh",
                    model=model_id,
                )
                return await self._openclaw_completion_once(
                    model_id=model_id,
                    messages_to_send=messages_to_send,
                    allow_auth_retry=False,
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

    async def _resolve_local_model_for_retry(
        self,
        model_manager: Any,
        preferred: str,
        *,
        has_photo: bool = False,
    ) -> str | None:
        """Подбирает локальную модель для аварийного retry."""
        if model_manager.is_local_model(preferred):
            return preferred
        preferred_local = await model_manager.resolve_preferred_local_model(has_photo=has_photo)
        if preferred_local:
            return preferred_local
        if hasattr(model_manager, "_local_candidates"):
            try:
                candidates = await model_manager._local_candidates(has_photo=has_photo)  # noqa: SLF001
            except Exception:
                candidates = []
            if candidates:
                return str(candidates[0][0])
        if not model_manager._models_cache:
            await model_manager.discover_models()
        local_candidates: list[tuple[str, Any]] = []
        for model_id, info in model_manager._models_cache.items():
            if not model_manager.is_local_model(model_id):
                continue
            if hasattr(model_manager, "_is_chat_capable_local_model"):
                try:
                    if not bool(model_manager._is_chat_capable_local_model(model_id, info)):  # noqa: SLF001
                        continue
                except Exception:
                    pass
            if has_photo and not bool(getattr(info, "supports_vision", False)):
                continue
            local_candidates.append((model_id, info))
        local_candidates.sort(
            key=lambda item: float(getattr(item[1], "size_gb", 0.0) or 0.0)
        )
        if local_candidates:
            return str(local_candidates[0][0])
        return None

    def _is_cloud_candidate_usable(self, model_id: str, model_manager: Any) -> bool:
        """
        Проверяет, что облачный кандидат действительно пригоден для runtime retry.

        Ключевой кейс:
        - `openai/*` без OPENAI_API_KEY нельзя выбирать как recovery-кандидат,
          иначе получаем ложный цикл 401 и пропускаем рабочий local/cloud путь.
        """
        candidate = str(model_id or "").strip()
        if not candidate:
            return False
        if model_manager.is_local_model(candidate):
            return False

        provider = self._provider_from_model(candidate)
        if provider == "openai":
            return bool(str(os.getenv("OPENAI_API_KEY", "") or "").strip())
        return True

    async def _pick_cloud_retry_model(
        self,
        *,
        model_manager: Any,
        current_model: str,
        has_photo: bool,
    ) -> str:
        """Возвращает облачный retry-кандидат (или пустую строку, если кандидата нет)."""
        if not hasattr(model_manager, "get_best_cloud_model"):
            return ""
        candidate = str(await model_manager.get_best_cloud_model(has_photo=has_photo) or "").strip()
        if not candidate or candidate == str(current_model or "").strip():
            return ""
        if not self._is_cloud_candidate_usable(candidate, model_manager):
            return ""
        return candidate

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

    def _finalize_chat_response(self, chat_id: str, final_response: str) -> None:
        """Сохраняет ответ ассистента в историю и кэш."""
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

        request_marked = False
        if hasattr(model_manager, "mark_request_started"):
            try:
                model_manager.mark_request_started()
                request_marked = True
            except Exception as exc:  # noqa: BLE001
                logger.debug("model_manager_mark_request_started_failed", error=str(exc))

        has_photo = bool(images)
        selected_model = ""
        attempt_model = ""
        messages_to_send: list[dict[str, Any]] = []

        try:
            selected_model = await model_manager.get_best_model(has_photo=has_photo)
            if not force_cloud and model_manager.is_local_model(selected_model):
                local_ready = await model_manager.ensure_model_loaded(
                    selected_model,
                    has_photo=has_photo,
                )
                if local_ready and hasattr(model_manager, "get_current_model"):
                    current_local = str(model_manager.get_current_model() or "").strip()
                    if current_local and current_local != selected_model:
                        logger.warning(
                            "local_model_remapped_after_autoload",
                            requested=selected_model,
                            remapped=current_local,
                        )
                        selected_model = current_local
                if not local_ready:
                    # Если локальный автозапуск не сработал, не отдаём пользователю silent-empty:
                    # заранее уходим в cloud-кандидат.
                    cloud_candidate = await self._pick_cloud_retry_model(
                        model_manager=model_manager,
                        current_model=selected_model,
                        has_photo=has_photo,
                    )
                    if cloud_candidate:
                        logger.warning(
                            "local_autoload_failed_switching_to_cloud",
                            requested=selected_model,
                            cloud_candidate=cloud_candidate,
                        )
                        selected_model = cloud_candidate

            attempt_model = selected_model

            messages_to_send = self._apply_sliding_window(chat_id, self._sessions[chat_id])

            logger.info(
                "openclaw_stream_start",
                chat_id=chat_id,
                model=selected_model,
                has_photo=has_photo,
                force_cloud=force_cloud,
            )
            self._set_last_runtime_route(
                channel="planning",
                model=selected_model,
                route_reason="selected_model",
                route_detail="Определена целевая модель перед выполнением запроса",
                force_cloud=force_cloud,
            )

            # Жесткий local-first: если выбран локальный маршрут, сначала бьем напрямую в LM Studio.
            # Это исключает ситуацию, когда OpenClaw runtime игнорирует модель и уходит в cloud.
            if not force_cloud and model_manager.is_local_model(selected_model):
                lm_text = await self._direct_lm_fallback(
                    chat_id=chat_id,
                    messages_to_send=messages_to_send,
                    model_hint=selected_model,
                )
                if lm_text:
                    logger.info("local_direct_path_used", chat_id=chat_id, model=selected_model)
                    self._set_last_runtime_route(
                        channel="local_direct",
                        model=selected_model,
                        route_reason="local_direct_primary",
                        route_detail="Ответ получен напрямую из LM Studio",
                        force_cloud=force_cloud,
                    )
                    self._finalize_chat_response(chat_id, lm_text)
                    yield lm_text
                    return
                logger.warning("local_direct_path_failed_fallback_openclaw", chat_id=chat_id, model=selected_model)

            tried_paid = False
            tried_cloud_auth_recovery = False
            tried_local = False
            tried_cloud_after_local = False
            tried_semantic_retry = False
            final_response = ""
            last_semantic: dict[str, str] | None = None

            for attempt in range(4):
                logger.info("openclaw_attempt", attempt=attempt + 1, model=attempt_model)
                semantic: dict[str, str] | None = None
                try:
                    final_response = await self._openclaw_completion_once(
                        model_id=attempt_model,
                        messages_to_send=messages_to_send,
                    )
                    semantic = self._detect_semantic_error(final_response)
                except (ProviderAuthError, ProviderError) as exc:
                    semantic = self._semantic_from_provider_exception(exc)
                    final_response = ""

                if semantic and semantic["code"] in {"lm_empty_stream", "lm_model_crash"} and not tried_semantic_retry:
                    tried_semantic_retry = True
                    retry_messages = self._build_retry_messages(messages_to_send)
                    logger.warning(
                        "openclaw_semantic_retry",
                        code=semantic["code"],
                        model=attempt_model,
                        messages_before=len(messages_to_send),
                        messages_after=len(retry_messages),
                    )
                    try:
                        final_response = await self._openclaw_completion_once(
                            model_id=attempt_model,
                            messages_to_send=retry_messages,
                        )
                        semantic = self._detect_semantic_error(final_response)
                        messages_to_send = retry_messages
                    except (ProviderAuthError, ProviderError) as retry_exc:
                        semantic = self._semantic_from_provider_exception(retry_exc)
                        final_response = ""

                if not semantic:
                    last_semantic = None
                    break

                last_semantic = semantic
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

                # 2) auth/key type/quota -> cloud retry без слепого openai fallback
                if semantic["code"] in (LEGACY_AUTH_CODES | {"quota_exceeded"}) and not tried_cloud_auth_recovery:
                    tried_cloud_auth_recovery = True
                    cloud_retry = await self._pick_cloud_retry_model(
                        model_manager=model_manager,
                        current_model=attempt_model,
                        has_photo=has_photo,
                    )
                    if cloud_retry:
                        attempt_model = cloud_retry
                        self._cloud_tier_state["last_recovery_action"] = "switch_to_cloud_retry"
                        continue
                    if self._is_cloud_candidate_usable("openai/gpt-4o-mini", model_manager):
                        attempt_model = "openai/gpt-4o-mini"
                        self._cloud_tier_state["last_recovery_action"] = "switch_to_openai"
                        continue

                # 3) критичные ошибки -> local autoload (если не force_cloud)
                local_recovery_codes = {
                    "model_not_loaded",
                    "quota_exceeded",
                    "provider_timeout",
                    "provider_error",
                    "transport_error",
                    "lm_empty_stream",
                    "lm_model_crash",
                } | LEGACY_AUTH_CODES
                if semantic["code"] in local_recovery_codes and not force_cloud and not tried_local:
                    tried_local = True
                    local_model = await self._resolve_local_model_for_retry(
                        model_manager,
                        attempt_model,
                        has_photo=has_photo,
                    )
                    if local_model:
                        loaded = await model_manager.ensure_model_loaded(
                            local_model,
                            has_photo=has_photo,
                        )
                        if loaded:
                            attempt_model = local_model
                            self._cloud_tier_state["last_recovery_action"] = "switch_to_local"
                            continue
                    if not tried_cloud_after_local:
                        tried_cloud_after_local = True
                        cloud_candidate = await self._pick_cloud_retry_model(
                            model_manager=model_manager,
                            current_model=attempt_model,
                            has_photo=has_photo,
                        )
                        if cloud_candidate:
                            attempt_model = cloud_candidate
                            self._cloud_tier_state["last_recovery_action"] = "switch_to_cloud_after_local_failure"
                            continue

                # Больше стратегий нет
                break

            if not final_response and last_semantic is not None:
                # Не перетираем реальную причину (например auth 401) синтетическим lm_empty_stream.
                semantic_after = dict(last_semantic)
            else:
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
                        self._set_last_runtime_route(
                            channel="local_direct",
                            model=attempt_model,
                            route_reason="local_direct_recovery",
                            route_detail="Семантическая ошибка OpenClaw, восстановление через прямой LM Studio",
                            force_cloud=force_cloud,
                        )
                        semantic_after = None

            if semantic_after:
                code = semantic_after["code"]
                self._set_last_runtime_route(
                    channel="error",
                    model=attempt_model,
                    route_reason="semantic_error",
                    route_detail=semantic_after["message"],
                    status="error",
                    error_code=code,
                    force_cloud=force_cloud,
                )
                if code == "quota_exceeded":
                    user_text = "❌ Квота облачных ключей исчерпана. Переключись на локальную модель: !model local"
                elif code in LEGACY_AUTH_CODES:
                    user_text = "❌ Облачный ключ невалиден для текущего API. Проверь Gemini ключ формата AIza..."
                elif code == "model_not_loaded":
                    user_text = "❌ Локальная модель не загружена. Загрузи её в LM Studio или командой !model load <name>."
                elif code == "lm_empty_stream":
                    user_text = "❌ Модель вернула пустой поток. Повтори запрос или переключись на !model local."
                elif code == "lm_model_crash":
                    user_text = "❌ Локальная модель аварийно завершилась. Повтори запрос или переключись на !model cloud."
                else:
                    user_text = "❌ Облачный сервис временно недоступен. Попробуй позже или !model local."
                yield user_text
                return

            if not final_response:
                final_response = "❌ Модель не вернула ответ."

            if (
                not self._last_runtime_route
                or self._last_runtime_route.get("status") != "ok"
                or self._last_runtime_route.get("channel") == "planning"
            ):
                route_channel = (
                    "openclaw_local"
                    if model_manager.is_local_model(attempt_model)
                    else "openclaw_cloud"
                )
                self._set_last_runtime_route(
                    channel=route_channel,
                    model=attempt_model,
                    route_reason="openclaw_response_ok",
                    route_detail="Ответ получен через OpenClaw API",
                    force_cloud=force_cloud,
                )

            self._finalize_chat_response(chat_id, final_response)
            yield final_response

        except RouterError:
            raise
        except (ProviderError, ProviderAuthError) as exc:
            semantic = self._semantic_from_provider_exception(exc)
            code = semantic["code"]
            self._cloud_tier_state["last_error_code"] = code
            self._cloud_tier_state["last_error_message"] = semantic["message"]
            self._set_last_runtime_route(
                channel="error",
                model=attempt_model or selected_model,
                route_reason="provider_exception",
                route_detail=semantic["message"],
                status="error",
                error_code=code,
                force_cloud=force_cloud,
            )
            if code in LEGACY_AUTH_CODES:
                yield "❌ Облачный ключ не прошёл авторизацию. Проверь ключ/токен."
            else:
                yield "❌ Провайдер временно недоступен. Попробуй позже или переключись на !model local."
        except httpx.TimeoutException as exc:
            logger.error("openclaw_stream_timeout", error=str(exc))
            self._set_last_runtime_route(
                channel="error",
                model=attempt_model or selected_model,
                route_reason="transport_timeout",
                route_detail=str(exc),
                status="error",
                error_code="provider_timeout",
                force_cloud=force_cloud,
            )
            yield "❌ Провайдер временно недоступен. Попробуй позже или переключись на !model local."
        except (httpx.ConnectError, httpx.RequestError) as exc:
            logger.error("openclaw_stream_connect_error", error=str(exc))
            self._set_last_runtime_route(
                channel="error",
                model=attempt_model or selected_model,
                route_reason="transport_connect_error",
                route_detail=str(exc),
                status="error",
                error_code="transport_error",
                force_cloud=force_cloud,
            )
            yield "❌ Провайдер временно недоступен. Попробуй позже или переключись на !model local."
        except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
            logger.error("openclaw_stream_error", error=str(exc))
            if force_cloud:
                yield "❌ Облачный сервис временно недоступен. Попробуй позже или переключись на !model local."
                return
            lm_text = await self._direct_lm_fallback(
                chat_id=chat_id,
                messages_to_send=messages_to_send,
                model_hint=attempt_model or selected_model,
            )
            if lm_text:
                self._set_last_runtime_route(
                    channel="local_direct",
                    model=attempt_model or selected_model,
                    route_reason="local_direct_exception_fallback",
                    route_detail="Ошибка OpenClaw транспорта, выполнен прямой fallback в LM Studio",
                    force_cloud=force_cloud,
                )
                yield lm_text
                return
            self._set_last_runtime_route(
                channel="error",
                model=attempt_model or selected_model,
                route_reason="transport_error",
                route_detail=str(exc),
                status="error",
                error_code="transport_error",
                force_cloud=force_cloud,
            )
            yield "❌ Ошибка облака. Попробуй позже или переключись на локальную модель: !model local."
        finally:
            if request_marked and hasattr(model_manager, "mark_request_finished"):
                try:
                    model_manager.mark_request_finished()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("model_manager_mark_request_finished_failed", error=str(exc))

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
        if state.get("last_error_code") in LEGACY_AUTH_CODES:
            actions.append("Проверь и замени paid/free ключ на AI Studio API key формата AIza...")
            actions.append("Запусти sync_openclaw_models.command и затем check_cloud_chain.command")
        elif state.get("last_error_code") == "quota_exceeded":
            actions.append("Переключи tier на paid и перезагрузи secrets (через web endpoint или CLI)")
            actions.append("Если paid недоступен — включи local fallback (!model local)")
        elif state.get("last_error_code") == "model_not_loaded":
            actions.append("Загрузи локальную модель в LM Studio и повтори запрос")
        elif state.get("last_error_code") == "lm_empty_stream":
            actions.append("Повтори запрос с сокращённым контекстом или переключись на другую локальную модель")
            actions.append("Проверь, что у локальной модели нет аварий в логах LM Studio")
        elif state.get("last_error_code") == "lm_model_crash":
            actions.append("Перезапусти проблемную модель в LM Studio и повтори запрос")
            actions.append("Если сбой повторяется — временно переключись на cloud/local fallback")
        else:
            actions.append("Проверь доступность OpenClaw и LM Studio")
            actions.append("Запусти check_cloud_chain.command для автоматической диагностики")
        return {
            "state": state,
            "actions": actions,
        }


openclaw_client = OpenClawClient()
