# -*- coding: utf-8 -*-
"""
Совместимый web-роутер поверх нового runtime-стека.

Зачем нужен этот слой:
- web_app и web-панель исторически ждут старый контракт NexusRouter;
- после рефакторинга часть методов стала заглушками, из-за чего UI получал
  слишком бедные `recommend` / `preflight` / `feedback` payload'ы;
- здесь мы восстанавливаем только полезный совместимый контракт, опираясь на
  текущие источники истины: ModelManager, OpenClawClient и runtime route meta.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
import json
from pathlib import Path
from typing import Any, Optional

import structlog

from ..config import config
from ..core.local_health import fetch_lm_studio_models_list
from ..core.model_types import ModelInfo, ModelType

logger = structlog.get_logger(__name__)


def _runtime_primary_model() -> str:
    """
    Возвращает primary-модель из живого OpenClaw runtime.

    Почему compat-слой читает это сам:
    - `config.MODEL` живёт в `.env` и может отставать от runtime-конфига OpenClaw;
    - owner UI должен по умолчанию показывать именно текущий production primary,
      а не исторический env-хвост вроде `gpt-4.5-preview`.
    """
    cfg_path = Path.home() / ".openclaw" / "openclaw.json"
    try:
        payload = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return str(config.MODEL or "").strip()

    agents = payload.get("agents") if isinstance(payload, dict) else {}
    defaults = agents.get("defaults") if isinstance(agents, dict) else {}
    model_defaults = defaults.get("model") if isinstance(defaults, dict) else {}
    if not isinstance(model_defaults, dict):
        # `openclaw onboard` на чистой учётке может оставить `model: null`.
        # Для owner UI это не повод падать: просто откатываемся к env-fallback.
        model_defaults = {}
    primary = str(model_defaults.get("primary", "") or "").strip()
    return primary or str(config.MODEL or "").strip()


class WebRouterCompat:
    """
    Совместимая замена старого NexusRouter для web-панели.

    Важно:
    - не пытается вернуть старый монолит;
    - собирает только те данные, которые реально нужны текущему UI;
    - старается не спорить с фактическим runtime-маршрутом OpenClawClient.
    """

    def __init__(self, model_manager: Any, openclaw_client: Any):
        self._mm = model_manager
        self.openclaw_client = openclaw_client
        self._stats: dict[str, int] = {"local_failures": 0, "cloud_failures": 0}
        self._last_route: dict[str, Any] = {}
        self._feedback: list[dict[str, Any]] = []
        self.force_mode: Optional[str] = None
        self.active_tier: str = getattr(openclaw_client, "active_tier", "free")
        self.cloud_soft_cap_reached: bool = False
        self.rag = None
        self.models: dict[str, str] = {"chat": _runtime_primary_model()}
        self._local_preferred_model_override: Optional[str] = None

    # --- Properties the old router exposed ---

    @property
    def is_local_available(self) -> bool:
        return self._mm._current_model is not None

    @property
    def local_engine(self) -> str:
        return "lm_studio"

    @property
    def active_local_model(self) -> Optional[str]:
        return self._mm._current_model

    @active_local_model.setter
    def active_local_model(self, value: Optional[str]) -> None:
        self._mm._current_model = value

    @property
    def cost_engine(self) -> Any:
        return self._mm.cost_analytics

    @property
    def cost_analytics(self) -> Any:
        return self._mm.cost_analytics

    @property
    def local_preferred_model(self) -> Optional[str]:
        """
        Совместимое preferred-local поле для старых web write-endpoint'ов.

        Почему это нужно:
        - web_app всё ещё ожидает старый атрибут `router.local_preferred_model`;
        - после рефакторинга compat-роутер его потерял, из-за чего `load-default`
          ложно считал preferred model не настроенной;
        - источником истины оставляем config, но допускаем runtime override.
        """
        override = str(self._local_preferred_model_override or "").strip()
        if override:
            return override

        preferred = str(getattr(config, "LOCAL_PREFERRED_MODEL", "") or "").strip()
        if preferred and preferred.lower() not in {"auto", "smallest"}:
            return preferred

        current = str(getattr(self._mm, "_current_model", "") or "").strip()
        if current:
            try:
                if self._mm.is_local_model(current):
                    return current
            except Exception:  # noqa: BLE001
                return current
        return None

    @local_preferred_model.setter
    def local_preferred_model(self, value: Optional[str]) -> None:
        normalized = str(value or "").strip()
        self._local_preferred_model_override = normalized or None

    # --- Model info ---

    def get_model_info(self) -> dict[str, Any]:
        current_chat_model = str(self.models.get("chat") or _runtime_primary_model()).strip()
        return {
            "current_model": current_chat_model,
            "models": dict(self.models),
            "force_cloud": config.FORCE_CLOUD,
            "lm_studio_url": config.LM_STUDIO_URL,
        }

    # --- Local model management (delegates to ModelManager) ---

    async def load_local_model(self, model_name: str) -> bool:
        return await self._mm.load_model(model_name)

    async def unload_model_manual(self, model_name: str) -> bool:
        await self._mm.unload_model(model_name)
        return True

    async def unload_models_manual(self) -> None:
        await self._mm.unload_all()

    async def unload_local_model(self, model_name: str) -> bool:
        await self._mm.unload_model(model_name)
        return True

    async def _smart_load(self, model_id: str, reason: str = "") -> bool:
        logger.info("web_smart_load", model=model_id, reason=reason)
        return await self._mm.load_model(model_id)

    async def _evict_idle_models(self, needed_gb: float = 0.0) -> float:
        await self._mm.unload_all()
        ram = self._mm.get_ram_usage()
        return ram.get("available_gb", 0.0)

    async def check_local_health(self, force: bool = False) -> bool:
        result = await self._mm.health_check()
        return result.get("status") == "healthy" if isinstance(result, dict) else bool(result)

    # --- Mode management ---

    def set_force_mode(self, mode: str) -> dict[str, Any]:
        mode_lower = mode.strip().lower()
        if mode_lower in ("cloud", "force_cloud"):
            config.FORCE_CLOUD = True
            self.force_mode = "force_cloud"
        elif mode_lower in ("local", "force_local"):
            config.FORCE_CLOUD = False
            self.force_mode = "force_local"
        else:
            config.FORCE_CLOUD = False
            self.force_mode = None
        return {"ok": True, "mode": self.force_mode or "auto", "force_cloud": config.FORCE_CLOUD}

    # --- Routing ---

    def get_last_route(self) -> dict[str, Any]:
        return self._last_route

    async def route_query(
        self,
        prompt: str,
        task_type: str = "chat",
        **kwargs: Any,
    ) -> str:
        """Совместимый web-query путь с уважением к force-mode и preferred model."""
        if not self.openclaw_client:
            return "OpenClaw client not configured"
        preferred_model = str(kwargs.get("preferred_model", "") or "").strip() or None
        effective_force_mode = str(self.force_mode or ("force_cloud" if config.FORCE_CLOUD else "auto"))
        effective_force_cloud = effective_force_mode == "force_cloud"
        if preferred_model:
            # Явный выбор модели в owner UI сильнее общего режима.
            # Иначе `preferred_model=google-gemini-cli/...` терялся, и запрос
            # всё равно уходил в default primary `openai-codex/gpt-5.4`.
            effective_force_cloud = not self._is_local_model(preferred_model)
        chunks = []
        async for chunk in self.openclaw_client.send_message_stream(
            prompt,
            chat_id="web_assistant",
            force_cloud=effective_force_cloud,
            preferred_model=preferred_model,
        ):
            chunks.append(chunk)
        self.active_tier = getattr(self.openclaw_client, "active_tier", self.active_tier)
        route_meta: dict[str, Any] = {}
        if hasattr(self.openclaw_client, "get_last_runtime_route"):
            try:
                route_meta = self.openclaw_client.get_last_runtime_route() or {}
            except Exception:  # noqa: BLE001
                route_meta = {}
        self._last_route = {
            "route_reason": str(route_meta.get("route_reason", "")).strip() or "unknown",
            "route_detail": str(route_meta.get("route_detail", "")).strip(),
            "channel": str(route_meta.get("channel", "")).strip() or "unknown",
            "provider": str(route_meta.get("provider", "")).strip() or "unknown",
            "model": str(route_meta.get("model", "")).strip() or str(config.MODEL),
            "status": str(route_meta.get("status", "")).strip() or "unknown",
            "error_code": route_meta.get("error_code"),
            "active_tier": str(route_meta.get("active_tier", self.active_tier)),
            "force_cloud": bool(route_meta.get("force_cloud", effective_force_cloud)),
            "timestamp": route_meta.get("timestamp"),
        }
        return "".join(chunks)

    # --- Explain / Preflight / Recommendation ---

    @staticmethod
    def _normalize_profile(profile: str | None) -> str:
        """Нормализует профиль задачи для рекомендаций и feedback."""
        return str(profile or "").strip().lower() or "chat"

    def _is_local_model(self, model_id: str | None) -> bool:
        """Безопасно определяет, относится ли модель к локальному рантайму."""
        candidate = str(model_id or "").strip()
        if not candidate:
            return False
        low = candidate.lower()
        if low.startswith(("lmstudio/", "local/")):
            return True
        if low.startswith(
            (
                "google/",
                "google-gemini-cli/",
                "google-antigravity/",
                "openai/",
                "openai-codex/",
                "openrouter/",
                "qwen-portal/",
                "anthropic/",
                "xai/",
                "deepseek/",
                "groq/",
            )
        ):
            return False
        if hasattr(self._mm, "is_local_model"):
            try:
                return bool(self._mm.is_local_model(candidate))
            except Exception:  # noqa: BLE001
                pass
        return not (low.startswith("google/") or low.startswith("openai/"))

    def _get_active_local_model(self) -> str:
        """Возвращает активную локальную модель по состоянию менеджера."""
        if hasattr(self._mm, "get_current_model"):
            try:
                current = str(self._mm.get_current_model() or "").strip()
                if current:
                    return current
            except Exception:  # noqa: BLE001
                pass
        return str(getattr(self._mm, "_current_model", "") or "").strip()

    def _get_preferred_local_model_hint(self) -> str:
        """Возвращает preferred local model из конфига как подсказку для UI."""
        preferred = str(getattr(config, "LOCAL_PREFERRED_MODEL", "") or "").strip()
        if preferred.lower() in {"", "auto", "smallest"}:
            return ""
        return preferred

    def _get_cloud_slot_model(self, profile: str) -> str:
        """Выбирает облачную модель-слот для профиля задачи."""
        normalized = self._normalize_profile(profile)
        for slot in (normalized, "reasoning", "coding", "review", "chat"):
            candidate = str(self.models.get(slot, "") or "").strip()
            if candidate and not self._is_local_model(candidate):
                return candidate
        return str(config.MODEL or "").strip()

    def _build_execution_plan(
        self,
        *,
        prompt: str = "",
        task_type: str = "chat",
        preferred_model: str | None = None,
        confirm_expensive: bool = False,
    ) -> dict[str, Any]:
        """
        Собирает лёгкий план выполнения для UI-compatible preflight/explain.

        Это не фактический runtime-route, а прогноз на основе текущей policy:
        force-mode, наличия локальной модели, выбранного профиля и preferred model.
        """
        profile = self.classify_task_profile(prompt, task_type)
        normalized_profile = self._normalize_profile(profile)
        preferred = str(preferred_model or "").strip()
        local_model = self._get_active_local_model()
        local_available = bool(local_model)
        local_target = local_model or self._get_preferred_local_model_hint()
        cloud_model = self._get_cloud_slot_model(normalized_profile)
        reasons: list[str] = []
        warnings: list[str] = []

        effective_force_mode = str(self.force_mode or ("force_cloud" if config.FORCE_CLOUD else "auto"))
        if preferred:
            model = preferred
            channel = "local" if self._is_local_model(preferred) else "cloud"
            reasons.append("Использована явно запрошенная модель.")
        elif effective_force_mode == "force_local":
            model = local_target
            channel = "local"
            reasons.append("Включён принудительный local-режим.")
        elif effective_force_mode == "force_cloud":
            model = cloud_model
            channel = "cloud"
            reasons.append("Включён принудительный cloud-режим.")
        elif normalized_profile in {"reasoning", "coding"} and cloud_model:
            model = cloud_model
            channel = "cloud"
            reasons.append("Для сложного профиля выбран облачный слот.")
        elif local_target:
            model = local_target
            channel = "local"
            reasons.append("Доступна локальная модель, применён local-first.")
        else:
            model = cloud_model
            channel = "cloud"
            reasons.append("Локальная модель недоступна, используем cloud fallback.")

        can_run_now = bool(model)
        if channel == "local" and not local_available:
            can_run_now = False
            warnings.append("Локальная модель сейчас не активна.")
            if local_target:
                reasons.append("В качестве цели для local-first используется preferred local model.")

        prompt_len = len(str(prompt or ""))
        critical = normalized_profile in {"reasoning", "coding", "review"} or prompt_len >= 1800
        estimated_cost = 0.0 if channel == "local" else (
            0.006 if normalized_profile == "chat" else 0.015 if normalized_profile == "review" else 0.03
        )
        requires_confirm_expensive = bool(
            channel == "cloud"
            and critical
            and estimated_cost >= 0.015
            and not confirm_expensive
        )
        if requires_confirm_expensive:
            warnings.append("Запрос выглядит дорогим для cloud-маршрута и требует подтверждения.")

        if preferred and self._is_local_model(preferred) and local_model and preferred != local_model:
            reasons.append("Предпочтение задано локально, но фактически активна другая локальная модель.")

        if preferred and not self._is_local_model(preferred) and channel == "cloud":
            reasons.append("Предпочтение закреплено за облачной моделью.")

        if channel == "cloud" and self.cloud_soft_cap_reached:
            warnings.append("Cloud soft cap уже достигнут; возможны ограничения по бюджету.")

        next_step = "Можно запускать задачу."
        if not can_run_now and channel == "local":
            next_step = "Сначала загрузить локальную модель или переключиться в auto/cloud."
        elif requires_confirm_expensive:
            next_step = "Подтвердить дорогой cloud-запуск и повторить preflight."

        return {
            "task_type": str(task_type or "chat").strip().lower() or "chat",
            "profile": normalized_profile,
            "critical": critical,
            "execution": {
                "channel": channel,
                "model": model or "",
                "can_run_now": can_run_now,
                "requires_confirm_expensive": requires_confirm_expensive,
                "force_mode": effective_force_mode,
            },
            "reasons": reasons,
            "warnings": warnings,
            "cost_hint": {
                "marginal_call_cost_usd": estimated_cost,
                "billing_mode": "local" if channel == "local" else "cloud_estimate",
            },
            "next_step": next_step,
            "local_available": local_available,
        }

    def get_route_explain(self, **kwargs: Any) -> dict[str, Any]:
        """
        Возвращает explainability-снимок для UI и отладки routing policy.

        Здесь важно показать одновременно:
        - последний фактический runtime-route;
        - текущую policy/force-mode;
        - прогноз preflight для следующего запуска.
        """
        prompt = str(kwargs.get("prompt", "") or "").strip()
        task_type = str(kwargs.get("task_type", "chat") or "chat").strip().lower() or "chat"
        preferred_model = str(kwargs.get("preferred_model", "") or "").strip() or None
        confirm_expensive = bool(kwargs.get("confirm_expensive", False))
        preflight = self._build_execution_plan(
            prompt=prompt,
            task_type=task_type,
            preferred_model=preferred_model,
            confirm_expensive=confirm_expensive,
        )
        last_route = self.get_last_route()
        reason_code = str(last_route.get("route_reason", "") or "").strip() or "preflight_only"
        reason_detail = str(last_route.get("route_detail", "") or "").strip()
        if not reason_detail:
            if preflight["warnings"]:
                reason_detail = str(preflight["warnings"][0])
            elif preflight["reasons"]:
                reason_detail = str(preflight["reasons"][0])
            else:
                reason_detail = "Роутинг будет выбран во время фактического выполнения."
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "last_route": last_route,
            "reason": {
                "code": reason_code,
                "detail": reason_detail,
                "human": "Показан последний runtime-route и прогноз следующего запуска.",
            },
            "policy": {
                "force_mode": str(self.force_mode or ("force_cloud" if config.FORCE_CLOUD else "auto")),
                "routing_policy": "local_first_web_compat",
                "cloud_soft_cap_reached": bool(self.cloud_soft_cap_reached),
                "local_available": bool(self._get_active_local_model()),
            },
            "recommendation": self.get_profile_recommendation(preflight["profile"]),
            "preflight": preflight,
        }

    def get_task_preflight(self, **kwargs: Any) -> dict[str, Any]:
        """Возвращает preflight-план в формате, который уже ожидает web UI."""
        return self._build_execution_plan(
            prompt=str(kwargs.get("prompt", "") or "").strip(),
            task_type=str(kwargs.get("task_type", "chat") or "chat").strip().lower() or "chat",
            preferred_model=str(kwargs.get("preferred_model", "") or "").strip() or None,
            confirm_expensive=bool(kwargs.get("confirm_expensive", False)),
        )

    def get_profile_recommendation(self, profile: str = "general") -> dict[str, Any]:
        """
        Возвращает совместимую рекомендацию для карточки Routing.

        UI использует поле `model`, а старые клиенты — `recommended_model`,
        поэтому держим оба.
        """
        normalized_profile = self._normalize_profile(profile)
        plan = self._build_execution_plan(task_type=normalized_profile)
        model = str(plan["execution"]["model"] or config.MODEL).strip()
        channel = str(plan["execution"]["channel"] or "auto").strip()
        reasoning = "; ".join(plan["reasons"]) if plan["reasons"] else "Используется текущая routing policy."
        return {
            "profile": normalized_profile,
            "model": model,
            "recommended_model": model,
            "channel": channel,
            "reasoning": reasoning,
            "local_available": bool(self._get_active_local_model()),
            "force_mode": str(self.force_mode or ("force_cloud" if config.FORCE_CLOUD else "auto")),
        }

    def classify_task_profile(self, prompt: str, task_type: str = "chat") -> str:
        return task_type

    # --- Feedback (совместимый in-memory агрегат для web UI) ---

    @staticmethod
    def _round_score(value: float) -> float:
        """Округляет score для UI без лишнего хвоста в JSON."""
        return round(float(value), 2)

    def _feedback_entries(self, profile: str | None = None) -> list[dict[str, Any]]:
        """Возвращает feedback-элементы с optional фильтром по профилю."""
        normalized_profile = self._normalize_profile(profile) if profile else ""
        if not normalized_profile:
            return list(self._feedback)
        return [
            entry
            for entry in self._feedback
            if self._normalize_profile(entry.get("profile")) == normalized_profile
        ]

    def get_feedback_summary(self, profile: str = "", top: int = 5) -> dict[str, Any]:
        """
        Возвращает агрегированный feedback-контракт для web UI.

        Поля `top_models` и `top_channels` нужны карточкам панели и были
        потеряны после перехода на compat-роутер.
        """
        filtered = self._feedback_entries(profile)
        top_limit = max(1, int(top))

        model_buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
        channel_buckets: dict[str, list[int]] = defaultdict(list)
        for entry in filtered:
            entry_profile = self._normalize_profile(entry.get("profile"))
            entry_model = str(entry.get("model") or "").strip() or str(config.MODEL or "").strip()
            entry_channel = str(entry.get("channel") or "auto").strip().lower() or "auto"
            score = int(entry.get("score") or 0)
            model_buckets[(entry_profile, entry_model)].append(score)
            channel_buckets[entry_channel].append(score)

        top_models = [
            {
                "profile": item_profile,
                "model": item_model,
                "avg_score": self._round_score(sum(scores) / len(scores)),
                "count": len(scores),
            }
            for (item_profile, item_model), scores in model_buckets.items()
        ]
        top_models.sort(key=lambda item: (-float(item["avg_score"]), -int(item["count"]), str(item["model"])))

        top_channels = [
            {
                "channel": channel,
                "avg_score": self._round_score(sum(scores) / len(scores)),
                "count": len(scores),
            }
            for channel, scores in channel_buckets.items()
        ]
        top_channels.sort(key=lambda item: (-float(item["avg_score"]), -int(item["count"]), str(item["channel"])))

        recent_entries = [
            {
                "ts": str(entry.get("ts") or ""),
                "profile": self._normalize_profile(entry.get("profile")),
                "model": str(entry.get("model") or ""),
                "channel": str(entry.get("channel") or "auto"),
                "score": int(entry.get("score") or 0),
                "note": str(entry.get("note") or ""),
            }
            for entry in filtered[-top_limit:]
        ]

        return {
            "profile": self._normalize_profile(profile) if profile else "all",
            "total_feedback": len(filtered),
            "top_models": top_models[:top_limit],
            "top_channels": top_channels[:top_limit],
            "entries": recent_entries,
        }

    def submit_feedback(self, **kwargs: Any) -> dict[str, Any]:
        """
        Сохраняет feedback и сразу возвращает агрегат по profile/model.

        Это позволяет UI мгновенно показать новый средний score без отдельного
        повторного запроса статистики.
        """
        score = int(kwargs.get("score") or 0)
        if score < 1 or score > 5:
            raise ValueError("score_must_be_between_1_and_5")

        profile = self._normalize_profile(kwargs.get("profile"))
        model_name = str(kwargs.get("model_name") or "").strip()
        if not model_name:
            model_name = str(self._last_route.get("model") or "").strip()
        if not model_name:
            model_name = self.get_profile_recommendation(profile).get("model", "") or str(config.MODEL or "")

        channel = str(kwargs.get("channel") or "").strip().lower()
        if not channel:
            channel = str(self._last_route.get("channel") or "").strip().lower()
        if not channel:
            channel = str(self.get_profile_recommendation(profile).get("channel") or "auto").strip().lower()

        entry = {
            "score": score,
            "profile": profile,
            "model": model_name,
            "channel": channel or "auto",
            "note": str(kwargs.get("note") or "").strip(),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self._feedback.append(entry)
        if len(self._feedback) > 100:
            self._feedback = self._feedback[-100:]

        relevant = [
            item for item in self._feedback
            if self._normalize_profile(item.get("profile")) == profile
            and str(item.get("model") or "").strip() == model_name
        ]
        relevant_scores = [int(item.get("score") or 0) for item in relevant] or [score]
        return {
            "ok": True,
            "score": score,
            "profile": profile,
            "model": model_name,
            "channel": channel or "auto",
            "profile_model_stats": {
                "avg": self._round_score(sum(relevant_scores) / len(relevant_scores)),
                "count": len(relevant_scores),
                "min": min(relevant_scores),
                "max": max(relevant_scores),
            },
            "total_feedback": len(self._feedback),
        }

    # --- Cost / Usage / Ops (delegate to cost_analytics where possible) ---

    def get_usage_summary(self) -> dict[str, Any]:
        if self.openclaw_client:
            return self.openclaw_client.get_usage_stats()
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def _build_cost_snapshot(self) -> dict[str, Any]:
        """
        Собирает совместимый cost snapshot.

        Приоритет:
        1) нативный `build_usage_report_dict()` у cost_analytics;
        2) аккуратный fallback из usage summary, если аналитика ещё не пишет calls.
        """
        ca = getattr(self._mm, "cost_analytics", None)
        if ca and hasattr(ca, "build_usage_report_dict"):
            try:
                snapshot = dict(ca.build_usage_report_dict())
                snapshot["source"] = "cost_analytics"
                return snapshot
            except Exception:  # noqa: BLE001
                logger.warning("web_router_cost_snapshot_failed")

        usage = self.get_usage_summary()
        return {
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
            "cost_session_usd": 0.0,
            "cost_month_usd": 0.0,
            "monthly_budget_usd": None,
            "remaining_budget_usd": None,
            "budget_ok": True,
            "monthly_calls_forecast": None,
            "by_model": {},
            "source": "usage_summary_fallback",
        }

    @staticmethod
    def _sum_report_calls(by_model: dict[str, Any]) -> int:
        """Суммирует известные вызовы по отчёту аналитики."""
        total_calls = 0
        for payload in by_model.values():
            if isinstance(payload, dict):
                total_calls += int(payload.get("calls", 0) or 0)
        return total_calls

    def get_cost_report(self, **kwargs: Any) -> dict[str, Any]:
        """
        Возвращает честный cost-report для ops API.

        Важно:
        - даже без исторических calls endpoint должен оставаться полезным;
        - вместо `not_configured` показываем нулевой/пустой срез и источник данных.
        """
        monthly_calls_forecast = int(kwargs.get("monthly_calls_forecast", 5000) or 0)
        snapshot = self._build_cost_snapshot()
        known_calls = self._sum_report_calls(snapshot.get("by_model", {}))
        has_usage = bool(snapshot.get("total_tokens", 0) or known_calls or snapshot.get("cost_session_usd", 0.0))
        status = "ok" if has_usage else "no_usage_yet"
        return {
            "status": status,
            "source": snapshot.get("source", "unknown"),
            "usage": {
                "input_tokens": int(snapshot.get("input_tokens", 0) or 0),
                "output_tokens": int(snapshot.get("output_tokens", 0) or 0),
                "total_tokens": int(snapshot.get("total_tokens", 0) or 0),
                "tracked_calls": known_calls,
            },
            "costs": {
                "session_usd": round(float(snapshot.get("cost_session_usd", 0.0) or 0.0), 6),
                "month_usd": round(float(snapshot.get("cost_month_usd", 0.0) or 0.0), 6),
                "monthly_budget_usd": snapshot.get("monthly_budget_usd"),
                "remaining_budget_usd": snapshot.get("remaining_budget_usd"),
                "budget_ok": bool(snapshot.get("budget_ok", True)),
            },
            "forecast": {
                "monthly_calls_forecast": snapshot.get("monthly_calls_forecast") or monthly_calls_forecast,
            },
            "by_model": snapshot.get("by_model", {}),
        }

    def get_credit_runway_report(self, **kwargs: Any) -> dict[str, Any]:
        """
        Возвращает честный runway-report вместо фиктивного `999 days`.

        Если cost usage ещё не накоплен, API явно сообщает `no_usage_yet`.
        """
        credits_usd = float(kwargs.get("credits_usd", 300.0) or 0.0)
        horizon_days = max(1, int(kwargs.get("horizon_days", 80) or 80))
        reserve_ratio = min(max(float(kwargs.get("reserve_ratio", 0.1) or 0.0), 0.0), 0.95)
        snapshot = self._build_cost_snapshot()

        cost_month_usd = float(snapshot.get("cost_month_usd", 0.0) or 0.0)
        spendable_budget = max(0.0, credits_usd * (1.0 - reserve_ratio))
        daily_burn_usd = cost_month_usd / 30.0 if cost_month_usd > 0 else 0.0
        runway_days = (spendable_budget / daily_burn_usd) if daily_burn_usd > 0 else None

        known_calls = self._sum_report_calls(snapshot.get("by_model", {}))
        avg_cost_per_call = (cost_month_usd / known_calls) if cost_month_usd > 0 and known_calls > 0 else 0.0
        safe_calls_per_day = (
            math.floor((spendable_budget / horizon_days) / avg_cost_per_call)
            if avg_cost_per_call > 0 and horizon_days > 0
            else None
        )

        if daily_burn_usd <= 0:
            status = "no_usage_yet"
        elif runway_days is not None and runway_days < max(7, horizon_days / 2):
            status = "warning"
        else:
            status = "ok"

        return {
            "status": status,
            "credits_usd": round(credits_usd, 2),
            "reserve_ratio": reserve_ratio,
            "spendable_budget_usd": round(spendable_budget, 2),
            "monthly_cost_usd": round(cost_month_usd, 6),
            "daily_burn_usd": round(daily_burn_usd, 6),
            "runway_days": round(runway_days, 2) if runway_days is not None else None,
            "horizon_days": horizon_days,
            "horizon_ok": (runway_days is None) or runway_days >= horizon_days,
            "safe_calls_per_day": safe_calls_per_day,
            "avg_cost_per_call_usd": round(avg_cost_per_call, 6) if avg_cost_per_call > 0 else None,
        }

    def get_ops_executive_summary(self, **kwargs: Any) -> dict[str, Any]:
        """
        Компактный executive summary для ops API и экспортов.
        """
        monthly_calls_forecast = int(kwargs.get("monthly_calls_forecast", 5000) or 0)
        recommend = self.get_profile_recommendation("chat")
        cost_report = self.get_cost_report(monthly_calls_forecast=monthly_calls_forecast)
        runway = self.get_credit_runway_report(
            credits_usd=float(kwargs.get("credits_usd", 300.0) or 300.0),
            horizon_days=int(kwargs.get("horizon_days", 80) or 80),
            reserve_ratio=float(kwargs.get("reserve_ratio", 0.1) or 0.1),
            monthly_calls_forecast=monthly_calls_forecast,
        )
        risks: list[str] = []
        recommendations: list[str] = []

        if not recommend.get("local_available") and recommend.get("channel") == "local":
            risks.append("local_preferred_not_loaded")
            recommendations.append("Загрузить preferred local model перед heavy local-first сценарием.")
        if runway.get("status") == "warning":
            risks.append("budget_runway_low")
            recommendations.append("Снизить cloud-share или пополнить баланс.")
        if cost_report["status"] == "no_usage_yet":
            recommendations.append("Пока нет cost usage; executive summary построен по runtime policy.")

        return {
            "status": "ok" if not risks else "attention",
            "model": str(recommend.get("model") or config.MODEL),
            "channel": str(recommend.get("channel") or "auto"),
            "force_cloud": config.FORCE_CLOUD,
            "force_mode": str(self.force_mode or ("force_cloud" if config.FORCE_CLOUD else "auto")),
            "active_tier": getattr(self.openclaw_client, "active_tier", self.active_tier),
            "ram": self._mm.get_ram_usage(),
            "budget": {
                "cost_report_status": cost_report["status"],
                "month_usd": cost_report["costs"]["month_usd"],
                "remaining_budget_usd": cost_report["costs"]["remaining_budget_usd"],
                "budget_ok": cost_report["costs"]["budget_ok"],
                "runway_status": runway["status"],
                "runway_days": runway["runway_days"],
            },
            "risks": risks,
            "recommendations": recommendations,
        }

    def get_ops_report(self, **kwargs: Any) -> dict[str, Any]:
        """
        Единый ops report для API/export.
        """
        monthly_calls_forecast = int(kwargs.get("monthly_calls_forecast", 5000) or 0)
        history_limit = int(kwargs.get("history_limit", 20) or 20)
        return {
            "status": "ok",
            "model": self.get_profile_recommendation("chat").get("model", config.MODEL),
            "ram": self._mm.get_ram_usage(),
            "usage": self.get_usage_summary(),
            "cost_report": self.get_cost_report(monthly_calls_forecast=monthly_calls_forecast),
            "runway": self.get_credit_runway_report(monthly_calls_forecast=monthly_calls_forecast),
            "executive_summary": self.get_ops_executive_summary(monthly_calls_forecast=monthly_calls_forecast),
            "active_tier": getattr(self.openclaw_client, "active_tier", self.active_tier),
            "last_route": self.get_last_route(),
            "history": self.get_ops_history(limit=history_limit),
            "alerts": self.get_ops_alerts(),
        }

    def get_ops_alerts(self) -> list[dict[str, Any]]:
        return []

    def get_ops_history(self, limit: int = 20) -> list[dict[str, Any]]:
        return []

    def prune_ops_history(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "pruned": 0}

    def acknowledge_ops_alert(self, code: str, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "code": code}

    def clear_ops_alert_ack(self, code: str) -> dict[str, Any]:
        return {"ok": True, "code": code}

    # --- Model listing ---

    async def list_local_models_verbose(self) -> list[dict[str, Any]]:
        """
        Возвращает truth-список локальных chat/VLM-моделей для owner UI.

        Почему не опираемся только на `_models_cache`:
        - после переподключения диска и рескана LM Studio живой API уже знает
          про модели, а старый кэш внутри Python-процесса может быть пустым
          или содержать только legacy/bundled записи;
        - веб-панель должна видеть тот же каталог, который реально показывает
          LM Studio API, иначе owner получает ложную картину доступной локали.
        """
        models: list[dict[str, Any]] = []
        loaded_ids = {
            str(item).strip()
            for item in await self._mm.get_loaded_models(force_refresh=True)
            if str(item or "").strip()
        }

        local_infos: dict[str, ModelInfo] = {}
        try:
            model_list = await fetch_lm_studio_models_list(
                self._mm.lm_studio_url,
                client=self._mm._http_client,
            )
        except Exception:  # noqa: BLE001
            model_list = []

        for model_data in model_list:
            model_id = str(model_data.get("id", "")).strip()
            if not model_id:
                continue
            cached = self._mm._models_cache.get(model_id)
            model_type = getattr(cached, "type", None)
            if model_type not in (ModelType.LOCAL_MLX, ModelType.LOCAL_GGUF):
                low_id = model_id.lower()
                model_type = ModelType.LOCAL_GGUF if "gguf" in low_id else ModelType.LOCAL_MLX
            local_infos[model_id] = ModelInfo(
                id=model_id,
                name=str(model_data.get("name", model_id)),
                type=model_type,
                size_gb=float(model_data.get("size_gb") or getattr(cached, "size_gb", 0.0) or 0.0),
                supports_vision=bool(
                    model_data.get("vision", False) or getattr(cached, "supports_vision", False)
                ),
            )

        if not local_infos:
            if not self._mm._models_cache:
                await self._mm.discover_models()
            local_infos = {
                mid: info
                for mid, info in self._mm._models_cache.items()
                if info.type in (ModelType.LOCAL_MLX, ModelType.LOCAL_GGUF)
            }

        for mid, info in sorted(local_infos.items(), key=lambda item: item[0].lower()):
            if not self._mm._is_chat_capable_local_model(mid, info):
                continue
            models.append(
                {
                    "id": mid,
                    "loaded": mid == self._mm._current_model or mid in loaded_ids,
                    "type": info.type.value if hasattr(info.type, "value") else str(info.type),
                    "size_human": f"{info.size_gb:.1f} GB" if float(info.size_gb or 0.0) > 0 else "n/a",
                    "vision": bool(getattr(info, "supports_vision", False)),
                }
            )
        return models

    # --- Health check (for ecosystem_health adapter) ---

    async def health_check(self) -> dict[str, Any]:
        return await self._mm.health_check()
