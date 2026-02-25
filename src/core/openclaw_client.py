# -*- coding: utf-8 -*-
"""
OpenClaw Client.

Роль модуля:
1) Каноничный HTTP-клиент к OpenClaw Gateway.
2) OpenClaw-first контур для web/tools/chat.
3) Health/diagnostics по browser/auth/providers без падений при несовместимых API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import aiohttp

# R24: Circuit breaker для защиты OpenClaw Gateway
from src.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError

logger = logging.getLogger(__name__)


@dataclass
class CloudTierState:
    """
    Состояние Cloud Tier в runtime.

    Зачем:
    - Единая точка истины об активном tier (free/paid/default).
    - История переключений для диагностики и observability.
    - sticky_paid блокирует авто-возврат на free после ручного переключения.

    Сохраняется только в памяти процесса (не персистируется на диск).
    """
    active_tier: str = "free"          # Текущий tier: "free" | "paid" | "default"
    last_switch_at: float = 0.0        # time.time() последнего переключения
    switch_reason: str = "init"        # Причина последнего switch
    sticky_paid: bool = False          # Если True, paid не сбрасывается авто-логикой
    switch_count: int = 0              # Количество переключений за сессию процесса


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
        # Runtime-настройки синхронизации paid-tier в OpenClaw конфиг.
        self.openclaw_config_path = os.path.expanduser(
            os.getenv("OPENCLAW_CONFIG_PATH", "~/.openclaw/openclaw.json")
        )
        self.openclaw_agent_models_path = os.path.expanduser(
            os.getenv("OPENCLAW_AGENT_MODELS_PATH", "~/.openclaw/agents/main/agent/models.json")
        )
        self.enable_paid_tier_sync = self._env_flag("OPENCLAW_TIER_SYNC_PAID_KEY", default=True)
        self.enable_paid_tier_restart = self._env_flag("OPENCLAW_TIER_SYNC_RESTART_GATEWAY", default=True)
        try:
            self.paid_tier_sync_cooldown_sec = max(
                5, int(os.getenv("OPENCLAW_TIER_SYNC_COOLDOWN_SEC", "30"))
            )
        except Exception:
            self.paid_tier_sync_cooldown_sec = 30
        self._last_paid_tier_sync_ts = 0.0
        self._paid_tier_applied = False

        # ─── R23: Cloud Tier State ───────────────────────────────────────────
        # Начальный tier определяется по наличию ключей в gemini_tiers.
        _initial_tier = "free" if "free" in self.gemini_tiers else (
            "paid" if "paid" in self.gemini_tiers else "default"
        )
        self._tier_state = CloudTierState(
            active_tier=_initial_tier,
            last_switch_at=time.time(),
            switch_reason="init",
            sticky_paid=False,
            switch_count=0,
        )
        # asyncio.Lock защищает от гонок при параллельных autoswitch.
        # Создаётся лениво в async-контексте через _get_tier_lock().
        self._tier_switch_lock: Optional[asyncio.Lock] = None

        # Cooldown для autoswitch (не переключать чаще N секунд).
        try:
            self._autoswitch_cooldown_sec = max(
                10, int(os.getenv("CLOUD_TIER_AUTOSWITCH_COOLDOWN_SEC", "60"))
            )
        except Exception:
            self._autoswitch_cooldown_sec = 60

        # Sticky paid: если True, paid tier остаётся до ручного reset_cloud_tier().
        # Читается из env при старте; может быть переопределён через reset.
        self._sticky_on_paid = self._env_flag("CLOUD_TIER_STICKY_ON_PAID", default=True)

        # ─── R23: Runtime-метрики (prometheus-style счётчики) ────────────────
        # Не логируем содержимое секретов, только счётчики событий.
        self._metrics: dict[str, int] = {
            "cloud_attempts_total": 0,      # Попыток вызова cloud API
            "cloud_failures_total": 0,      # Неудачных cloud-вызовов
            "tier_switch_total": 0,         # Переключений тира
            "force_cloud_failfast_total": 0, # Fail-fast в force_cloud режиме
        }

        # ─── R24: Circuit Breaker ─────────────────────────────────────────────
        # Защищает OpenClaw от каскадных отказов.
        # При N=5 отказах/60с → OPEN (блокирует запросы на 30с).
        # Параметры читаются из конфига через BREAKER_* переменные.
        # Для передачи конфига использовать OpenClawClient.configure_breaker().
        self._breaker = CircuitBreaker()

        self._provider_probe_cache: dict[str, tuple[float, str]] = {}
        self._health_cli_probe_cache: tuple[float, bool] | None = None
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

    @staticmethod
    def _env_flag(name: str, default: bool = False) -> bool:
        """Парсит bool-переменные окружения в формате 1/0, true/false, on/off."""
        raw = os.getenv(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def _read_json_file(self, path: str) -> dict[str, Any]:
        """Безопасно читает JSON-файл. При ошибке возвращает пустой dict."""
        if not path or not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as fp:
                payload = json.load(fp)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _write_json_file(self, path: str, payload: dict[str, Any]) -> bool:
        """Атомарно записывает JSON-файл без утечки ключей в лог."""
        if not path or not isinstance(payload, dict):
            return False
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp_path = f"{path}.tmp.{os.getpid()}"
            with open(tmp_path, "w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
            return True
        except Exception:
            return False

    @staticmethod
    def _extract_google_api_key(payload: dict[str, Any]) -> str:
        """Достает models.providers.google.apiKey из OpenClaw payload."""
        try:
            models = payload.get("models", {})
            providers = models.get("providers", {})
            google = providers.get("google", {})
            value = google.get("apiKey") or google.get("api_key") or ""
            return str(value).strip()
        except Exception:
            return ""

    @staticmethod
    def _inject_google_api_key(payload: dict[str, Any], api_key: str) -> bool:
        """Обновляет models.providers.google.apiKey. Возвращает True, если payload изменился."""
        if not isinstance(payload, dict):
            return False
        models = payload.get("models")
        if not isinstance(models, dict):
            return False
        providers = models.get("providers")
        if not isinstance(providers, dict):
            return False
        google = providers.get("google")
        if not isinstance(google, dict):
            return False
        current = str(google.get("apiKey") or "").strip()
        if current == api_key:
            return False
        google["apiKey"] = api_key
        return True

    def _sync_paid_tier_google_key(self) -> bool:
        """
        Применяет GEMINI_API_KEY_PAID в OpenClaw конфиг и перезапускает gateway.
        Выполняется только при первом переключении на paid-tier.
        """
        paid_key = str(self.gemini_tiers.get("paid", "") or "").strip()
        if not paid_key:
            return False

        now_ts = time.time()
        if (now_ts - float(self._last_paid_tier_sync_ts)) < float(self.paid_tier_sync_cooldown_sec):
            return self._paid_tier_applied

        openclaw_payload = self._read_json_file(self.openclaw_config_path)
        current_openclaw_key = self._extract_google_api_key(openclaw_payload)
        if current_openclaw_key == paid_key:
            self._paid_tier_applied = True
            self._last_paid_tier_sync_ts = now_ts
            return True

        changed_any = False
        targets = [self.openclaw_config_path, self.openclaw_agent_models_path]
        for path in targets:
            payload = self._read_json_file(path)
            if not payload:
                continue
            changed = self._inject_google_api_key(payload, paid_key)
            if changed and self._write_json_file(path, payload):
                changed_any = True

        if not changed_any:
            self._last_paid_tier_sync_ts = now_ts
            return False

        if self.enable_paid_tier_restart:
            try:
                proc = subprocess.run(
                    ["openclaw", "gateway", "restart"],
                    capture_output=True,
                    text=True,
                    timeout=45,
                    check=False,
                )
                if proc.returncode != 0:
                    self._last_paid_tier_sync_ts = now_ts
                    return False
            except Exception:
                self._last_paid_tier_sync_ts = now_ts
                return False

        self._paid_tier_applied = True
        self._last_paid_tier_sync_ts = now_ts
        return True

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
            if tier_name == "paid" and self.enable_paid_tier_sync and not self._paid_tier_applied:
                if not self._sync_paid_tier_google_key():
                    logger.warning(
                        "OpenClaw tier switch blocked: failed to sync paid Gemini key to gateway config."
                    )
                else:
                    self.active_tier = tier_name
                    switched = True
            else:
                self.active_tier = tier_name
                switched = True

        if tier_name in self.gateway_tiers:
            self.api_key = self.gateway_tiers[tier_name]
            self.active_gateway_tier = tier_name
            self._update_auth_header()
            switched = True

        if switched:
            logger.info("OpenClaw tier switched: %s", tier_name)
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

    def _sanitize_assistant_output(self, text: str) -> str:
        """
        Убирает служебные артефакты OpenClaw/tool-рантайма из пользовательского ответа.
        Нужен как fail-safe для каналов, где upstream может вернуть сырой служебный хвост.
        """
        cleaned = str(text or "")
        if not cleaned:
            return ""

        cleaned = cleaned.replace("<|begin_of_box|>", "")
        cleaned = cleaned.replace("<|end_of_box|>", "")
        cleaned = re.sub(r"<\|[^|>]+?\|>", "", cleaned)
        cleaned = re.sub(
            r"\{[^{}\n]*\"action\"\s*:\s*\"[^\"]+\"[^{}\n]*\}",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\{[^{}\n]*\\u[0-9a-fA-F]{4}[^{}\n]*\"action\"\s*:\s*\"[^\"]+\"[^{}\n]*\}",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\[\[reply_to:\d+\]\]\s*", "", cleaned, flags=re.IGNORECASE)

        filtered_lines: list[str] = []
        for line in cleaned.splitlines():
            low = line.strip().lower()
            if low in {"```", "```json", "```text", "```yaml"}:
                continue
            if "[[reply_to:" in low:
                continue
            if "\"action\"" in low and "not found" in low:
                continue
            if " not found" in low and ("begin_of_box" in low or "reply_to" in low):
                continue
            if low.endswith("not found"):
                line = re.sub(r"\s*not found\s*$", "", line, flags=re.IGNORECASE).rstrip()
                if not line:
                    continue
            filtered_lines.append(line)

        cleaned = "\n".join(filtered_lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned

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

        R23: Расширена классификация:
        - resource_exhausted / 429 → quota_or_billing (триггер autoswitch)
        - html вместо json → gateway_unavailable
        - gateway недоступен (не TCP-сеть) → gateway_unavailable
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
                "triggers_autoswitch": False,
            }
        if "invalid api key" in lowered or "incorrect api key" in lowered:
            return {
                "code": "api_key_invalid",
                "summary": "API key невалидный",
                "retryable": False,
                "triggers_autoswitch": False,
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
                "triggers_autoswitch": False,
            }
        if "permission_denied" in lowered or " 403" in f" {lowered}":
            return {
                "code": "permission_denied",
                "summary": "доступ отклонён провайдером (403)",
                "retryable": False,
                "triggers_autoswitch": False,
            }
        if "unauthorized" in lowered or " 401" in f" {lowered}":
            return {
                "code": "unauthorized",
                "summary": "ошибка авторизации (401)",
                "retryable": False,
                "triggers_autoswitch": False,
            }
        # R23: resource_exhausted и 429 добавлены как явные квота-триггеры.
        # triggers_autoswitch=True → при chat_completions() запустится try_autoswitch_to_paid().
        if (
            "quota" in lowered
            or "billing" in lowered
            or "out of credits" in lowered
            or "resource_exhausted" in lowered
            or "resource exhausted" in lowered
            or " 429" in f" {lowered}"
        ):
            return {
                "code": "quota_or_billing",
                "summary": "исчерпан лимит/биллинг (квота или 429)",
                "retryable": False,
                "triggers_autoswitch": True,
            }
        if "not found" in lowered or "not_found" in lowered:
            return {
                "code": "model_not_found",
                "summary": "модель/endpoint не найден",
                "retryable": False,
                "triggers_autoswitch": False,
            }
        if "timeout" in lowered or "timed out" in lowered:
            return {
                "code": "timeout",
                "summary": "таймаут соединения",
                "retryable": True,
                "triggers_autoswitch": False,
            }
        # R23: gateway_unavailable — Gateway вернул HTML вместо JSON API
        # или поднялся ошибочный процесс на порту.
        if "html instead of json" in lowered or "gateway unavailable" in lowered:
            return {
                "code": "gateway_unavailable",
                "summary": "Gateway недоступен или вернул HTML вместо JSON API",
                "retryable": True,
                "triggers_autoswitch": False,
            }
        if (
            "connection error" in lowered
            or "failed to connect" in lowered
            or "upstream" in lowered
            or "probe" in lowered
        ):
            return {
                "code": "network",
                "summary": "ошибка сети/шлюза",
                "retryable": True,
                "triggers_autoswitch": False,
            }
        return {
            "code": "unknown",
            "summary": "неизвестная ошибка провайдера",
            "retryable": True,
            "triggers_autoswitch": False,
        }

    # ─── R23: Tier State Management ──────────────────────────────────────────

    def _get_tier_lock(self) -> asyncio.Lock:
        """
        Возвращает asyncio.Lock для tier switch.
        Создаётся лениво при первом вызове в async-контексте.
        Это позволяет использовать OpenClawClient в синхронном init,
        не создавая event loop преждевременно.
        """
        if self._tier_switch_lock is None:
            self._tier_switch_lock = asyncio.Lock()
        return self._tier_switch_lock

    async def try_autoswitch_to_paid(self, reason: str = "quota_or_billing") -> bool:
        """
        Безопасное переключение на paid tier при исчерпании квоты free tier.

        Защиты:
        - asyncio.Lock: исключает гонку при параллельных вызовах.
        - cooldown: не переключает чаще _autoswitch_cooldown_sec секунд.
        - sticky_paid: если уже активен и sticky — не трогает.
        - Нет paid ключа → возвращает False без лишних попыток.

        Не логирует сами ключи — только факт переключения и причину.

        Возвращает True если переключение выполнено, False иначе.
        """
        # Быстрая проверка вне lock — если paid уже активен, ничего делать не надо.
        if self._tier_state.active_tier == "paid":
            return True

        paid_key = str(self.gemini_tiers.get("paid", "") or "").strip()
        if not paid_key:
            logger.debug("CloudTier autoswitch skipped: GEMINI_API_KEY_PAID не задан.")
            return False

        lock = self._get_tier_lock()
        async with lock:
            # После захвата lock перепроверяем — другой coroutine мог уже переключить.
            if self._tier_state.active_tier == "paid":
                return True

            now = time.time()
            last_sw = float(self._tier_state.last_switch_at or 0.0)
            elapsed = now - last_sw

            if elapsed < float(self._autoswitch_cooldown_sec):
                remaining = float(self._autoswitch_cooldown_sec) - elapsed
                logger.info(
                    "CloudTier autoswitch cooldown active: %.0fs остаток (cooldown=%ss).",
                    remaining,
                    self._autoswitch_cooldown_sec,
                )
                return False

            # Выполняем переключение на paid tier.
            # Ключ уже проверен выше — присваиваем active_tier.
            switched = self.set_tier("paid")
            if not switched:
                logger.warning(
                    "CloudTier autoswitch failed: set_tier('paid') не применился (sync/restart issue)."
                )
                return False

            prev_tier = self._tier_state.active_tier
            self._tier_state.active_tier = "paid"
            self._tier_state.last_switch_at = time.time()
            self._tier_state.switch_reason = reason
            self._tier_state.switch_count += 1
            if self._sticky_on_paid:
                self._tier_state.sticky_paid = True

            # Обновляем active_tier на объекте для совместимости с legacy-кодом.
            self.active_tier = "paid"

            # Инкрементируем метрику.
            self._metrics["tier_switch_total"] += 1

            logger.info(
                "CloudTier autoswitch: %s → paid | reason=%s | switch_count=%d | sticky=%s",
                prev_tier,
                reason,
                self._tier_state.switch_count,
                self._tier_state.sticky_paid,
            )
            return True

    def get_tier_state_export(self) -> dict[str, Any]:
        """
        Экспортирует CloudTierState и runtime-метрики для диагностики.

        Зачем:
        - Единый endpoint /api/openclaw/cloud/tier/state читает этот метод.
        - Не содержит секретов (ключи не включаются).
        - Включает: active_tier, switch_count, sticky_paid, метрики.

        Вызывается из web_app.py endpoint GET /api/openclaw/cloud/tier/state.
        """
        state = self._tier_state
        # Доступные tiers (без значений ключей — только имена).
        available_tiers = list(self.gemini_tiers.keys()) if self.gemini_tiers else ["default"]
        return {
            "active_tier": state.active_tier,
            "last_switch_at": state.last_switch_at,
            "switch_reason": state.switch_reason,
            "sticky_paid": state.sticky_paid,
            "switch_count": state.switch_count,
            "available_tiers": available_tiers,
            "autoswitch_cooldown_sec": self._autoswitch_cooldown_sec,
            "sticky_on_paid_config": self._sticky_on_paid,
            "metrics": dict(self._metrics),
            # R24: circuit breaker диагностика
            "circuit_breaker": self._breaker.get_diagnostics(),
        }

    async def reset_cloud_tier(self) -> dict[str, Any]:
        """
        Сбрасывает cloud tier на free (ручной reset через API).

        Зачем:
        - Используется через POST /api/openclaw/cloud/tier/reset.
        - Снимает sticky_paid флаг.
        - Не требует перезапуска — влияет мгновенно.

        Возвращает: {ok, previous_tier, new_tier, reset_at}.
        """
        lock = self._get_tier_lock()
        async with lock:
            prev = self._tier_state.active_tier
            free_key = str(self.gemini_tiers.get("free", "") or "").strip()
            # Определяем новый tier: free если есть ключ, иначе default.
            new_tier = "free" if free_key else "default"

            self._tier_state.active_tier = new_tier
            self._tier_state.last_switch_at = time.time()
            self._tier_state.switch_reason = "manual_reset"
            self._tier_state.sticky_paid = False
            self._tier_state.switch_count += 1

            # Обновляем legacy-атрибут для совместимости.
            self.active_tier = new_tier
            self._metrics["tier_switch_total"] += 1

            logger.info(
                "CloudTier manual reset: %s → %s | switch_count=%d",
                prev,
                new_tier,
                self._tier_state.switch_count,
            )

        return {
            "ok": True,
            "previous_tier": prev,
            "new_tier": new_tier,
            "reset_at": self._tier_state.last_switch_at,
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
        gateway_probe = await self._probe_gateway_api_health()
        gateway_ok = bool(gateway_probe.get("ok"))
        if not gateway_ok:
            overall_ok = False

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
                if not gateway_ok:
                    diagnostics[provider] = {
                        "ok": False,
                        "provider": provider,
                        "key_present": True,
                        "key_source": source,
                        "key_preview": key_preview,
                        "error_code": str(gateway_probe.get("error_code", "gateway_api_unavailable")),
                        "summary": str(gateway_probe.get("summary", "gateway API недоступен")),
                        "retryable": bool(gateway_probe.get("retryable", True)),
                    }
                    overall_ok = False
                    continue
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
            "ok": overall_ok and gateway_ok,
            "providers": diagnostics,
            "checked": target,
            "checked_at": int(time.time()),
            "gateway": gateway_probe,
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
        """
        Проверяет доступность OpenClaw Gateway.

        Почему не только HTTP 200:
        иногда на API-роуты попадает HTML control UI (reverse-proxy/SPA fallback).
        Такой ответ не считается рабочим API.
        """
        result = await self._request_json("GET", "/health", timeout=4)
        if not bool(result.get("ok")):
            return False

        payload = result.get("data", {})
        if self._payload_contains_html(payload):
            if self._probe_gateway_cli_health():
                logger.info("OpenClaw health_check: /health вернул HTML, но CLI probe подтвердил рабочий runtime.")
                return True
            logger.warning("OpenClaw health_check: /health returned HTML instead of JSON API")
            return False

        if self._looks_like_gateway_health_payload(payload):
            return True

        # Fallback: некоторые инсталляции отдают минимальный /health,
        # поэтому валидируем API-контур через /v1/models.
        models_probe = await self._request_json("GET", "/v1/models", timeout=4)
        if not bool(models_probe.get("ok")):
            return False
        models_payload = models_probe.get("data", {})
        if self._payload_contains_html(models_payload):
            if self._probe_gateway_cli_health():
                logger.info("OpenClaw health_check: /v1/models вернул HTML, но CLI probe подтвердил рабочий runtime.")
                return True
            logger.warning("OpenClaw health_check: /v1/models returned HTML instead of JSON API")
            return False
        return self._looks_like_models_payload(models_payload)

    def _probe_gateway_cli_health(self, timeout: int = 8, cache_ttl_sec: int = 5) -> bool:
        """
        Fallback-проверка runtime через CLI, если HTTP-роуты отдают control UI HTML.
        Кэшируем коротко, чтобы не запускать subprocess на каждом health-пуле.
        """
        now_ts = time.time()
        if self._health_cli_probe_cache:
            cached_ts, cached_state = self._health_cli_probe_cache
            if now_ts - cached_ts <= max(1, cache_ttl_sec):
                return bool(cached_state)

        if str(os.getenv("OPENCLAW_HEALTH_CLI_FALLBACK", "1")).strip().lower() in {"0", "false", "off", "no"}:
            self._health_cli_probe_cache = (now_ts, False)
            return False

        try:
            proc = subprocess.run(
                ["openclaw", "channels", "status", "--probe"],
                capture_output=True,
                text=True,
                timeout=max(3, int(timeout)),
                check=False,
            )
        except FileNotFoundError:
            logger.debug("OpenClaw health_check: CLI fallback недоступен (openclaw не найден в PATH).")
            self._health_cli_probe_cache = (now_ts, False)
            return False
        except Exception as exc:
            logger.warning("OpenClaw health_check: CLI fallback failed: %s", str(exc) or exc.__class__.__name__)
            self._health_cli_probe_cache = (now_ts, False)
            return False

        combined_output = f"{proc.stdout}\n{proc.stderr}".lower()
        is_ok = proc.returncode == 0 and "gateway reachable" in combined_output
        self._health_cli_probe_cache = (now_ts, bool(is_ok))
        return bool(is_ok)

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
        """
        Отправляет chat-completion в OpenClaw Gateway.

        R23: Инкрементирует cloud_attempts_total/cloud_failures_total.
        При quota_or_billing / 429 запускает try_autoswitch_to_paid().
        R24: Оборачивает в CircuitBreaker — при OPEN возвращает ошибку немедленно.
        """
        async def _do_call():
            self._metrics["cloud_attempts_total"] += 1

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
                self._metrics["cloud_failures_total"] += 1
                detail = self._format_error_detail(response.get("data") or response.get("error"))
                detail_lower = detail.lower()

                # R23: Если ответ содержит признаки квоты/429 — пробуем autoswitch на paid.
                classified = self._classify_provider_probe_hint(detail)
                if classified.get("triggers_autoswitch"):
                    switched = await self.try_autoswitch_to_paid(
                        reason=classified.get("code", "quota_or_billing")
                    )
                    if switched:
                        logger.info(
                            "CloudTier: autoswitch выполнен после quota-ошибки. "
                            "Повторяем текущий запрос через paid tier."
                        )
                        retry_response = await self._request_json(
                            "POST",
                            "/v1/chat/completions",
                            payload=payload,
                            timeout=safe_timeout,
                        )
                        if retry_response.get("ok"):
                            retry_data = retry_response.get("data", {})
                            try:
                                retry_content = str(retry_data["choices"][0]["message"]["content"] or "")
                                return self._sanitize_assistant_output(retry_content)
                            except Exception:
                                detail = self._format_error_detail(retry_data)
                                raise Exception(f"OpenClaw retry вернул неожиданный формат: {detail}")
                        detail = self._format_error_detail(
                            retry_response.get("data") or retry_response.get("error")
                        )
                        detail_lower = detail.lower()

                if probe_provider_on_error and "connection error" in detail_lower:
                    provider_hint = await self._probe_provider_health_hint(model)
                    if provider_hint:
                        detail = f"{detail} | {provider_hint}"
                # Поднимаем исключение, чтобы breaker засчитал отказ
                raise Exception(f"OpenClaw Error ({response.get('status', 0)}): {detail}")

            data = response.get("data", {})
            try:
                content = str(data["choices"][0]["message"]["content"] or "")
                lowered = content.strip().lower()
                if probe_provider_on_error and "connection error" in lowered:
                    provider_hint = await self._probe_provider_health_hint(model)
                    if provider_hint:
                        return self._sanitize_assistant_output(f"{content} | {provider_hint}")
                return self._sanitize_assistant_output(content)
            except Exception:
                self._metrics["cloud_failures_total"] += 1
                detail = self._format_error_detail(data)
                raise Exception(f"OpenClaw вернул неожиданный формат: {detail}")

        cloud_coro = _do_call()
        try:
            return await self._breaker.call(cloud_coro)
        except CircuitBreakerOpenError as exc:
            # В OPEN breaker мог отклонить запрос до await корутины.
            # Явно закрываем, чтобы не получить RuntimeWarning "never awaited".
            try:
                cloud_coro.close()
            except Exception:
                pass
            self._metrics["cloud_failures_total"] += 1
            return f"❌ OpenClaw Circuit OPEN: gateway недоступен. {str(exc)}"
        except Exception as exc:
            return f"❌ {str(exc)}"



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

    def _payload_contains_html(self, payload: Any) -> bool:
        """
        Определяет, что payload фактически HTML-страница control UI.
        Используется как защитный слой для health/models/auth endpoint'ов.
        """
        if isinstance(payload, dict):
            raw = str(payload.get("raw", "")).strip().lower()
            if raw.startswith("<!doctype html") or raw.startswith("<html"):
                return True
            return False
        if isinstance(payload, str):
            lowered = payload.strip().lower()
            return lowered.startswith("<!doctype html") or lowered.startswith("<html")
        return False

    def _looks_like_gateway_health_payload(self, payload: Any) -> bool:
        """
        Эвристика валидного JSON payload для /health.
        Не принимаем пустой dict и HTML-ответ.
        """
        if not isinstance(payload, dict):
            return False
        if self._payload_contains_html(payload):
            return False
        if not payload:
            return False
        known_keys = {"ok", "status", "healthy", "ready", "service", "version", "uptime", "gateway"}
        return bool(set(payload.keys()).intersection(known_keys))

    def _looks_like_models_payload(self, payload: Any) -> bool:
        """Проверяет, что /v1/models вернул API-совместимую структуру."""
        if self._payload_contains_html(payload):
            return False
        if isinstance(payload, dict):
            if isinstance(payload.get("data"), list):
                return True
            if isinstance(payload.get("models"), list):
                return True
            return False
        if isinstance(payload, list):
            return True
        return False

    async def _probe_gateway_api_health(self) -> dict[str, Any]:
        """
        Проверяет готовность именно API-контура OpenClaw (не только доступность UI).
        """
        def _ws_runtime_fallback_ok(summary: str) -> dict[str, Any]:
            """
            Для новых сборок OpenClaw, где HTTP-порт отдаёт Control UI (HTML),
            принимаем runtime как рабочий, если CLI probe подтверждает gateway reachable.
            """
            if self._probe_gateway_cli_health():
                return {
                    "ok": True,
                    "status": 200,
                    "error_code": "ok_ws_runtime",
                    "summary": summary,
                    "transport": "ws_runtime_fallback",
                }
            return {
                "ok": False,
                "status": 200,
                "error_code": "gateway_api_unavailable",
                "summary": "gateway вернул HTML, а CLI health fallback не подтвердил runtime",
                "retryable": True,
            }

        health_result = await self._request_json("GET", "/health", timeout=4)
        if not bool(health_result.get("ok")):
            status = int(health_result.get("status", 0))
            return {
                "ok": False,
                "status": status,
                "error_code": "gateway_api_unavailable",
                "summary": f"gateway /health недоступен (HTTP {status})",
                "retryable": True,
            }

        health_payload = health_result.get("data", {})
        if self._payload_contains_html(health_payload):
            return _ws_runtime_fallback_ok("gateway runtime reachable (CLI fallback), HTTP /health вернул HTML")

        if self._looks_like_gateway_health_payload(health_payload):
            return {
                "ok": True,
                "status": int(health_result.get("status", 0)),
                "error_code": "ok",
                "summary": "gateway API health ok",
            }

        models_result = await self._request_json("GET", "/v1/models", timeout=5)
        if not bool(models_result.get("ok")):
            status = int(models_result.get("status", 0))
            return {
                "ok": False,
                "status": status,
                "error_code": "gateway_api_unavailable",
                "summary": f"gateway /v1/models недоступен (HTTP {status})",
                "retryable": True,
            }

        models_payload = models_result.get("data", {})
        if not self._looks_like_models_payload(models_payload):
            if self._payload_contains_html(models_payload):
                return _ws_runtime_fallback_ok(
                    "gateway runtime reachable (CLI fallback), HTTP /v1/models вернул HTML"
                )
            return {
                "ok": False,
                "status": int(models_result.get("status", 0)),
                "error_code": "gateway_api_unavailable",
                "summary": "gateway /v1/models вернул не-API payload",
                "retryable": False,
            }

        return {
            "ok": True,
            "status": int(models_result.get("status", 0)),
            "error_code": "ok",
            "summary": "gateway API models ok",
        }

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
