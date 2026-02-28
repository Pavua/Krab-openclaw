# -*- coding: utf-8 -*-
"""
Compatibility adapter: bridges the old NexusRouter API (used by web_app.py)
to the new decomposed architecture (ModelManager, OpenClawClient, CostAnalytics).

Methods that have real backing implementations delegate to the new singletons.
Methods that no longer have backing code return sensible defaults so the web panel
doesn't crash — it just shows "not configured" in the UI.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

from ..config import config
from ..core.local_health import is_lm_studio_available

logger = structlog.get_logger(__name__)


class WebRouterCompat:
    """
    Drop-in replacement for the old NexusRouter, used as deps["router"] in WebApp.
    Wraps ModelManager and OpenClawClient singletons.
    """

    def __init__(self, model_manager: Any, openclaw_client: Any):
        self._mm = model_manager
        self.openclaw_client = openclaw_client
        self._stats: dict[str, int] = {"local_failures": 0, "cloud_failures": 0}
        self._last_route: dict[str, Any] = {}
        self._feedback: list[dict[str, Any]] = []
        self.force_mode: Optional[str] = None
        self.rag = None
        self.models: dict[str, str] = {"chat": config.MODEL}

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

    # --- Model info ---

    def get_model_info(self) -> dict[str, Any]:
        return {
            "current_model": config.MODEL,
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
        """Simplified query routing via OpenClaw."""
        if not self.openclaw_client:
            return "OpenClaw client not configured"
        chunks = []
        async for chunk in self.openclaw_client.send_message_stream(
            prompt, chat_id="web_assistant", force_cloud=config.FORCE_CLOUD
        ):
            chunks.append(chunk)
        return "".join(chunks)

    # --- Explain / Preflight / Recommendation ---

    def get_route_explain(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "model": config.MODEL,
            "force_cloud": config.FORCE_CLOUD,
            "explanation": "Routing via OpenClaw gateway",
        }

    def get_task_preflight(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "model": config.MODEL,
            "ready": True,
            "checks": {"openclaw": True, "model_configured": bool(config.MODEL)},
        }

    def get_profile_recommendation(self, profile: str = "general") -> dict[str, Any]:
        return {
            "profile": profile,
            "recommended_model": config.MODEL,
            "reasoning": "Using configured model via OpenClaw",
        }

    def classify_task_profile(self, prompt: str, task_type: str = "chat") -> str:
        return task_type

    # --- Feedback (in-memory stub) ---

    def get_feedback_summary(self, profile: str = "", top: int = 5) -> dict[str, Any]:
        return {"total": len(self._feedback), "entries": self._feedback[-top:]}

    def submit_feedback(self, **kwargs: Any) -> dict[str, Any]:
        entry = {**kwargs, "ts": datetime.now(timezone.utc).isoformat()}
        self._feedback.append(entry)
        if len(self._feedback) > 100:
            self._feedback = self._feedback[-100:]
        return {"ok": True, "total": len(self._feedback)}

    # --- Cost / Usage / Ops (delegate to cost_analytics where possible) ---

    def get_usage_summary(self) -> dict[str, Any]:
        if self.openclaw_client:
            return self.openclaw_client.get_usage_stats()
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def get_cost_report(self, **kwargs: Any) -> dict[str, Any]:
        ca = self._mm.cost_analytics
        if ca and hasattr(ca, "get_report"):
            return ca.get_report(**kwargs)
        return {"status": "not_configured", "total_cost": 0.0}

    def get_credit_runway_report(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "not_configured", "runway_days": 999}

    def get_ops_executive_summary(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "model": config.MODEL,
            "force_cloud": config.FORCE_CLOUD,
            "ram": self._mm.get_ram_usage(),
        }

    def get_ops_report(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "model": config.MODEL,
            "ram": self._mm.get_ram_usage(),
            "usage": self.get_usage_summary(),
            "alerts": [],
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
        """Lists local models with metadata for the web panel catalog."""
        models = []
        if not self._mm._models_cache:
            await self._mm.discover_models()
        from ..core.model_types import ModelType
        for mid, info in self._mm._models_cache.items():
            if info.type in (ModelType.LOCAL_MLX, ModelType.LOCAL_GGUF):
                models.append({
                    "id": mid,
                    "loaded": mid == self._mm._current_model,
                    "type": info.type.value if hasattr(info.type, "value") else str(info.type),
                    "size_human": f"{info.size_gb:.1f} GB" if info.size_gb > 0 else "n/a",
                })
        return models

    # --- Health check (for ecosystem_health adapter) ---

    async def health_check(self) -> dict[str, Any]:
        return await self._mm.health_check()
