# -*- coding: utf-8 -*-
"""
OpenClaw Client.

Роль модуля:
1) Каноничный HTTP-клиент к OpenClaw Gateway.
2) OpenClaw-first контур для web/tools/chat.
3) Health/diagnostics по browser/auth/providers без падений при несовместимых API.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class OpenClawClient:
    """Клиент интеграции Krab -> OpenClaw Gateway."""

    def __init__(self, base_url: str = "http://localhost:18789", api_key: Optional[str | dict[str, str]] = None):
        self.base_url = base_url.rstrip("/")
        # Gateway auth key может быть строкой или словарем {"free": "...", "paid": "..."}.
        # Это bearer-доступ к OpenClaw, не ключи провайдера Gemini.
        if isinstance(api_key, dict):
            self.gateway_tiers = {k: str(v) for k, v in api_key.items() if str(v).strip()}
            self.api_key = self.gateway_tiers.get("free") or next(iter(self.gateway_tiers.values()), None)
        else:
            self.gateway_tiers = {"default": str(api_key)} if api_key else {}
            self.api_key = api_key

        self.active_gateway_tier = next(
            (k for k, v in self.gateway_tiers.items() if v == self.api_key),
            "default",
        )

        # Провайдерные Gemini-ключи для free->paid fallback в диагностике и direct-probe.
        # Приоритет: GEMINI_API_KEY_FREE/GEMINI_API_KEY_PAID -> GEMINI_API_KEY -> GOOGLE_API_KEY.
        self.gemini_tiers: dict[str, str] = {}
        env_free = os.getenv("GEMINI_API_KEY_FREE", "").strip()
        env_paid = os.getenv("GEMINI_API_KEY_PAID", "").strip()
        env_default = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
        if env_free:
            self.gemini_tiers["free"] = env_free
        if env_paid:
            self.gemini_tiers["paid"] = env_paid
        if env_default and "free" not in self.gemini_tiers:
            self.gemini_tiers["free"] = env_default
        self.active_tier = "free" if "free" in self.gemini_tiers else (
            "paid" if "paid" in self.gemini_tiers else "default"
        )

        self._provider_probe_cache: dict[str, tuple[float, str]] = {}
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Krab/8.0 (OpenClaw-Client)",
        }
        self._update_auth_header()

    def _update_auth_header(self):
        if self.api_key:
            self.headers["Authorization"] = f"Bearer {self.api_key}"
        elif "Authorization" in self.headers:
            del self.headers["Authorization"]

    def has_tier(self, tier_name: str) -> bool:
        """Проверяет доступность tier в gateway- или gemini-пуле."""
        return tier_name in self.gemini_tiers or tier_name in self.gateway_tiers

    def set_tier(self, tier_name: str) -> bool:
        """
        Переключает активный tier.
        - Gemini tier: влияет на выбор ключа в provider probe/direct fallback.
        - Gateway tier: влияет на Bearer токен OpenClaw.
        """
        switched = False
        if tier_name in self.gemini_tiers:
            self.active_tier = tier_name
            switched = True

        if tier_name in self.gateway_tiers:
            self.api_key = self.gateway_tiers[tier_name]
            self.active_gateway_tier = tier_name
            self._update_auth_header()
            switched = True

        if switched:
            logger.info("OpenClaw tier switched", tier=tier_name)
        return switched

    def get_token_info(self) -> dict[str, Any]:
        """
        Возвращает безопасную информацию о токене.
        Совместим с R15 (`masked_key`) и расширен для R16 (`tiers`).
        """
        def _mask(key: str) -> str:
            if len(key) <= 8:
                return "****"
            return f"{key[:6]}...{key[-4:]}"

        tier_info: dict[str, dict[str, Any]] = {}
        for name, key in self.gemini_tiers.items():
            if not key:
                tier_info[name] = {"is_configured": False, "masked_key": None}
                continue
            tier_info[name] = {"is_configured": True, "masked_key": _mask(key)}

        masked_key = None
        if self.api_key:
            masked_key = _mask(str(self.api_key))

        return {
            "is_configured": bool(self.api_key),
            "masked_key": masked_key,
            "active_tier": self.active_tier,
            "tiers": tier_info,
            "gateway_active_tier": self.active_gateway_tier,
            "provider": "openclaw"
        }

    def _url(self, path: str) -> str:
        """Собирает абсолютный URL с нормализацией слешей."""
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.base_url}{path}"

    async def _request_json(
        self,
        method: str,
        path: str,
        payload: Optional[dict[str, Any]] = None,
        timeout: int = 15,
    ) -> dict[str, Any]:
        """
        Унифицированный HTTP-запрос с безопасным парсингом JSON.
        Возвращает нормализованный словарь без исключений наружу.
        """
        url = self._url(path)
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                request_kwargs: dict[str, Any] = {
                    "headers": self.headers,
                    "timeout": timeout,
                }
                if payload is not None:
                    request_kwargs["json"] = payload

                async with session.request(method.upper(), url, **request_kwargs) as resp:
                    text = await resp.text()
                    data: Any
                    try:
                        data = await resp.json(content_type=None)
                    except Exception:
                        data = {"raw": text}

                return {
                    "ok": 200 <= resp.status < 300,
                    "status": resp.status,
                    "data": data,
                    "url": url,
                    "raw": text,
            }
        except Exception as exc:
            err_text = str(exc).strip()
            if not err_text:
                err_text = exc.__class__.__name__
            logger.warning("OpenClaw request failed path=%s error=%s", path, err_text)
            return {
                "ok": False,
                "status": 0,
                "data": {"error": err_text},
                "url": url,
                "error": err_text,
            }

    def _format_error_detail(self, payload: Any) -> str:
        """Подготавливает понятное описание ошибки из разнообразных ответов."""
        if not payload:
            return "нет дополнительных данных"
        if isinstance(payload, dict):
            candidates = [
                payload.get("error"),
                payload.get("message"),
                payload.get("detail"),
                payload.get("description"),
            ]
            for candidate in candidates:
                if not candidate:
                    continue
                if isinstance(candidate, dict):
                    nested = candidate.get("message") or candidate.get("detail")
                    if nested:
                        return str(nested)
                    return json.dumps(candidate, ensure_ascii=False)
                return str(candidate)
            return json.dumps(payload, ensure_ascii=False)
        return str(payload)

    async def _probe_provider_health_hint(self, model: str) -> Optional[str]:
        """
        Быстрый probe upstream-провайдера, чтобы превратить «Connection error.»
        в диагностическое сообщение (invalid/leaked key, quota и т.д.).
        """
        model_name = str(model or "").strip().lower()
        if not model_name:
            return None

        provider = model_name.split("/", 1)[0]
        if provider not in {"google", "openai"}:
            return None

        # Короткий TTL-кеш, чтобы не спамить внешние API на каждом запросе.
        now = time.time()
        cached = self._provider_probe_cache.get(provider)
        if cached and (now - cached[0]) < 120:
            return cached[1]

        hint = None
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                if provider == "google":
                    api_key, _source = self._resolve_provider_api_key("google")
                    if not api_key:
                        hint = "Google/Gemini API key не задан (env/auth-profiles)."
                    else:
                        url = (
                            "https://generativelanguage.googleapis.com/v1beta/"
                            f"models/gemini-2.5-flash:generateContent?key={api_key}"
                        )
                        payload = {"contents": [{"parts": [{"text": "ping"}]}]}
                        async with session.post(url, json=payload) as resp:
                            text = await resp.text()
                            if resp.status >= 400:
                                hint = f"Google API {resp.status}: {text[:220]}"
                elif provider == "openai":
                    api_key = os.getenv("OPENAI_API_KEY", "").strip() or self._get_auth_profile_api_key("openai")
                    if not api_key:
                        hint = "OPENAI_API_KEY не задан (env/auth-profiles)."
                    else:
                        headers = {"Authorization": f"Bearer {api_key}"}
                        async with session.get("https://api.openai.com/v1/models", headers=headers) as resp:
                            text = await resp.text()
                            if resp.status >= 400:
                                hint = f"OpenAI API {resp.status}: {text[:220]}"
        except Exception as exc:
            hint = f"probe {provider} недоступен: {exc}"

        if hint:
            self._provider_probe_cache[provider] = (now, hint)
        return hint

    def _resolve_provider_api_key(self, provider: str) -> tuple[str, str]:
        """
        Возвращает (api_key, source) для cloud-провайдера без логирования секрета.
        source нужен для диагностики конфигурации.
        """
        normalized = str(provider or "").strip().lower()
        if normalized == "google":
            # R16: tier-aware Gemini ключи из env.
            tier_key = self.gemini_tiers.get(self.active_tier, "").strip()
            if tier_key:
                return tier_key, f"env:GEMINI_API_KEY_{self.active_tier.upper()}"

            free_key = self.gemini_tiers.get("free", "").strip()
            if free_key:
                return free_key, "env:GEMINI_API_KEY_FREE"

            paid_key = self.gemini_tiers.get("paid", "").strip()
            if paid_key:
                return paid_key, "env:GEMINI_API_KEY_PAID"

            env_gemini = os.getenv("GEMINI_API_KEY", "").strip()
            if env_gemini:
                return env_gemini, "env:GEMINI_API_KEY"
            env_google = os.getenv("GOOGLE_API_KEY", "").strip()
            if env_google:
                return env_google, "env:GOOGLE_API_KEY"
            profile_google = self._get_auth_profile_api_key("google")
            if profile_google:
                return profile_google, "auth_profiles:google"
            profile_gemini = self._get_auth_profile_api_key("gemini")
            if profile_gemini:
                return profile_gemini, "auth_profiles:gemini"
            return "", "missing"

        if normalized == "openai":
            env_openai = os.getenv("OPENAI_API_KEY", "").strip()
            if env_openai:
                return env_openai, "env:OPENAI_API_KEY"
            profile_openai = self._get_auth_profile_api_key("openai")
            if profile_openai:
                return profile_openai, "auth_profiles:openai"
            return "", "missing"

        return "", "unsupported_provider"

    def _classify_provider_probe_hint(self, hint: str) -> dict[str, Any]:
        """
        Нормализует provider probe hint в стабильный код/summary/retryable.
        """
        raw = str(hint or "").strip()
        lowered = raw.lower()
        if not lowered:
            return {"code": "ok", "summary": "ok", "retryable": True}

        if "reported as leaked" in lowered:
            return {
                "code": "api_key_leaked",
                "summary": "API key помечен как скомпрометированный (leaked)",
                "retryable": False,
            }
        if "invalid api key" in lowered or "incorrect api key" in lowered:
            return {
                "code": "api_key_invalid",
                "summary": "API key невалидный",
                "retryable": False,
            }
        if (
            "generative language api has not been used" in lowered
            or "api has not been used in project" in lowered
            or "it is disabled" in lowered
            or "enable it by visiting" in lowered
        ):
            return {
                "code": "api_disabled",
                "summary": "Generative Language API не включён в проекте",
                "retryable": False,
            }
        if "permission_denied" in lowered or " 403" in f" {lowered}":
            return {
                "code": "permission_denied",
                "summary": "доступ отклонён провайдером (403)",
                "retryable": False,
            }
        if "unauthorized" in lowered or " 401" in f" {lowered}":
            return {
                "code": "unauthorized",
                "summary": "ошибка авторизации (401)",
                "retryable": False,
            }
        if "quota" in lowered or "billing" in lowered or "out of credits" in lowered:
            return {
                "code": "quota_or_billing",
                "summary": "исчерпан лимит/биллинг",
                "retryable": False,
            }
        if "not found" in lowered or "not_found" in lowered:
            return {
                "code": "model_not_found",
                "summary": "модель/endpoint не найден",
                "retryable": False,
            }
        if "timeout" in lowered or "timed out" in lowered:
            return {
                "code": "timeout",
                "summary": "таймаут соединения",
                "retryable": True,
            }
        if (
            "connection error" in lowered
            or "failed to connect" in lowered
            or "upstream" in lowered
            or "probe" in lowered
        ):
            return {
                "code": "network_error",
                "summary": "ошибка сети/шлюза",
                "retryable": True,
            }
        return {
            "code": "unknown",
            "summary": "неизвестная ошибка провайдера",
            "retryable": True,
        }

    async def get_cloud_provider_diagnostics(
        self,
        providers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Быстрая диагностика ключей cloud-провайдеров (google/openai).
        Для каждого провайдера возвращает:
        - key_present/source,
        - probe status (ok/fail),
        - error class и краткую сводку.
        """
        target = [str(item).strip().lower() for item in (providers or ["google", "openai"]) if str(item).strip()]
        diagnostics: dict[str, Any] = {}
        overall_ok = True

        probe_model = {
            "google": "google/gemini-2.5-flash",
            "openai": "openai/gpt-4o-mini",
        }

        for provider in target:
            key, source = self._resolve_provider_api_key(provider)
            key_present = bool(key)
            key_preview = ""
            if key_present:
                key_preview = f"***{key[-4:]}" if len(key) >= 4 else "***"

            if not key_present:
                diagnostics[provider] = {
                    "ok": False,
                    "provider": provider,
                    "key_present": False,
                    "key_source": source,
                    "key_preview": "",
                    "error_code": "missing_api_key",
                    "summary": "API key не задан",
                    "retryable": False,
                }
                overall_ok = False
                continue

            hint = await self._probe_provider_health_hint(probe_model.get(provider, provider))
            if not hint:
                diagnostics[provider] = {
                    "ok": True,
                    "provider": provider,
                    "key_present": True,
                    "key_source": source,
                    "key_preview": key_preview,
                    "error_code": "ok",
                    "summary": "доступ подтверждён",
                    "retryable": True,
                }
                continue

            classified = self._classify_provider_probe_hint(hint)
            diagnostics[provider] = {
                "ok": False,
                "provider": provider,
                "key_present": True,
                "key_source": source,
                "key_preview": key_preview,
                "error_code": str(classified.get("code", "unknown")),
                "summary": str(classified.get("summary", "ошибка провайдера")),
                "retryable": bool(classified.get("retryable", True)),
                "hint": str(hint)[:260],
            }
            overall_ok = False

        return {
            "ok": overall_ok,
            "providers": diagnostics,
            "checked": target,
            "checked_at": int(time.time()),
        }

    def _load_auth_profiles_payload(self) -> dict[str, Any]:
        """
        Загружает локальный auth-profiles store OpenClaw.
        Возвращает пустой dict, если файл отсутствует/битый.
        """
        profile_path = os.path.expanduser(
            os.getenv(
                "OPENCLAW_AUTH_PROFILES_PATH",
                "~/.openclaw/agents/main/agent/auth-profiles.json",
            )
        )
        if not os.path.exists(profile_path):
            return {}
        try:
            with open(profile_path, "r", encoding="utf-8") as fp:
                payload = json.load(fp)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _get_auth_profile_api_key(self, provider_name: str) -> str:
        """
        Возвращает apiKey из auth-profiles для указанного провайдера.
        Не логирует ключ и не бросает исключения.
        """
        payload = self._load_auth_profiles_payload()
        node = payload.get(str(provider_name).strip().lower())
        if not isinstance(node, dict):
            return ""
        value = node.get("apiKey") or node.get("api_key") or ""
        return str(value).strip()

    async def _probe_first_available(self, paths: list[str], timeout: int = 5) -> dict[str, Any]:
        """Пробует список endpoint'ов и возвращает первый успешный ответ."""
        tried: list[str] = []
        for path in paths:
            tried.append(path)
            result = await self._request_json("GET", path, timeout=timeout)
            if result.get("ok"):
                result["path"] = path
                result["tried"] = tried
                return result

        return {
            "ok": False,
            "status": 0,
            "data": {},
            "error": "no_endpoint_available",
            "tried": tried,
        }

    async def health_check(self) -> bool:
        """Проверяет доступность OpenClaw Gateway."""
        result = await self._request_json("GET", "/health", timeout=4)
        return bool(result.get("ok"))

    async def invoke_tool(self, tool_name: str, args: dict) -> dict:
        """Вызывает tool через OpenClaw API."""
        payload = {"tool": tool_name, "args": args}
        response = await self._request_json("POST", "/tools/invoke", payload=payload, timeout=30)
        if not response.get("ok"):
            return {"error": f"HTTP {response.get('status', 0)}", "details": response.get("data", {})}

        data = response.get("data", {})
        if isinstance(data, dict) and data.get("ok"):
            return data.get("result", {})

        if isinstance(data, dict):
            return {"error": str(data.get("error", "Unknown Error"))}
        return {"error": "Invalid OpenClaw response"}

    async def chat_completions(
        self,
        messages: list,
        model: str = "google/gemini-1.5-flash",
        timeout_seconds: int = 60,
        probe_provider_on_error: bool = True,
    ) -> str:
        """Отправляет chat-completion в OpenClaw Gateway."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "max_tokens": 2048,  # Защита от бесконечного выхлопа
        }
        safe_timeout = int(max(4, timeout_seconds))
        response = await self._request_json(
            "POST",
            "/v1/chat/completions",
            payload=payload,
            timeout=safe_timeout,
        )
        if not response.get("ok"):
            detail = self._format_error_detail(response.get("data") or response.get("error"))
            if probe_provider_on_error and "connection error" in detail.lower():
                provider_hint = await self._probe_provider_health_hint(model)
                if provider_hint:
                    detail = f"{detail} | {provider_hint}"
            return f"❌ OpenClaw Error ({response.get('status', 0)}): {detail}"

        data = response.get("data", {})
        try:
            content = str(data["choices"][0]["message"]["content"] or "")
            lowered = content.strip().lower()
            if probe_provider_on_error and "connection error" in lowered:
                provider_hint = await self._probe_provider_health_hint(model)
                if provider_hint:
                    return f"{content} | {provider_hint}"
            return content
        except Exception:
            detail = self._format_error_detail(data)
            return f"❌ OpenClaw вернул неожиданный формат: {detail}"

    async def get_models(self) -> list[dict[str, Any]]:
        """
        Получает список моделей от OpenClaw Gateway.
        Пробует /v1/models (OpenAI-compatible) и возвращает нормализованный список.
        [HOTFIX] Если endpoint возвращает HTML (SPA), возвращаем пустой список вместо ошибки парсинга.
        """
        result = await self._request_json("GET", "/v1/models")
        if not result.get("ok"):
            logger.warning("OpenClaw get_models failed: %s", result.get("error"))
            return []

        data = result.get("data", {})
        
        # Если в data есть "raw" и там HTML - значит это SPA Gateway, а не API JSON.
        if isinstance(data, dict) and "raw" in data:
            if "<!doctype html>" in str(data["raw"]).lower():
                logger.warning("OpenClaw /v1/models returned HTML instead of JSON. Check Gateway configuration.")
                return []

        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return data["data"]

        if isinstance(data, list):
            return data

        return []

    async def execute_agent_task(self, query: str, agent_id: str = "researcher") -> str:
        """
        Выполняет исследовательскую задачу через web_search + синтез ответа.
        """
        query_text = str(query or "").strip()
        if not query_text:
            return "⚠️ Пустой web-запрос. Передай тему после команды."

        profile = self._resolve_research_profile(agent_id)
        logger.info(
            "OpenClawClient: web research start query=%s count=%s profile=%s",
            query_text,
            profile["count"],
            profile["id"],
        )
        search_results = await self.invoke_tool(
            "web_search",
            {
                "query": query_text,
                "count": profile["count"],
            },
        )

        if "error" in search_results:
            return f"⚠️ Web search failed: {search_results['error']}"

        results_data = self._extract_search_results(
            search_results,
            limit=int(profile["count"]),
        )
        if not results_data:
            return "⚠️ По запросу не найдено релевантных источников."

        fetch_limit = int(profile["web_fetch_limit"])
        if fetch_limit > 0:
            results_data = await self._enrich_results_with_web_fetch(results_data, max_fetch=fetch_limit)

        context = self._build_research_context(results_data)
        instruction_block = (
            "Сделай глубокий аналитический разбор: ключевые факты, риски, противоречия, выводы.\n"
            "Если есть расхождения в источниках — явно укажи их.\n"
        ) if profile["id"] == "research_deep" else (
            "Сделай компактный и практичный разбор: 4-7 пунктов по сути.\n"
        )

        prompt = (
            f"Запрос пользователя: {query_text}\n\n"
            f"{context}\n"
            "Требования к ответу:\n"
            "1) Отвечай на русском языке.\n"
            f"2) {instruction_block}"
            "3) Ссылайся на источники в формате [Название](URL).\n"
            "4) Не выдумывай факты, которых нет в источниках.\n"
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты Senior Research Analyst. "
                    "Делаешь фактологичный анализ только по источникам из контекста."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        research_model = (
            str(os.getenv("OPENCLAW_RESEARCH_MODEL", "google/gemini-1.5-flash")).strip()
            or "google/gemini-1.5-flash"
        )
        return await self.chat_completions(messages, model=research_model)

    async def search(self, query: str) -> str:
        """Shortcut для research-задач."""
        return await self.execute_agent_task(query, agent_id="research")

    def _resolve_research_profile(self, agent_id: str) -> dict[str, Any]:
        """
        Нормализует профиль research-задачи.
        Почему отдельный метод: чтобы команды могли передавать алиасы (`fast/deep`)
        без размазывания логики по хендлерам.
        """
        mode = str(agent_id or "").strip().lower()
        if mode in {"fast", "research_fast"}:
            return {"id": "research_fast", "count": 5, "web_fetch_limit": 0}
        if mode in {"deep", "research_deep", "browser_deep"}:
            return {"id": "research_deep", "count": 14, "web_fetch_limit": 2}
        return {"id": "research", "count": 10, "web_fetch_limit": 1}

    def _sanitize_external_text(self, value: Any, max_chars: int = 1200) -> str:
        """Очищает внешние web-данные от служебных маркеров и шумовых хвостов."""
        text = str(value or "")
        text = (
            text.replace("<<<EXTERNAL_UNTRUSTED_CONTENT>>>", "")
            .replace("<<<END_EXTERNAL_UNTRUSTED_CONTENT>>>", "")
            .replace("Source: Web Search", "")
            .replace("---", "")
            .strip()
        )
        if len(text) > max_chars:
            return text[: max_chars - 1].rstrip() + "…"
        return text

    def _parse_results_from_content(self, content: Any) -> list[Any]:
        """Пытается извлечь список результатов из content-обёртки tool-ответа."""
        chunks: list[str] = []
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict):
                    for key in ("text", "content", "raw"):
                        piece = item.get(key)
                        if isinstance(piece, str) and piece.strip():
                            chunks.append(piece)
                            break

        extracted: list[Any] = []
        for raw_chunk in chunks:
            try:
                parsed = json.loads(raw_chunk)
            except Exception:
                continue
            if isinstance(parsed, list):
                extracted.extend(parsed)
                continue
            if isinstance(parsed, dict):
                for key in ("results", "items", "data"):
                    candidate = parsed.get(key)
                    if isinstance(candidate, list):
                        extracted.extend(candidate)
                        break
        return extracted

    def _extract_search_results(self, payload: Any, limit: int = 10) -> list[dict[str, str]]:
        """
        Нормализует результаты web_search в стабильный список словарей.
        Поддерживает разные формы OpenClaw/tool payload (details/results/content wrapper).
        """
        raw_results: list[Any] = []
        if isinstance(payload, dict):
            details = payload.get("details")
            if isinstance(details, dict):
                for key in ("results", "items", "data"):
                    candidate = details.get(key)
                    if isinstance(candidate, list):
                        raw_results.extend(candidate)
                        break
            for key in ("results", "items"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    raw_results.extend(candidate)
            raw_results.extend(self._parse_results_from_content(payload.get("content")))

        if not raw_results:
            return []

        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_results:
            if isinstance(item, dict):
                title = self._sanitize_external_text(item.get("title") or item.get("name") or item.get("headline") or "Без названия", max_chars=180)
                url = self._sanitize_external_text(item.get("url") or item.get("link") or item.get("href") or "", max_chars=500)
                description = self._sanitize_external_text(
                    item.get("description") or item.get("snippet") or item.get("summary") or item.get("text") or "",
                    max_chars=900,
                )
                published = self._sanitize_external_text(
                    item.get("published") or item.get("date") or item.get("updated_at") or "дата не указана",
                    max_chars=80,
                )
            else:
                title = self._sanitize_external_text(item, max_chars=180) or "Без названия"
                url = ""
                description = ""
                published = "дата не указана"

            dedup_key = f"{url}|{title}".strip().lower()
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            normalized.append(
                {
                    "title": title or "Без названия",
                    "url": url,
                    "description": description,
                    "published": published,
                }
            )
            if len(normalized) >= max(1, int(limit)):
                break
        return normalized

    def _extract_web_fetch_excerpt(self, payload: Any) -> str:
        """Извлекает короткий читаемый фрагмент из web_fetch payload."""
        if not isinstance(payload, dict):
            return ""
        content = payload.get("content")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            first = content[0] if content else None
            if isinstance(first, dict):
                text = str(first.get("text") or first.get("content") or "")
            elif isinstance(first, str):
                text = first

        if not text and isinstance(payload.get("details"), dict):
            details = payload.get("details", {})
            text = str(details.get("content") or details.get("summary") or "")

        return self._sanitize_external_text(text, max_chars=850)

    async def _enrich_results_with_web_fetch(
        self,
        results: list[dict[str, str]],
        max_fetch: int = 1,
    ) -> list[dict[str, str]]:
        """
        Подтягивает 1-2 страницы через web_fetch для более точного deep-анализа.
        Если fetch недоступен, silently продолжаем на сниппетах поиска.
        """
        fetch_budget = max(0, int(max_fetch))
        if fetch_budget == 0:
            return results

        enriched: list[dict[str, str]] = []
        for item in results:
            item_copy = dict(item)
            target_url = str(item_copy.get("url") or "").strip()
            if fetch_budget > 0 and target_url:
                fetched = await self.invoke_tool("web_fetch", {"url": target_url})
                if isinstance(fetched, dict) and not fetched.get("error"):
                    excerpt = self._extract_web_fetch_excerpt(fetched)
                    if excerpt:
                        item_copy["page_excerpt"] = excerpt
                    details = fetched.get("details")
                    if isinstance(details, dict):
                        fetched_title = self._sanitize_external_text(details.get("title") or "", max_chars=180)
                        if fetched_title and item_copy.get("title", "").lower() in {"", "без названия"}:
                            item_copy["title"] = fetched_title
                fetch_budget -= 1
            enriched.append(item_copy)
        return enriched

    def _build_research_context(self, results: list[dict[str, str]]) -> str:
        """Собирает контекст источников для последующего синтеза LLM."""
        lines = ["Источники web_search:"]
        for idx, item in enumerate(results, 1):
            title = item.get("title") or "Без названия"
            url = item.get("url") or "#"
            date = item.get("published") or "дата не указана"
            description = item.get("description") or ""
            page_excerpt = item.get("page_excerpt") or ""
            lines.append(f"{idx}. [{title}]({url}) — {date}")
            if description:
                lines.append(f"   Сниппет: {description}")
            if page_excerpt:
                lines.append(f"   Подробности страницы: {page_excerpt}")
        return "\n".join(lines).strip()

    async def get_auth_provider_health(self) -> dict[str, Any]:
        """Проверяет доступность auth-provider endpoint'ов OpenClaw."""
        paths = [
            "/v1/auth/providers/health",
            "/auth/providers/health",
            "/v1/providers/health",
            "/providers/health",
        ]
        result = await self._probe_first_available(paths)
        payload = result.get("data", {})
        payload_valid = self._looks_like_auth_payload(payload)
        providers = self._normalize_auth_providers(payload)
        required = self._required_auth_providers()
        missing_required = [name for name in required if name not in providers]
        unhealthy_required = [
            name for name in required if name in providers and not bool(providers.get(name, {}).get("healthy", False))
        ]
        lmstudio_profile = self._inspect_local_lmstudio_profile()
        endpoint_available = bool(result.get("ok")) and payload_valid
        status_reason = "ok"
        if not result.get("ok"):
            status_reason = "gateway_route_unavailable"
        elif not payload_valid:
            status_reason = "gateway_route_unavailable"
        elif not lmstudio_profile.get("present"):
            status_reason = "auth_missing_lmstudio_profile"
        elif missing_required:
            status_reason = "required_auth_providers_missing"
        elif unhealthy_required:
            status_reason = "required_auth_providers_unhealthy"

        ready_for_subscriptions = (
            endpoint_available
            and not missing_required
            and not unhealthy_required
            and bool(lmstudio_profile.get("present"))
        )

        return {
            "available": endpoint_available,
            "path": result.get("path"),
            "tried": result.get("tried", paths),
            "status": result.get("status", 0),
            "payload": payload,
            "payload_valid": payload_valid,
            "providers": providers,
            "provider_count": len(providers),
            "required_providers": required,
            "missing_required": missing_required,
            "unhealthy_required": unhealthy_required,
            "ready_for_subscriptions": ready_for_subscriptions,
            "status_reason": status_reason,
            "lmstudio_profile": lmstudio_profile,
            "error": result.get("error"),
        }

    async def get_browser_health(self) -> dict[str, Any]:
        """Проверяет browser-контур OpenClaw (авторизованный automation path)."""
        paths = [
            "/v1/browser/health",
            "/browser/health",
            "/v1/automation/browser/health",
        ]
        result = await self._probe_first_available(paths)
        return {
            "available": bool(result.get("ok")),
            "path": result.get("path"),
            "tried": result.get("tried", paths),
            "status": result.get("status", 0),
            "payload": result.get("data", {}),
            "error": result.get("error"),
        }

    async def get_tools_overview(self) -> dict[str, Any]:
        """Проверяет endpoint реестра tool'ов OpenClaw (если доступен)."""
        paths = [
            "/v1/tools",
            "/tools/registry",
            "/tools",
        ]
        result = await self._probe_first_available(paths)
        payload = result.get("data", {})

        tools_count = 0
        if isinstance(payload, dict):
            if isinstance(payload.get("tools"), list):
                tools_count = len(payload.get("tools", []))
            elif isinstance(payload.get("result"), list):
                tools_count = len(payload.get("result", []))
        elif isinstance(payload, list):
            tools_count = len(payload)

        return {
            "available": bool(result.get("ok")),
            "path": result.get("path"),
            "tried": result.get("tried", paths),
            "status": result.get("status", 0),
            "tools_count": tools_count,
            "payload": payload,
            "error": result.get("error"),
        }

    async def get_health_report(self) -> dict[str, Any]:
        """Агрегированный health-репорт OpenClaw (gateway/auth/browser/tools)."""
        gateway_ok = await self.health_check()
        auth = await self.get_auth_provider_health()
        browser = await self.get_browser_health()
        tools = await self.get_tools_overview()

        return {
            "gateway": gateway_ok,
            "auth": auth,
            "browser": browser,
            "tools": tools,
            "ready_for_subscriptions": bool(auth.get("ready_for_subscriptions")) and bool(browser.get("available")),
            "base_url": self.base_url,
        }

    async def run_tool_smoke(self, query: str = "OpenClaw connectivity check") -> dict[str, Any]:
        """
        Легкий smoke tool-run для проверки реальной исполняемости tool-контура.
        По умолчанию использует web_search с минимальным count.
        """
        result = await self.invoke_tool("web_search", {"query": query, "count": 1})
        if isinstance(result, dict) and result.get("error"):
            return {"ok": False, "tool": "web_search", "error": str(result.get("error"))}

        looks_valid = False
        if isinstance(result, dict):
            if any(key in result for key in ("content", "results", "items", "details")):
                looks_valid = True
            if isinstance(result.get("details"), dict) and "results" in result.get("details", {}):
                looks_valid = True
        return {
            "ok": looks_valid,
            "tool": "web_search",
            "payload_preview": result if isinstance(result, dict) else {"raw": str(result)},
            "error": None if looks_valid else "unexpected_tool_payload",
        }

    async def run_browser_smoke(self, url: str = "https://example.com") -> dict[str, Any]:
        """
        Пытается выполнить легкий browser smoke через OpenClaw.

        Стратегия:
        1) Прямые browser smoke endpoint'ы (если доступны в gateway),
        2) fallback на tool-вызовы browser/web_fetch.
        """
        target_url = (url or "").strip() or "https://example.com"

        endpoint_paths = [
            "/v1/browser/smoke",
            "/browser/smoke",
            "/v1/automation/browser/smoke",
        ]
        endpoint_attempts: list[dict[str, Any]] = []
        for path in endpoint_paths:
            result = await self._request_json(
                "POST",
                path,
                payload={"url": target_url},
                timeout=20,
            )
            attempt = {
                "path": path,
                "ok": bool(result.get("ok")),
                "status": int(result.get("status", 0)),
            }
            endpoint_attempts.append(attempt)
            if result.get("ok") and self._looks_like_browser_payload(result.get("data", {})):
                return {
                    "ok": True,
                    "channel": "endpoint",
                    "path": path,
                    "url": target_url,
                    "payload_preview": result.get("data", {}),
                    "endpoint_attempts": endpoint_attempts,
                    "tool_attempts": [],
                }

        tool_plan = [
            ("browser_open", {"url": target_url}),
            ("browser_navigate", {"url": target_url}),
            ("browser_snapshot", {"url": target_url}),
            ("web_fetch", {"url": target_url}),
        ]
        tool_attempts: list[dict[str, Any]] = []
        for tool_name, args in tool_plan:
            result = await self.invoke_tool(tool_name, args)
            has_error = isinstance(result, dict) and bool(result.get("error"))
            attempt = {"tool": tool_name, "ok": not has_error}
            tool_attempts.append(attempt)
            if not has_error and self._looks_like_browser_payload(result):
                return {
                    "ok": True,
                    "channel": "tool",
                    "tool": tool_name,
                    "url": target_url,
                    "payload_preview": result,
                    "endpoint_attempts": endpoint_attempts,
                    "tool_attempts": tool_attempts,
                }

        return {
            "ok": False,
            "channel": "none",
            "url": target_url,
            "error": "browser_smoke_failed",
            "endpoint_attempts": endpoint_attempts,
            "tool_attempts": tool_attempts,
        }

    async def get_deep_health_report(self) -> dict[str, Any]:
        """
        Расширенный health-check для Ops:
        - базовый health report,
        - smoke tool-run,
        - remediation hints.
        """
        base = await self.get_health_report()
        smoke = await self.run_tool_smoke()
        issues: list[str] = []
        remediations: list[str] = []

        if not base.get("gateway"):
            issues.append("gateway_down")
            remediations.append("Проверь OPENCLAW_BASE_URL и доступность /health.")

        auth = base.get("auth", {})
        if not auth.get("available"):
            issues.append("auth_endpoint_unavailable")
            remediations.append("Проверь auth-plugin routes в OpenClaw и reverse-proxy.")
        if auth.get("status_reason") == "auth_missing_lmstudio_profile":
            issues.append("auth_missing_lmstudio_profile")
            profile_path = ((auth.get("lmstudio_profile") or {}).get("path")) or "~/.openclaw/agents/main/agent/auth-profiles.json"
            remediations.append(
                "Профиль lmstudio отсутствует в auth store OpenClaw. "
                f"Проверь/восстанови: {profile_path}."
            )

        if auth.get("missing_required"):
            issues.append("required_auth_providers_missing")
            remediations.append(
                "Подключи отсутствующие провайдеры из OPENCLAW_REQUIRED_AUTH_PROVIDERS "
                f"({auth.get('missing_required')})."
            )

        if auth.get("unhealthy_required"):
            issues.append("required_auth_providers_unhealthy")
            remediations.append(
                "Почини статусы required провайдеров (credentials/session refresh): "
                f"{auth.get('unhealthy_required')}."
            )

        browser = base.get("browser", {})
        if not browser.get("available"):
            issues.append("browser_path_unavailable")
            remediations.append("Проверь browser automation path в OpenClaw profile.")

        tools = base.get("tools", {})
        if not tools.get("available"):
            issues.append("tools_registry_unavailable")
            remediations.append("Проверь endpoint реестра tools и права API-ключа.")

        if not smoke.get("ok"):
            issues.append("tool_smoke_failed")
            remediations.append("Проверь invoke_tool(web_search) и сетевую доступность search backend.")

        ready = bool(base.get("ready_for_subscriptions")) and bool(smoke.get("ok"))
        return {
            "ready": ready,
            "base": base,
            "tool_smoke": smoke,
            "issues": issues,
            "remediations": remediations,
        }

    async def get_browser_smoke_report(self, url: str = "https://example.com") -> dict[str, Any]:
        """
        Отдельный отчет browser smoke без запуска полного deep-check.
        Используется в командах оператора и web API.
        """
        base = await self.get_health_report()
        smoke = await self.run_browser_smoke(url=url)
        return {
            "base": base,
            "browser_smoke": smoke,
            "ready": bool(base.get("browser", {}).get("available")) and bool(smoke.get("ok")),
        }

    async def get_remediation_plan(self) -> dict[str, Any]:
        """
        Возвращает детальный план исправлений OpenClaw-интеграции.
        Используется для operator-runbook в Telegram/Web.
        """
        deep = await self.get_deep_health_report()
        base = deep.get("base", {})
        auth = base.get("auth", {})
        issues = set(deep.get("issues", []))

        steps: list[dict[str, Any]] = []

        if "gateway_down" in issues:
            steps.append(
                {
                    "priority": "P0",
                    "id": "check_gateway_url",
                    "title": "Проверить OPENCLAW_BASE_URL и доступность gateway",
                    "done": bool(base.get("gateway")),
                    "action": "Проверь /health и сетевой доступ до OpenClaw.",
                }
            )

        if not os.getenv("OPENCLAW_API_KEY", "").strip():
            steps.append(
                {
                    "priority": "P1",
                    "id": "set_openclaw_api_key",
                    "title": "Проверить OPENCLAW_API_KEY",
                    "done": False,
                    "action": "Добавь OPENCLAW_API_KEY в .env, если gateway требует bearer auth.",
                }
            )

        if "auth_endpoint_unavailable" in issues:
            steps.append(
                {
                    "priority": "P0",
                    "id": "fix_auth_routes",
                    "title": "Восстановить auth provider routes",
                    "done": False,
                    "action": "Проверь, что OpenClaw экспортирует /v1/auth/providers/health или совместимый endpoint.",
                }
            )
        if "auth_missing_lmstudio_profile" in issues:
            profile_path = ((auth.get("lmstudio_profile") or {}).get("path")) or "~/.openclaw/agents/main/agent/auth-profiles.json"
            steps.append(
                {
                    "priority": "P0",
                    "id": "restore_lmstudio_profile",
                    "title": "Восстановить lmstudio auth profile",
                    "done": False,
                    "action": (
                        "Добавь/восстанови профиль provider=lmstudio в auth store OpenClaw "
                        f"({profile_path}) и перезапусти gateway."
                    ),
                }
            )

        provider_guide = {
            "openai-codex": "Проверь авторизацию ChatGPT Plus path (openai-codex provider) и обнови сессию.",
            "google-gemini-cli": "Проверь google-gemini-cli auth plugin (Gemini Pro path) и токены.",
            "qwen-portal-auth": "Проверь optional qwen-portal-auth fallback и состояние browser cookies/profile.",
        }

        for name in auth.get("missing_required", []) or []:
            steps.append(
                {
                    "priority": "P1",
                    "id": f"enable_provider_{name}",
                    "title": f"Подключить провайдер {name}",
                    "done": False,
                    "action": provider_guide.get(name, f"Подключи и проверь health провайдера {name}."),
                }
            )
        for name in auth.get("unhealthy_required", []) or []:
            steps.append(
                {
                    "priority": "P1",
                    "id": f"repair_provider_{name}",
                    "title": f"Починить провайдер {name}",
                    "done": False,
                    "action": provider_guide.get(name, f"Почини авторизацию и health провайдера {name}."),
                }
            )

        browser = base.get("browser", {})
        if "browser_path_unavailable" in issues:
            steps.append(
                {
                    "priority": "P1",
                    "id": "fix_browser_path",
                    "title": "Восстановить browser automation path",
                    "done": bool(browser.get("available")),
                    "action": "Проверь OpenClaw browser profile и endpoint /v1/browser/health.",
                }
            )

        if "tool_smoke_failed" in issues:
            steps.append(
                {
                    "priority": "P1",
                    "id": "fix_tool_smoke",
                    "title": "Починить tool smoke web_search",
                    "done": False,
                    "action": "Проверь invoke_tool(web_search), инструменты и сетевой backend поиска.",
                }
            )

        # Если проблем нет, возвращаем короткий позитивный plan.
        if not steps:
            steps.append(
                {
                    "priority": "P3",
                    "id": "no_action_needed",
                    "title": "Критичных проблем не обнаружено",
                    "done": True,
                    "action": "OpenClaw контур готов к работе; поддерживай health мониторинг.",
                }
            )

        # Стабильная сортировка по приоритету.
        prio_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        steps = sorted(steps, key=lambda item: prio_rank.get(str(item.get("priority", "P3")), 99))

        open_items = [item for item in steps if not bool(item.get("done"))]
        return {
            "ready": bool(deep.get("ready")),
            "issues": list(deep.get("issues", [])),
            "steps": steps,
            "open_items": len(open_items),
        }

    def _looks_like_auth_payload(self, payload: Any) -> bool:
        """Эвристика: auth health должен быть структурированным JSON, а не HTML control UI."""
        if not isinstance(payload, dict):
            return False

        if "providers" in payload:
            providers = payload.get("providers")
            return isinstance(providers, (dict, list))

        known_keys = {"required", "missing_required", "unhealthy_required", "ready_for_subscriptions"}
        if known_keys.intersection(set(payload.keys())):
            return True

        raw = str(payload.get("raw", "")).strip().lower()
        if raw.startswith("<!doctype html") or raw.startswith("<html"):
            return False

        return False

    def _inspect_local_lmstudio_profile(self) -> dict[str, Any]:
        """
        Локальная preflight-проверка профиля auth для provider=lmstudio.
        Помогает отличать auth-missing от проблем сети/маршрута gateway.
        """
        profile_path = os.path.expanduser(
            os.getenv(
                "OPENCLAW_AUTH_PROFILES_PATH",
                "~/.openclaw/agents/main/agent/auth-profiles.json",
            )
        )
        info: dict[str, Any] = {
            "path": profile_path,
            "present": False,
            "provider_hint": "lmstudio",
            "error": "",
        }

        if not os.path.exists(profile_path):
            info["error"] = "auth_profiles_file_missing"
            return info

        try:
            with open(profile_path, "r", encoding="utf-8") as fp:
                payload = json.load(fp)
        except Exception as exc:
            info["error"] = f"auth_profiles_parse_error:{exc}"
            return info

        if self._json_contains_lmstudio(payload):
            info["present"] = True
            return info

        info["error"] = "lmstudio_profile_missing"
        return info

    def _json_contains_lmstudio(self, payload: Any) -> bool:
        """Рекурсивно проверяет наличие упоминания provider lmstudio в auth store."""
        if isinstance(payload, dict):
            for key, value in payload.items():
                key_lower = str(key).strip().lower()
                if key_lower == "lmstudio":
                    return True
                if key_lower in {"provider", "provider_id", "name", "id"} and str(value).strip().lower() == "lmstudio":
                    return True
                if self._json_contains_lmstudio(value):
                    return True
            return False

        if isinstance(payload, list):
            return any(self._json_contains_lmstudio(item) for item in payload)

        if isinstance(payload, str):
            return payload.strip().lower() == "lmstudio"

        return False

    def _required_auth_providers(self) -> list[str]:
        """Возвращает обязательные auth-провайдеры из env или дефолтов."""
        raw = os.getenv(
            "OPENCLAW_REQUIRED_AUTH_PROVIDERS",
            "openai-codex,google-gemini-cli,qwen-portal-auth",
        )
        providers = [item.strip() for item in raw.split(",") if item.strip()]
        dedup: list[str] = []
        for name in providers:
            if name not in dedup:
                dedup.append(name)
        return dedup

    def _normalize_auth_providers(self, payload: Any) -> dict[str, dict[str, Any]]:
        """
        Нормализует payload с провайдерами в унифицированный формат:
        {
          "provider-name": {"healthy": bool, "raw": ...}
        }
        """
        providers: dict[str, dict[str, Any]] = {}

        source: Any = payload
        if isinstance(payload, dict) and "providers" in payload:
            source = payload.get("providers")

        if isinstance(source, list):
            for item in source:
                if isinstance(item, str):
                    name = item.strip()
                    if name:
                        providers[name] = {"healthy": True, "raw": item}
                    continue
                if isinstance(item, dict):
                    name = str(item.get("id") or item.get("name") or item.get("provider") or "").strip()
                    if not name:
                        continue
                    providers[name] = {
                        "healthy": self._extract_provider_health(item),
                        "raw": item,
                    }
            return providers

        if isinstance(source, dict):
            for name, value in source.items():
                provider_name = str(name).strip()
                if not provider_name:
                    continue
                healthy = self._extract_provider_health(value)
                providers[provider_name] = {"healthy": healthy, "raw": value}
            return providers

        return providers

    def _extract_provider_health(self, value: Any) -> bool:
        """Извлекает bool-статус здоровья провайдера из разных форм payload."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            return lowered in {"ok", "ready", "healthy", "up", "enabled", "true", "1"}
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, dict):
            for key in ("healthy", "ok", "enabled", "available", "ready"):
                if key in value:
                    return self._extract_provider_health(value.get(key))
            status = value.get("status")
            if status is not None:
                return self._extract_provider_health(status)
            return True
        return False

    def _looks_like_browser_payload(self, payload: Any) -> bool:
        """Эвристика: определяет, что payload похож на валидный browser/web ответ."""
        if not isinstance(payload, dict):
            return False
        keys = set(payload.keys())
        browser_keys = {"url", "title", "html", "content", "snapshot", "result", "details", "page"}
        if keys.intersection(browser_keys):
            return True
        details = payload.get("details")
        if isinstance(details, dict):
            detail_keys = {"url", "title", "html", "content", "snapshot", "page"}
            if set(details.keys()).intersection(detail_keys):
                return True
        content = payload.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and ("text" in first or "html" in first):
                return True
        return False
