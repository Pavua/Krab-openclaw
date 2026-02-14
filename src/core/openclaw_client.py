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
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class OpenClawClient:
    """Клиент интеграции Krab -> OpenClaw Gateway."""

    def __init__(self, base_url: str = "http://localhost:18789", api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Krab/8.0 (OpenClaw-Client)",
        }
        if self.api_key:
            self.headers["Authorization"] = f"Bearer {self.api_key}"

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
            logger.warning("OpenClaw request failed path=%s error=%s", path, exc)
            return {
                "ok": False,
                "status": 0,
                "data": {"error": str(exc)},
                "url": url,
                "error": str(exc),
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

    async def chat_completions(self, messages: list, model: str = "google/gemini-2.0-flash-exp") -> str:
        """Отправляет chat-completion в OpenClaw Gateway."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        response = await self._request_json("POST", "/v1/chat/completions", payload=payload, timeout=60)
        if not response.get("ok"):
            detail = self._format_error_detail(response.get("data") or response.get("error"))
            return f"❌ OpenClaw Error ({response.get('status', 0)}): {detail}"

        data = response.get("data", {})
        try:
            return data["choices"][0]["message"]["content"]
        except Exception:
            detail = self._format_error_detail(data)
            return f"❌ OpenClaw вернул неожиданный формат: {detail}"

    async def get_models(self) -> list[dict[str, Any]]:
        """
        Получает список моделей от OpenClaw Gateway.
        Пробует /v1/models (OpenAI-compatible) и возвращает нормализованный список.
        """
        # 1. Пробуем OpenAI-style endpoint
        result = await self._request_json("GET", "/v1/models")
        if not result.get("ok"):
            logger.warning("OpenClaw get_models failed: %s", result.get("error"))
            return []

        data = result.get("data", {})

        # 2. Нормализация ответа
        # OpenAI style: {"data": [...], "object": "list"}
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return data["data"]

        # Direct list: [...]
        if isinstance(data, list):
            return data

        return []

    async def execute_agent_task(self, query: str, agent_id: str = "researcher") -> str:
        """
        Выполняет исследовательскую задачу через web_search + синтез ответа.
        """
        count = 5 if agent_id == "research_fast" else 10

        logger.info("OpenClawClient: searching query=%s count=%s", query, count)
        search_results = await self.invoke_tool("web_search", {"query": query, "count": count})

        if "error" in search_results:
            return f"⚠️ Search Failed: {search_results['error']}"

        results_data = search_results.get("details", {}).get("results", [])

        if not results_data and "content" in search_results:
            try:
                # Пытаемся извлечь текст если это обертка
                text = ""
                if isinstance(search_results.get("content"), list):
                    text = search_results["content"][0].get("text", "")
                elif isinstance(search_results.get("content"), str):
                    text = search_results["content"]
                
                if text:
                    parsed = json.loads(text)
                    results_data = parsed.get("results", [])
            except Exception:
                pass

        if not results_data:
            return "⚠️ No search results found."

        context = "Search Results (Articles & News):\n"
        for i, result in enumerate(results_data, 1):
            if isinstance(result, dict):
                title = (
                    result.get("title", "No Title")
                    .replace("<<<EXTERNAL_UNTRUSTED_CONTENT>>>", "")
                    .replace("<<<END_EXTERNAL_UNTRUSTED_CONTENT>>>", "")
                    .replace("Source: Web Search", "")
                    .replace("---", "")
                    .strip()
                )
                url = result.get("url", "#")
                description = (
                    result.get("description", "No description")
                    .replace("<<<EXTERNAL_UNTRUSTED_CONTENT>>>", "")
                    .replace("<<<END_EXTERNAL_UNTRUSTED_CONTENT>>>", "")
                    .replace("Source: Web Search", "")
                    .replace("---", "")
                    .strip()
                )
                date = result.get("published") or result.get("date") or "Stable date"
                context += f"{i}. [{title}]({url}) — {date}\n"
                context += f"   Snippet: {description}\n\n"
            else:
                context += f"{i}. {str(result)}\n"

        prompt = (
            f"User Query: {query}\n\n"
            f"{context}\n"
            "INSTRUCTIONS:\n"
            "1. Analyze the search results above carefully. Do NOT ignore articles or deep-dive content.\n"
            "2. Provide a COMPREHENSIVE and DETAILED answer based on all available information.\n"
            "3. Cite sources naturally using the [Title](URL) format.\n"
            "4. If multiple points of view or complex details are present in articles, summarize them thoroughly.\n"
            "5. Answer in the language of the user query (Russian unless specified otherwise)."
        )

        messages = [
            {"role": "system", "content": "You are a Senior Research Analyst. You specialize in deep content analysis and comprehensive reporting. You never skip relevant details or skip articles."},
            {"role": "user", "content": prompt},
        ]

        return await self.chat_completions(messages)

    async def search(self, query: str) -> str:
        """Shortcut для research-задач."""
        return await self.execute_agent_task(query, agent_id="research")

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
        providers = self._normalize_auth_providers(payload)
        required = self._required_auth_providers()
        missing_required = [name for name in required if name not in providers]
        unhealthy_required = [
            name for name in required if name in providers and not bool(providers.get(name, {}).get("healthy", False))
        ]
        ready_for_subscriptions = (
            bool(result.get("ok"))
            and not missing_required
            and not unhealthy_required
        )

        return {
            "available": bool(result.get("ok")),
            "path": result.get("path"),
            "tried": result.get("tried", paths),
            "status": result.get("status", 0),
            "payload": payload,
            "providers": providers,
            "provider_count": len(providers),
            "required_providers": required,
            "missing_required": missing_required,
            "unhealthy_required": unhealthy_required,
            "ready_for_subscriptions": ready_for_subscriptions,
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
