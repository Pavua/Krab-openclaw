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
import json
import aiohttp
from pathlib import Path
import re
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Literal, Optional, Dict, Any, List, Set
# from src.core.rag_engine import RAGEngine # Deprecated

# Настройка логгера
import structlog
logger = structlog.get_logger("ModelRouter")

from src.core.openclaw_client import OpenClawClient
from src.core.agent_swarm import SwarmManager

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

        # OpenClaw Client (Cloud Model Gateway)
        self.openclaw_client = OpenClawClient(
            base_url=config.get("OPENCLAW_BASE_URL", "http://localhost:18789"),
            api_key=config.get("OPENCLAW_API_KEY")
        )
        logger.info("☁️ OpenClaw Client configured for Cloud Models")

        # RAG Engine (Deprecated, use OpenClaw)
        self.rag = None # RAGEngine()

        # Persona Manager (назначается в main.py)
        self.persona = None
        self.tools = None  # Назначается в main.py (ToolHandler)

        # Agent Swarm Manager
        self.swarm = SwarmManager(model_router=self)

        # Пул моделей — читаем из .env, дефолты как fallback
        self.models = {
            "chat": config.get("GEMINI_CHAT_MODEL", "google/gemini-1.5-flash"),
            "thinking": config.get("GEMINI_THINKING_MODEL", "google/gemini-1.5-pro"),
            "pro": config.get("GEMINI_PRO_MODEL", "google/gemini-1.5-pro"),
            "coding": config.get("GEMINI_CODING_MODEL", "google/gemini-1.5-flash"),
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
            "gemini-2.0-flash",         # Если основной занят
            "gemini-2.0-flash-001",     # Стабильная версия
            "gemini-flash-latest",      # Алиас на актуальную flash
            "gemini-pro-latest"         # Алиас на актуальную pro
        ]
        
        # Режим работы: 'auto', 'force_local', 'force_cloud'
        self.force_mode = "auto"

        # Политика роутинга (Phase D): free-first hybrid.
        self.routing_policy = str(config.get("MODEL_ROUTING_POLICY", "free_first_hybrid")).strip().lower()
        self.require_confirm_expensive = str(config.get("MODEL_REQUIRE_CONFIRM_EXPENSIVE", "0")).strip().lower() in {
            "1", "true", "yes", "on"
        }
        self.enable_cloud_review_for_critical = str(
            config.get("MODEL_ENABLE_CLOUD_REVIEW_CRITICAL", "0")
        ).strip().lower() in {"1", "true", "yes", "on"}

        # Ограничение расходов в вызовах (бюджетный guardrail без привязки к провайдеру).
        try:
            self.cloud_soft_cap_calls = int(config.get("CLOUD_SOFT_CAP_CALLS", 10000))
        except Exception:
            self.cloud_soft_cap_calls = 10000
        self.cloud_soft_cap_reached = False
        try:
            self.cloud_cost_per_call_usd = float(config.get("CLOUD_COST_PER_CALL_USD", 0.01))
        except Exception:
            self.cloud_cost_per_call_usd = 0.01
        try:
            self.local_cost_per_call_usd = float(config.get("LOCAL_COST_PER_CALL_USD", 0.0))
        except Exception:
            self.local_cost_per_call_usd = 0.0
        try:
            self.cloud_monthly_budget_usd = float(config.get("CLOUD_MONTHLY_BUDGET_USD", 25.0))
        except Exception:
            self.cloud_monthly_budget_usd = 25.0
        try:
            self.monthly_calls_forecast = int(config.get("MONTHLY_CALLS_FORECAST", 5000))
        except Exception:
            self.monthly_calls_forecast = 5000

        # Политика локального параллелизма: 1 heavy + 1 light.
        self._local_heavy_slot = asyncio.Semaphore(1)
        self._local_light_slot = asyncio.Semaphore(1)

        self.local_timeout_seconds = float(config.get("LOCAL_CHAT_TIMEOUT_SECONDS", 300))
        self.last_cloud_error: Optional[str] = None
        self.last_cloud_model: Optional[str] = None
        self.cloud_priority_models = self._parse_cloud_priority(config.get(
            "MODEL_CLOUD_PRIORITY_LIST",
            "google/gemini-2.0-flash,google/gemini-2.0-flash-lite-preview-02-05,openai/gpt-4o-mini,openai/gpt-4o-mini-standalone,wormgpt-1.0,kimi/k2-llama-mix"
        ))

        # Память предпочтений моделей по профилям задач.
        self._routing_memory_path = Path(
            config.get("MODEL_ROUTING_MEMORY_PATH", "artifacts/model_routing_memory.json")
        )
        self._usage_report_path = Path(
            config.get("MODEL_USAGE_REPORT_PATH", "artifacts/model_usage_report.json")
        )
        self._routing_memory = self._load_json(self._routing_memory_path, default={})
        self._usage_report = self._load_json(
            self._usage_report_path,
            default={"profiles": {}, "models": {}, "channels": {"local": 0, "cloud": 0}},
        )
        self._ops_state_path = Path(
            config.get("MODEL_OPS_STATE_PATH", "artifacts/model_ops_state.json")
        )
        self._ops_state = self._load_json(
            self._ops_state_path,
            default={"acknowledged": {}, "history": []},
        )
        if not isinstance(self._ops_state.get("acknowledged"), dict):
            self._ops_state["acknowledged"] = {}
        if not isinstance(self._ops_state.get("history"), list):
            self._ops_state["history"] = []

        # Контур обратной связи по качеству (1-5) для самообучающегося роутинга.
        self._feedback_path = Path(
            config.get("MODEL_FEEDBACK_PATH", "artifacts/model_feedback.json")
        )
        self._feedback_store = self._load_json(
            self._feedback_path,
            default={"profiles": {}, "events": [], "last_route": {}, "updated_at": None},
        )
        if not isinstance(self._feedback_store.get("profiles"), dict):
            self._feedback_store["profiles"] = {}
        if not isinstance(self._feedback_store.get("events"), list):
            self._feedback_store["events"] = []
        if not isinstance(self._feedback_store.get("last_route"), dict):
            self._feedback_store["last_route"] = {}

        existing_cloud_calls = int(self._usage_report.get("channels", {}).get("cloud", 0))
        if existing_cloud_calls >= self.cloud_soft_cap_calls:
            self.cloud_soft_cap_reached = True
            logger.warning(f"Cloud Soft Cap reached at startup ({existing_cloud_calls}/{self.cloud_soft_cap_calls})")
        else:
            self.cloud_soft_cap_reached = False
            logger.info(f"Cloud Soft Cap status: {existing_cloud_calls}/{self.cloud_soft_cap_calls} ok")

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

    def _load_json(self, path: Path, default: dict) -> dict:
        """Безопасная загрузка JSON-файла."""
        try:
            if not path.exists():
                return default
            with path.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
                return data if isinstance(data, dict) else default
        except Exception:
            return default

    def _save_json(self, path: Path, payload: dict) -> None:
        """Безопасная запись JSON-файла."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("Не удалось сохранить JSON метрики роутера", path=str(path), error=str(exc))

    def _parse_cloud_priority(self, raw: Optional[str]) -> List[str]:
        """
        Разбирает список моделей из строки конфигурации и убирает дубли.
        """
        if not raw:
            return []
        result: list[str] = []
        seen: Set[str] = set()
        for token in str(raw).split(","):
            token = token.strip()
            if not token or token in seen:
                continue
            seen.add(token)
            result.append(token)
        return result

    def _lm_studio_api_root(self) -> str:
        """
        Возвращает базовый адрес LM Studio без суффикса /v1 или /api/v1.
        Это позволяет строить разные REST-пути через один корень.
        """
        root = self.lm_studio_url.rstrip("/")
        for suffix in ("/api/v1", "/v1"):
            if root.endswith(suffix):
                root = root[: -len(suffix)]
                break
        return root.rstrip("/")

    def _normalize_model_entries(self, payload: Any) -> List[Dict[str, Any]]:
        """
        Приводит ответ LM Studio / OpenClaw к списку словарей с моделями.
        """
        entries: List[Dict[str, Any]] = []
        candidate = []
        if isinstance(payload, dict):
            if isinstance(payload.get("models"), list):
                candidate = payload["models"]
            elif isinstance(payload.get("data"), list):
                candidate = payload["data"]
            elif isinstance(payload.get("result"), list):
                candidate = payload["result"]
            else:
                candidate = []
        elif isinstance(payload, list):
            candidate = payload
        else:
            candidate = []

        for item in candidate:
            if isinstance(item, dict):
                entries.append(item)
            else:
                entries.append({"id": str(item)})
        return entries

    def _extract_model_id(self, entry: Dict[str, Any]) -> Optional[str]:
        """
        Извлекает читаемый идентификатор модели из записи LM Studio.
        """
        for key in ("id", "key", "modelId", "identifier", "name"):
            value = entry.get(key)
            if value:
                return str(value)
        return None

    def _is_cloud_error_message(self, text: Optional[str]) -> bool:
        """
        Определяет, является ли ответ OpenClaw явной ошибкой.
        """
        if not text:
            return True
        lowered = text.strip().lower()
        return lowered.startswith("❌") or lowered.startswith("⚠️")

    def _is_cloud_billing_error(self, text: str) -> bool:
        """
        Обнаруживает billing-ошибки по ключевым словам.
        Исключает ложные срабатывания на Rate Limit (quota exceeded).
        """
        lowered = text.lower()
        
        # Если есть упоминание rate limit или 429 — это НЕ ошибка биллинга, а перегрузка
        if "rate limit" in lowered or "429" in lowered:
            return False

        billing_keywords = [
            "billing error",
            "out of credits",
            "insufficient balance",
            "insufficient funds",
            "billing",
            "credit balance",
        ]
        
        # 'quota' часто используется и для биллинга и для рейт-лимитов. 
        # Считаем за биллинг только если НЕТ упоминания rate limit.
        if "quota" in lowered and "rate" not in lowered:
             return True

        return any(keyword in lowered for keyword in billing_keywords)

    def _mark_cloud_soft_cap_if_needed(self, error_text: str) -> None:
        """
        При billing-ошибке пишет в лог, но НЕ блокирует облако, 
        так как мы доверяем ключу пользователя.
        """
        if self._is_cloud_billing_error(error_text):
            logger.warning("Cloud warning (billing-related): %s. Продолжаем попытки.", error_text)
            # self.cloud_soft_cap_reached = True  <-- Блокировка отключена

    def _ensure_feedback_store(self) -> dict:
        """Приводит feedback store к ожидаемой структуре."""
        if not isinstance(self._feedback_store, dict):
            self._feedback_store = {"profiles": {}, "events": [], "last_route": {}, "updated_at": None}
        if not isinstance(self._feedback_store.get("profiles"), dict):
            self._feedback_store["profiles"] = {}
        if not isinstance(self._feedback_store.get("events"), list):
            self._feedback_store["events"] = []
        if not isinstance(self._feedback_store.get("last_route"), dict):
            self._feedback_store["last_route"] = {}
        return self._feedback_store

    def _normalize_channel(self, channel: Optional[str]) -> str:
        """Нормализует имя канала маршрутизации."""
        lowered = str(channel or "").strip().lower()
        if lowered in {"local", "cloud"}:
            return lowered
        return "local"

    def _remember_last_route(
        self,
        profile: str,
        task_type: str,
        channel: str,
        model_name: str,
        prompt: str = "",
    ) -> None:
        """
        Сохраняет метаданные последнего успешного прогона,
        чтобы владелец мог оценить результат без ручного ввода profile/model.
        """
        store = self._ensure_feedback_store()
        route = {
            "ts": self._now_iso(),
            "profile": (profile or "chat").strip().lower() or "chat",
            "task_type": (task_type or "chat").strip().lower() or "chat",
            "channel": self._normalize_channel(channel),
            "model": (model_name or "unknown").strip() or "unknown",
            "prompt_preview": (prompt or "").strip()[:160],
        }
        store["last_route"] = route
        history = store.setdefault("route_history", [])
        if not isinstance(history, list):
            history = []
            store["route_history"] = history
        history.append(route)
        if len(history) > 60:
            del history[: len(history) - 60]
        store["updated_at"] = self._now_iso()
        self._save_json(self._feedback_path, store)

    def _get_model_feedback_stats(self, profile: str, model_name: str) -> dict:
        """Возвращает сводку feedback по модели в конкретном профиле."""
        store = self._ensure_feedback_store()
        profiles = store.get("profiles", {})
        profile_data = profiles.get(profile, {}) if isinstance(profiles, dict) else {}
        models = profile_data.get("models", {}) if isinstance(profile_data, dict) else {}
        entry = models.get(model_name, {}) if isinstance(models, dict) else {}
        count = int(entry.get("count", 0)) if isinstance(entry, dict) else 0
        avg = float(entry.get("avg", 0.0)) if isinstance(entry, dict) else 0.0
        return {"count": count, "avg": round(avg, 3)}

    def classify_task_profile(self, prompt: str, task_type: str = "chat") -> str:
        """
        Классифицирует профиль задачи для роутинга.
        Профили: chat, moderation, code, security, infra, review, communication.
        """
        normalized_type = (task_type or "chat").strip().lower()
        if normalized_type in {"coding", "code"}:
            return "code"
        if normalized_type in {"reasoning", "review"}:
            return "review"

        text = (prompt or "").lower()
        keyword_map = {
            "moderation": ["ban", "mute", "warn", "delete message", "спам", "модерац", "muted"],
            "security": ["vulnerability", "security", "audit", "exploit", "уязв", "безопас"],
            "infra": ["deploy", "terraform", "k8s", "kubernetes", "docker", "infra", "сервер", "ci/cd"],
            "review": ["code review", "critique", "проверь код", "ревью", "критика"],
            "communication": ["translate", "перевод", "summary", "саммари", "telegram", "чат"],
            "code": ["python", "typescript", "javascript", "refactor", "bugfix", "код", "скрипт"],
        }
        for profile, markers in keyword_map.items():
            if any(marker in text for marker in markers):
                return profile
        return "chat"

    def _is_critical_profile(self, profile: str) -> bool:
        """Критичные профили, где по умолчанию выше приоритет качества."""
        return profile in {"security", "infra", "review"}

    def _model_tier(self, model_name: Optional[str]) -> str:
        """
        Определяет класс локальной модели для scheduler-а:
        heavy или light.
        """
        if not model_name:
            return "light"
        lowered = model_name.lower()
        if any(token in lowered for token in ["70b", "72b", "34b", "32b", "30b", "27b", "22b", "20b", "mixtral"]):
            return "heavy"

        match = re.search(r"(\d+)\s*b", lowered)
        if match:
            try:
                size_b = int(match.group(1))
                return "heavy" if size_b >= 20 else "light"
            except ValueError:
                return "light"
        return "light"

    @asynccontextmanager
    async def _acquire_local_slot(self, model_name: Optional[str]):
        """
        Планировщик локальных запусков:
        - heavy: максимум 1 одновременный heavy.
        - light: максимум 1 одновременный light.
        """
        tier = self._model_tier(model_name)
        semaphore = self._local_heavy_slot if tier == "heavy" else self._local_light_slot
        await semaphore.acquire()
        try:
            yield tier
        finally:
            semaphore.release()

    def _remember_model_choice(self, profile: str, model_name: str, channel: str) -> None:
        """
        Запоминает фактический выбор модели для похожих задач.
        """
        if not profile or not model_name:
            return

        memory = self._routing_memory.setdefault("profiles", {})
        profile_entry = memory.setdefault(profile, {"models": {}, "channels": {}})
        profile_entry["models"][model_name] = int(profile_entry["models"].get(model_name, 0)) + 1
        profile_entry["channels"][channel] = int(profile_entry["channels"].get(channel, 0)) + 1
        self._save_json(self._routing_memory_path, self._routing_memory)

    def _update_usage_report(self, profile: str, model_name: str, channel: str) -> None:
        """Обновляет отчёт usage/cost guardrails."""
        profiles = self._usage_report.setdefault("profiles", {})
        profiles[profile] = int(profiles.get(profile, 0)) + 1

        models = self._usage_report.setdefault("models", {})
        models[model_name] = int(models.get(model_name, 0)) + 1

        channels = self._usage_report.setdefault("channels", {"local": 0, "cloud": 0})
        channels[channel] = int(channels.get(channel, 0)) + 1

        if channel == "cloud" and channels.get("cloud", 0) >= self.cloud_soft_cap_calls:
            self.cloud_soft_cap_reached = True

        self._save_json(self._usage_report_path, self._usage_report)

    def _get_profile_recommendation(self, profile: str) -> dict:
        """
        Возвращает рекомендованную модель и канал для профиля.
        """
        profile = profile or "chat"
        profiles = self._routing_memory.get("profiles", {})
        memorized = profiles.get(profile, {})
        memorized_models = memorized.get("models", {})
        memorized_channels = memorized.get("channels", {})

        top_model = None
        top_channel = None
        if memorized_models:
            top_model = max(memorized_models.items(), key=lambda item: int(item[1]))[0]
        if memorized_channels:
            top_channel = max(memorized_channels.items(), key=lambda item: int(item[1]))[0]

        if profile in {"security", "infra", "review"}:
            default_model = self.models.get("pro", self.models.get("thinking", self.models["chat"]))
            default_channel = "cloud"
        elif profile == "code":
            default_model = self.models.get("coding", self.models["chat"])
            default_channel = "local"
        elif profile == "moderation":
            default_model = self.models.get("chat", "gemini-2.0-flash")
        
        if not profile:
            default_model = self.models.get("chat", "gemini-2.0-flash")
            default_channel = "local"

        # Adaptive feedback loop: если по модели накоплены оценки,
        # дополнительно взвешиваем выбор по среднему качеству.
        store = self._ensure_feedback_store()
        feedback_profiles = store.get("profiles", {})
        feedback_profile = feedback_profiles.get(profile, {}) if isinstance(feedback_profiles, dict) else {}
        feedback_models = feedback_profile.get("models", {}) if isinstance(feedback_profile, dict) else {}

        candidate_models = set(memorized_models.keys()) if isinstance(memorized_models, dict) else set()
        if isinstance(feedback_models, dict):
            candidate_models.update(feedback_models.keys())
        if not candidate_models and default_model:
            candidate_models.add(default_model)

        if candidate_models:
            best_model = None
            best_score = None
            for candidate in candidate_models:
                usage_count = int(memorized_models.get(candidate, 0)) if isinstance(memorized_models, dict) else 0
                feedback_entry = feedback_models.get(candidate, {}) if isinstance(feedback_models, dict) else {}
                feedback_count = int(feedback_entry.get("count", 0)) if isinstance(feedback_entry, dict) else 0
                feedback_avg = float(feedback_entry.get("avg", 0.0)) if isinstance(feedback_entry, dict) else 0.0

                # Базовый вес usage + вес качества.
                quality_weight = (feedback_avg / 5.0) * min(feedback_count, 12)
                score = float(usage_count) + float(quality_weight)

                # Жесткий штраф за системно низкие оценки.
                if feedback_count >= 3 and feedback_avg <= 2.4:
                    score -= 4.0

                if best_score is None or score > best_score:
                    best_score = score
                    best_model = candidate

            if best_model:
                top_model = best_model

        selected_model = top_model or default_model
        feedback_hint = self._get_model_feedback_stats(profile, selected_model)

        return {
            "profile": profile,
            "model": selected_model,
            "channel": top_channel or default_channel,
            "critical": self._is_critical_profile(profile),
            "feedback_hint": {
                "avg_score": feedback_hint.get("avg", 0.0),
                "count": feedback_hint.get("count", 0),
            },
        }

    def _resolve_cloud_model(self, task_type: str, profile: str, preferred_model: Optional[str] = None) -> str:
        """Выбирает облачную модель с учетом профиля и предпочтений."""
        if preferred_model and "gemini" in preferred_model:
            return preferred_model
        if profile in {"security", "infra", "review"}:
            return self.models.get("pro", self.models.get("thinking", self.models["chat"]))
        if profile == "code":
            return self.models.get("coding", self.models["chat"])
        if task_type == "reasoning":
            return self.models.get("thinking", self.models["chat"])
        return self.models.get(task_type, self.models["chat"])

    def _build_cloud_candidates(self, task_type: str, profile: str, preferred_model: Optional[str] = None) -> List[str]:
        """
        Формирует последовательность моделей для cloud-подсистемы.
        """
        base = self._resolve_cloud_model(task_type, profile, preferred_model)
        candidates: list[str] = []
        seen: Set[str] = set()

        def add(model_name: Optional[str]) -> None:
            if not model_name:
                return
            normalized = model_name.strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            candidates.append(normalized)

        add(preferred_model or "")
        add(base)
        for extra in self.cloud_priority_models:
            add(extra)

        return candidates

    async def check_local_health(self, force: bool = False) -> bool:
        """
        Проверяет доступность локального движка (LM Studio → Ollama).
        """
        now = time.time()
        if not force and (now - self._health_cache_ts) < self._health_cache_ttl:
            return self.is_local_available

        self._health_cache_ts = now

        base_root = self._lm_studio_api_root()
        if not base_root:
            base_root = self.lm_studio_url.rstrip("/")

        # Сначала проверяем, есть ли РЕАЛЬНО загруженная модель через /api/v1/models
        # (в 0.3.x загруженные модели имеют специфические поля или это единственный способ)
        try:
            models = await self._scan_local_models()
            loaded_models = [m for m in models if m.get("loaded")]
            
            if loaded_models:
                self.local_engine = "lm-studio"
                self.is_local_available = True
                self.active_local_model = loaded_models[0]["id"]
                logger.info(f"✅ Local AI active: {self.active_local_model} (LM Studio)")
                return True
            
            # Если моделей загруженных нет, проверяем доступность самого сервера
            timeout = aiohttp.ClientTimeout(total=2)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{base_root}/api/v1/models") as resp:
                    if resp.status == 200:
                        self.local_engine = "lm-studio"
                        self.is_local_available = False # Но модель не загружена!
                        self.active_local_model = None
                        logger.info("📡 LM Studio server alive, but no models loaded.")
                        return False
        except Exception:
            pass

        # Fallback to Ollama
        try:
            timeout = aiohttp.ClientTimeout(total=2)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.ollama_url.replace('/api', '/v1')}/models") as response:
                    if response.status == 200:
                        payload = await response.json()
                        models = self._normalize_model_entries(payload)
                        if models:
                            self.active_local_model = self._extract_model_id(models[0]) or models[0].get("id")
                            self.local_engine = "ollama"
                            self.is_local_available = True
                            return True
        except Exception:
            pass

        self.is_local_available = False
        self.local_engine = None
        self.active_local_model = None
        return False

    async def _scan_local_models(self) -> List[Dict[str, Any]]:
        """
        Сканирует доступные локальные модели через REST API LM Studio 0.3.x или CLI.
        """
        base = self._lm_studio_api_root()
        url = f"{base}/api/v1/models"
        
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        payload = await resp.json(content_type=None)
                        normalized = []
                        if isinstance(payload, dict):
                            normalized = payload.get("data") or payload.get("models") or []
                        elif isinstance(payload, list):
                            normalized = payload

                        models = []
                        for m in normalized:
                            identifier = self._extract_model_id(m) or m.get("id", "")
                            if not identifier: continue
                            
                            # В 0.3.x загруженная модель часто имеет state="loaded" или аналогичное
                            # Но самый простой способ — посмотреть, есть ли у нее инстанс в API
                            state = m.get("state", "").lower()
                            is_loaded = (state == "loaded" or m.get("is_loaded") is True)
                            
                            models.append({
                                "id": identifier,
                                "type": "embedding" if "embedding" in identifier.lower() else "llm",
                                "name": m.get("name", identifier),
                                "loaded": is_loaded
                            })
                        return models
        except Exception:
            pass

        # Fallback to CLI only if API fails or exception occurs

        # Fallback to CLI only if API fails
        lms_path = os.path.expanduser("~/.lmstudio/bin/lms")
        if not os.path.exists(lms_path):
            return []

        try:
            proc = await asyncio.create_subprocess_exec(
                lms_path, "ls",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode()
            
            models = []
            is_embedding_section = False
            for line in output.splitlines():
                line = line.strip()
                if not line or "SIZE" in line: continue
                if "EMBEDDING" in line: is_embedding_section = True; continue
                if "LLM" in line: is_embedding_section = False; continue
                parts = line.split()
                if parts and ("/" in parts[0] or "-" in parts[0]):
                    models.append({
                        "id": parts[0],
                        "type": "embedding" if is_embedding_section else "llm"
                    })
            return models
        except Exception:
            return []

    async def _ensure_chat_model_loaded(self) -> bool:
        """
        Пытается загрузить любую доступную LLM модель через REST API.
        """
        # Сначала проверяем текущий статус
        if await self.check_local_health(force=True):
            if self.active_local_model and "embed" not in self.active_local_model.lower():
                return True

        models = await self._scan_local_models()
        chat_candidate = next((m["id"] for m in models if m["type"] == "llm"), None)
        
        if chat_candidate:
            return await self.load_local_model(chat_candidate)
        return False
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
                if "LOADED" in output: 
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
            models = await self._scan_local_models()
            
            # Ищем LLM (не embedding)
            chat_candidate = None
            
            # Priority 1: Instruct/Chat models
            for m in models:
                if m["type"] == "embedding":
                    continue
                mid = m["id"].lower()
                if "instruct" in mid or "chat" in mid:
                    chat_candidate = m["id"]
                    break
            
            # Priority 2: Any LLM
            if not chat_candidate:
                for m in models:
                    if m["type"] == "embedding":
                        continue
                    chat_candidate = m["id"]
                    break
            
            if chat_candidate:
                logger.info(f"🚀 Auto-Loading Local Model: {chat_candidate}")
                # Use -y to accept defaults for variants
                await asyncio.create_subprocess_exec(lms_path, "load", chat_candidate, "--gpu", "auto", "-y")
                await asyncio.sleep(5) # Wait for load
                return True
            else:
                logger.warning("⚠️ No Chat models found in 'lms ls'.")
                return False

        except Exception as e:
            logger.error(f"❌ Auto-load failed: {e}")
            return False

    async def list_local_models(self) -> List[str]:
        """Сканирует доступные локальные модели (lms ls) и возвращает уникальные ID."""
        models = await self._scan_local_models()
        ids: list[str] = []
        for entry in models:
            identifier = self._extract_model_id(entry)
            if identifier:
                ids.append(identifier)
        # Удаляем дубли и сортируем в устойчивом порядке
        return sorted(set(ids))

    async def load_local_model(self, model_name: str) -> bool:
        """
        Загружает модель в LM Studio через REST API (0.3.x).
        """
        base = self._lm_studio_api_root()
        # В 0.3.x эндпоинт загрузки: POST /api/v1/models/load
        url = f"{base}/api/v1/models/load"
        
        try:
            logger.info(f"🚀 Loading model via REST API: {model_name}")
            timeout = aiohttp.ClientTimeout(total=35)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                payload = {
                    "identifier": model_name,
                    "gpu_offload": "auto"
                }
                async with session.post(url, json=payload, timeout=30) as resp:
                    if resp.status == 200:
                        logger.info(f"✅ REST API Load Success: {model_name}")
                        self.active_local_model = model_name
                        self.is_local_available = True
                        return True
                    else:
                        text = await resp.text()
                        logger.warning(f"⚠️ REST API Load failed ({resp.status}): {text}")
        except Exception as e:
            logger.error(f"❌ REST API Load Exception: {e}")

        # Fallback to CLI for backwards compatibility
        lms_path = os.path.expanduser("~/.lmstudio/bin/lms")
        if os.path.exists(lms_path):
            try:
                proc = await asyncio.create_subprocess_exec(
                    lms_path, "load", model_name, "--gpu", "auto", "-y"
                )
                await proc.communicate()
                if proc.returncode == 0:
                    self.active_local_model = model_name
                    self.is_local_available = True
                    return True
            except Exception:
                pass
        
        return False

    async def unload_local_model(self, model_name: str = None) -> bool:
        """
        Выгружает модель из LM Studio через REST API.
        """
        base = self._lm_studio_api_root()
        url = f"{base}/api/v1/models/unload"
        
        try:
            payload = {}
            if model_name:
                payload["identifier"] = model_name
            
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        logger.info(f"✅ REST API Unload Success")
                        if not model_name:
                            self.active_local_model = None
                        return True
        except Exception as e:
            logger.error(f"❌ REST API Unload failed: {e}")

        # Fallback to CLI
        lms_path = os.path.expanduser("~/.lmstudio/bin/lms")
        if os.path.exists(lms_path):
            try:
                cmd = [lms_path, "unload", "--all"] if not model_name else [lms_path, "unload", model_name]
                proc = await asyncio.create_subprocess_exec(*cmd)
                await proc.communicate()
                return proc.returncode == 0
            except Exception:
                pass
        return False

        # Legacy fallback removed

    async def list_cloud_models(self) -> List[str]:
        """Сканирует доступные Cloud модели (via OpenClaw)."""
        if not self.openclaw_client:
            return ["Ошибка: OpenClaw клиент не инициализирован"]
        
        try:
            raw_models = await self.openclaw_client.get_models()
            models = []
            for m in raw_models:
                # OpenAI format: {"id": "foo", "object": "model"}
                if isinstance(m, dict) and "id" in m:
                    models.append(m["id"])
                # Fallback: simple string list
                elif isinstance(m, str):
                    models.append(m)
            
            self.last_cloud_error = None  # Сбрасываем старую ошибку при успехе
            return sorted(models)
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Cloud scan error: {err_msg}")
            # Если это ошибка биллинга, помечаем soft cap
            self._mark_cloud_soft_cap_if_needed(err_msg)
            
            # Возвращаем понятное сообщение об ошибке для команды !model scan
            if self._is_cloud_billing_error(err_msg):
                return [f"❌ Ошибка биллинга (Cloud): Оплатите счет или замените API ключ в .env"]
            return [f"Ошибка API: {err_msg}"]

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
                for idx, msg in enumerate(context):
                    if not isinstance(msg, dict):
                        logger.debug("Skipping context entry (not dict) #%s: %s", idx, type(msg))
                        continue
                    mrole = str(msg.get("role") or "user")
                    content = msg.get("content") or msg.get("text") or msg.get("message")
                    if isinstance(content, list):
                        content = "\n".join(str(item) for item in content if item is not None)
                    elif isinstance(content, dict):
                        content = json.dumps(content, ensure_ascii=False)
                    if content is None:
                        logger.debug("Skipping context entry #%s due to missing content", idx)
                        continue
                    messages.append({"role": mrole, "content": str(content)})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": self.active_local_model or "local-model",
                "messages": messages,
                "temperature": 0.7,
                "include_reasoning": True  # User requested reasoning back
            }

            headers = {"Content-Type": "application/json"}
            
            # Таймаут 300с для тяжелых генераций
            timeout = aiohttp.ClientTimeout(total=max(300, self.local_timeout_seconds))
            start_t = time.time()

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{base_url}/chat/completions", 
                    json=payload, 
                    headers=headers
                ) as response:
                    
                        if response.status == 200:
                            data = await response.json()
                            duration = time.time() - start_t
                            
                            choices = data.get('choices')
                            if choices and len(choices) > 0:
                                content = choices[0].get('message', {}).get('content')
                                reasoning = choices[0].get('message', {}).get('reasoning_content')
                                
                                if content:
                                    logger.info(
                                        "Local LLM success",
                                        duration_sec=round(duration, 2),
                                        char_count=len(content),
                                        has_reasoning=bool(reasoning)
                                    )
                                    return content
                            
                            logger.warning("Local LLM returned empty choices")
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
                          task_type: Literal['coding', 'chat', 'reasoning', 'creative', 'moderation', 'security', 'infra', 'review'] = 'chat',
                          context: list = None,
                          chat_type: str = "private",
                          is_owner: bool = False,
                          use_rag: bool = True,
                          preferred_model: Optional[str] = None,
                          confirm_expensive: bool = False):
        """
        Главный метод маршрутизации запроса с Auto-Fallback, RAG и policy-роутингом.
        """

        profile = self.classify_task_profile(prompt, task_type)
        recommendation = self._get_profile_recommendation(profile)
        is_critical = recommendation["critical"]

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

        await self.check_local_health()

        async def _run_local() -> Optional[str]:
            if not self.is_local_available:
                return None
            async with self._acquire_local_slot(self.active_local_model):
                logger.info(
                    "Routing to LOCAL",
                    model=self.active_local_model,
                    profile=profile,
                    tier=self._model_tier(self.active_local_model),
                )
                local_response = await self._call_local_llm(prompt, context, chat_type, is_owner)
                if local_response:
                    self._stats["local_calls"] += 1
                    local_model = self.active_local_model or "local-model"
                    self._remember_model_choice(profile, local_model, "local")
                    self._update_usage_report(profile, local_model, "local")
                    self._remember_last_route(
                        profile=profile,
                        task_type=task_type,
                        channel="local",
                        model_name=local_model,
                        prompt=prompt,
                    )
                return local_response

        async def _run_cloud():
            if self.require_confirm_expensive and is_critical and not confirm_expensive:
                return "confirm_needed", "⚠️ Для критичной задачи требуется подтверждение дорогого облачного прогона. Повтори команду с подтверждением."
            for i, candidate in enumerate(self._build_cloud_candidates(task_type, profile, preferred_model or recommendation.get("model"))):
                logger.info("Routing to CLOUD", model=candidate, profile=profile)
                # Для первого кандидата делаем ретраи, для остальных - пробуем один раз и идем дальше
                max_retries_cloud = 1 if i == 0 else 0
                response = await self._call_gemini(prompt, candidate, context, chat_type, is_owner, max_retries=max_retries_cloud)
                normalized = (response or "").strip()
                cloud_issue = (
                    self._is_cloud_error_message(normalized) or self._is_cloud_billing_error(normalized)
                )
                if cloud_issue:
                    error_label = normalized or response or "cloud_error"
                    logger.warning("Cloud candidate %s failed: %s", candidate, error_label)
                    self._mark_cloud_soft_cap_if_needed(error_label)
                    self.last_cloud_error = error_label
                    self.last_cloud_model = candidate
                    continue
                self.last_cloud_error = None
                self.last_cloud_model = candidate
                return candidate, response or ""
            return None

        if self.force_mode == "force_local":
            if not self.is_local_available:
                return "❌ Режим 'Force Local' включен, но локальная модель недоступна (LM Studio/Ollama offline)."
            forced_local = await _run_local()
            if forced_local:
                return forced_local
            return "❌ Ошибка генерации локальной модели (Force Local active)."

        def _finalize_cloud(candidate: str, response_text: str) -> Optional[str]:
            if not response_text:
                return None
            self._remember_model_choice(profile, candidate, "cloud")
            self._update_usage_report(profile, candidate, "cloud")
            self._remember_last_route(
                profile=profile,
                task_type=task_type,
                channel="cloud",
                model_name=candidate,
                prompt=prompt,
            )
            return response_text

        if self.force_mode == "force_cloud":
            cloud_result = await _run_cloud()
            if isinstance(cloud_result, str):
                return cloud_result
            if cloud_result:
                candidate, response = cloud_result
                finalized = _finalize_cloud(candidate, response)
                if finalized:
                    return finalized
            return self.last_cloud_error or "❌ Не удалось получить ответ ни от облачной, ни от локальной модели."

        # Soft cap: при превышении лимита облака, не-критичные задачи уводим в локалку.
        force_local_due_cost = self.cloud_soft_cap_reached and not is_critical
        prefer_cloud = is_critical or task_type == "reasoning"
        if recommendation.get("channel") == "cloud":
            prefer_cloud = True
        if force_local_due_cost:
            prefer_cloud = False

        local_response: Optional[str] = None
        if not prefer_cloud and self.is_local_available:
            local_response = await _run_local()
            if local_response:
                return local_response

        latest_cloud_error: Optional[str] = None
        cloud_result = await _run_cloud()
        cloud_response = None
        response_model = None
        if isinstance(cloud_result, tuple):
            response_model, cloud_response = cloud_result
        elif isinstance(cloud_result, str):
            cloud_response = cloud_result

        if isinstance(cloud_result, tuple):
            finalized = _finalize_cloud(response_model, cloud_response or "")
            if finalized:
                return finalized
        elif isinstance(cloud_result, str):
            return cloud_result

        # Если облако не дало ответа, пытаемся локальный fallback.
        if self.is_local_available and not local_response:
            local_response = await _run_local()
            if local_response:
                if is_critical and self.enable_cloud_review_for_critical and self.gemini_client:
                    review_model = self._resolve_cloud_model("reasoning", "review", self.models.get("pro"))
                    review_prompt = (
                        "Проведи строгую проверку и улучшение ответа локальной модели.\n\n"
                        f"Запрос:\n{prompt}\n\n"
                        f"Черновой ответ:\n{local_response}\n\n"
                        "Верни исправленный финальный ответ."
                    )
                    reviewed = await self._call_gemini(review_prompt, review_model, None, chat_type, is_owner)
                    if reviewed and not reviewed.startswith("❌"):
                        self._remember_model_choice("review", review_model, "cloud")
                        self._update_usage_report("review", review_model, "cloud")
                        self._remember_last_route(
                            profile="review",
                            task_type="reasoning",
                            channel="cloud",
                            model_name=review_model,
                            prompt=review_prompt,
                        )
                        return reviewed
                return local_response

        if not latest_cloud_error:
            latest_cloud_error = self.last_cloud_error
        return latest_cloud_error or "❌ Не удалось получить ответ ни от локальной, ни от облачной модели."

    async def _call_gemini(self, prompt: str, model_name: str, context: list = None,
                           chat_type: str = "private", is_owner: bool = False, max_retries: int = 2) -> str:
        """
        Вызов Cloud модели через OpenClaw Gateway.
        """
        # Динамический System Prompt
        from src.core.prompts import get_system_prompt
        base_instructions = get_system_prompt(chat_type == "private")

        persona_prompt = ""
        if self.persona:
            persona_prompt = self.persona.get_current_prompt(chat_type, is_owner)

        system_instructions = f"{persona_prompt}\n\n{base_instructions}".strip()

        # Формируем сообщения для OpenClaw (OpenAI-like format)
        messages = []
        if system_instructions:
            messages.append({"role": "system", "content": system_instructions})
        
        if context:
            # Преобразуем контекст в формат сообщений
            for msg in context:
                role = msg.get("role", "user")
                # Маппинг ролей если нужно, но обычно user/model/assistant совпадают
                if role == "model": role = "assistant"
                messages.append({"role": role, "content": msg.get("text", "")})
        
        messages.append({"role": "user", "content": prompt})

        for attempt in range(max_retries + 1):
            try:
                response_text = await self.openclaw_client.chat_completions(messages, model=model_name)

                normalized = (response_text or "").strip()
                error_detected = self._is_cloud_error_message(normalized)
                billing_issue = self._is_cloud_billing_error(normalized)

                if error_detected or billing_issue:
                    self._mark_cloud_soft_cap_if_needed(normalized or "пустой ответ")
                    if attempt < max_retries:
                        logger.warning(f"OpenClaw Attempt {attempt+1} failed: {response_text}")
                        await asyncio.sleep(2 ** (attempt + 1))
                        continue
                        
                    if billing_issue:
                        return f"❌ Ошибка биллинга (OpenClaw): Похоже, на аккаунте закончились средства или достигнут лимит провайдера. Проверьте баланс на шлюзе. (Детали: {response_text})"
                    return f"❌ Ошибка Cloud: {response_text}"

                self._stats["cloud_calls"] += 1
                return response_text

            except Exception as e:
                logger.error(f"Cloud call failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                
                self._stats["cloud_failures"] += 1
                return f"❌ Ошибка Cloud: {e}"

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

        # 2. Проверка доступности
        if self.force_mode == 'force_local' and not self.is_local_available:
             yield "❌ Режим 'Force Local' включен, но локальная модель недоступна."
             return
             
        if not self.is_local_available and not self.openclaw_client:
             yield "❌ Нет доступных моделей (локальный сервер оффлайн, облачный клиент не настроен)."
             return

        # 3. Маршрутизация
        model_name = self.models.get(task_type, self.models["chat"])
        
        # Если принудительно локалка или она доступна и это чат/код
        if self.force_mode == 'force_local' or (self.is_local_available and task_type in ['chat', 'coding']):
             try:
                 full_res = await self.route_query(prompt, task_type, context, chat_type, is_owner, use_rag=False)
                 yield full_res
             except Exception as e:
                 logger.error(f"Fallback routing in stream failed: {e}")
                 yield f"❌ Ошибка маршрутизации: {e}"
             return

        # 4. Стриминг через облако (Gemini)
        async for chunk in self._call_gemini_stream(prompt, model_name, context, chat_type, is_owner):
            if chunk:
                yield chunk
            else:
                break

    async def _call_gemini_stream(self, prompt: str, model_name: str, context: list = None,
                                  chat_type: str = "private", is_owner: bool = False):
        """
        Генератор для стриминга ответов из Cloud (OpenClaw).
        Пока реализован как псевдо-стриминг (полный ответ за раз), так как OpenClawClient.chat_completions не стримит.
        """
        # В будущем можно добавить stream=True в OpenClawClient
        full_response = await self._call_gemini(prompt, model_name, context, chat_type, is_owner)
        yield full_response

    async def diagnose(self) -> dict:
        """
        Полная диагностика всех подсистем.
        """
        result = {}

        # 1. Локальные модели
        local_ok = await self.check_local_health(force=True)
        
        # Enhanced diagnostics via CLI scan
        local_models = await self._scan_local_models()
        local_count = len(local_models)
        
        local_status = "Offline"
        if local_ok:
            if self.active_local_model:
                local_status = f"{self.local_engine}: {self.active_local_model} ({local_count} models available)"
            else:
                local_status = f"{self.local_engine}: Ready (No Model Loaded, {local_count} available)"
        elif local_count > 0:
             local_status = f"Offline ({local_count} models detected via CLI)"
                
        result["Local AI"] = {
            "ok": local_ok,
            "status": local_status,
            "engine": self.local_engine or "Unknown",
            "model_count": local_count,
            "active_model": self.active_local_model
        }

        # 2. Gemini Cloud (via OpenClaw)
        openclaw_health = await self.openclaw_client.health_check()
        result["Cloud (OpenClaw)"] = {
            "ok": openclaw_health,
            "status": f"Ready ({self.models['chat']})" if openclaw_health else "Unreachable",
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

        # 6. Workspace Check
        handover_path = Path(os.getcwd()) / "HANDOVER.md"
        result["📁 Workspace"] = {
            "ok": handover_path.exists(),
            "status": f"Root: {os.getcwd()} (HANDOVER.md: {'Found' if handover_path.exists() else 'MISSING'})"
        }

        return result

    def get_model_info(self) -> dict:
        """Возвращает информацию о текущих моделях для команды !model."""
        recommendations = {
            profile: self._get_profile_recommendation(profile)
            for profile in ["chat", "moderation", "code", "security", "infra", "review", "communication"]
        }
        return {
            "cloud_models": self.models.copy(),
            "local_engine": self.local_engine,
            "local_model": self.active_local_model,
            "local_available": self.is_local_available,
            "stats": self._stats.copy(),
            "force_mode": self.force_mode,
            "fallback_models": self.fallback_models,
            "routing_policy": self.routing_policy,
            "cloud_soft_cap_calls": self.cloud_soft_cap_calls,
            "cloud_soft_cap_reached": self.cloud_soft_cap_reached,
            "recommendations": recommendations,
            "usage_report": self._usage_report.copy(),
            "feedback_summary": self.get_feedback_summary(top=3),
        }

    def get_profile_recommendation(self, profile: str = "chat") -> dict:
        """Публичный helper для показа рекомендаций по профилю."""
        return self._get_profile_recommendation(profile)

    def get_last_route(self) -> dict:
        """Возвращает метаданные последнего успешного прогона роутера."""
        store = self._ensure_feedback_store()
        last_route = store.get("last_route", {})
        return dict(last_route) if isinstance(last_route, dict) else {}

    def submit_feedback(
        self,
        score: int,
        profile: str | None = None,
        model_name: str | None = None,
        channel: str | None = None,
        note: str = "",
    ) -> dict:
        """
        Принимает оценку качества ответа (1-5) и сохраняет её
        в профильную статистику выбора моделей.
        """
        try:
            normalized_score = int(score)
        except Exception as exc:
            raise ValueError("score_must_be_integer_1_5") from exc
        if normalized_score < 1 or normalized_score > 5:
            raise ValueError("score_out_of_range_1_5")

        store = self._ensure_feedback_store()
        last_route = store.get("last_route", {}) if isinstance(store.get("last_route"), dict) else {}

        resolved_profile = str(profile or last_route.get("profile", "")).strip().lower()
        resolved_model = str(model_name or last_route.get("model", "")).strip()
        resolved_channel = self._normalize_channel(channel or last_route.get("channel"))

        if not resolved_profile or not resolved_model:
            raise ValueError("profile_and_model_required_or_run_task_first")

        profiles = store.setdefault("profiles", {})
        profile_entry = profiles.setdefault(
            resolved_profile,
            {"models": {}, "channels": {}, "feedback_total": 0},
        )
        if not isinstance(profile_entry.get("models"), dict):
            profile_entry["models"] = {}
        if not isinstance(profile_entry.get("channels"), dict):
            profile_entry["channels"] = {}

        model_entry = profile_entry["models"].setdefault(
            resolved_model,
            {"count": 0, "sum": 0, "avg": 0.0, "channels": {}, "last_score": 0, "last_ts": ""},
        )
        model_entry["count"] = int(model_entry.get("count", 0)) + 1
        model_entry["sum"] = int(model_entry.get("sum", 0)) + normalized_score
        model_entry["avg"] = round(model_entry["sum"] / model_entry["count"], 3)
        model_entry["last_score"] = normalized_score
        model_entry["last_ts"] = self._now_iso()
        if not isinstance(model_entry.get("channels"), dict):
            model_entry["channels"] = {}

        model_channel_entry = model_entry["channels"].setdefault(
            resolved_channel,
            {"count": 0, "sum": 0, "avg": 0.0},
        )
        model_channel_entry["count"] = int(model_channel_entry.get("count", 0)) + 1
        model_channel_entry["sum"] = int(model_channel_entry.get("sum", 0)) + normalized_score
        model_channel_entry["avg"] = round(model_channel_entry["sum"] / model_channel_entry["count"], 3)

        profile_channel_entry = profile_entry["channels"].setdefault(
            resolved_channel,
            {"count": 0, "sum": 0, "avg": 0.0},
        )
        profile_channel_entry["count"] = int(profile_channel_entry.get("count", 0)) + 1
        profile_channel_entry["sum"] = int(profile_channel_entry.get("sum", 0)) + normalized_score
        profile_channel_entry["avg"] = round(profile_channel_entry["sum"] / profile_channel_entry["count"], 3)
        profile_entry["feedback_total"] = int(profile_entry.get("feedback_total", 0)) + 1

        events = store.setdefault("events", [])
        if not isinstance(events, list):
            events = []
            store["events"] = events
        events.append(
            {
                "ts": self._now_iso(),
                "score": normalized_score,
                "profile": resolved_profile,
                "model": resolved_model,
                "channel": resolved_channel,
                "note": (note or "").strip()[:240],
            }
        )
        if len(events) > 400:
            del events[: len(events) - 400]

        store["updated_at"] = self._now_iso()
        self._save_json(self._feedback_path, store)
        return {
            "ok": True,
            "score": normalized_score,
            "profile": resolved_profile,
            "model": resolved_model,
            "channel": resolved_channel,
            "used_last_route": bool(not profile and not model_name),
            "profile_model_stats": {
                "count": int(model_entry.get("count", 0)),
                "avg": float(model_entry.get("avg", 0.0)),
            },
            "profile_channel_stats": {
                "count": int(profile_channel_entry.get("count", 0)),
                "avg": float(profile_channel_entry.get("avg", 0.0)),
            },
        }

    def get_feedback_summary(self, profile: str | None = None, top: int = 5) -> dict:
        """
        Возвращает агрегированную сводку по оценкам качества маршрутизации.
        """
        safe_top = max(1, min(int(top), 20))
        store = self._ensure_feedback_store()
        profiles = store.get("profiles", {})
        events = store.get("events", [])
        last_route = store.get("last_route", {})

        profile_key = (profile or "").strip().lower() or None
        selected_profiles: list[tuple[str, dict]] = []
        if profile_key:
            selected_profiles.append((profile_key, profiles.get(profile_key, {})))
        else:
            selected_profiles = list(profiles.items())

        top_models: list[dict[str, Any]] = []
        channels_agg: dict[str, dict[str, float]] = {}
        total_feedback = 0

        for profile_name, pdata in selected_profiles:
            if not isinstance(pdata, dict):
                continue
            models = pdata.get("models", {})
            channels = pdata.get("channels", {})
            if not isinstance(models, dict):
                models = {}
            if not isinstance(channels, dict):
                channels = {}

            for model_name, mdata in models.items():
                if not isinstance(mdata, dict):
                    continue
                count = int(mdata.get("count", 0))
                avg = float(mdata.get("avg", 0.0))
                total_feedback += count
                top_models.append(
                    {
                        "profile": profile_name,
                        "model": str(model_name),
                        "count": count,
                        "avg_score": round(avg, 3),
                        "last_score": int(mdata.get("last_score", 0)),
                        "last_ts": str(mdata.get("last_ts", "")),
                    }
                )

            for channel_name, cdata in channels.items():
                if not isinstance(cdata, dict):
                    continue
                entry = channels_agg.setdefault(
                    str(channel_name),
                    {"count": 0, "sum": 0.0},
                )
                ch_count = int(cdata.get("count", 0))
                ch_avg = float(cdata.get("avg", 0.0))
                entry["count"] += ch_count
                entry["sum"] += ch_avg * ch_count

        top_models_sorted = sorted(
            top_models,
            key=lambda item: (float(item.get("avg_score", 0.0)), int(item.get("count", 0))),
            reverse=True,
        )[:safe_top]

        top_channels: list[dict[str, Any]] = []
        for channel_name, cdata in channels_agg.items():
            ccount = int(cdata.get("count", 0))
            csum = float(cdata.get("sum", 0.0))
            avg = (csum / ccount) if ccount > 0 else 0.0
            top_channels.append({"channel": channel_name, "count": ccount, "avg_score": round(avg, 3)})
        top_channels = sorted(
            top_channels,
            key=lambda item: (float(item.get("avg_score", 0.0)), int(item.get("count", 0))),
            reverse=True,
        )[:3]

        recent_events = []
        if isinstance(events, list):
            for item in events[-5:]:
                if isinstance(item, dict):
                    recent_events.append(
                        {
                            "ts": str(item.get("ts", "")),
                            "score": int(item.get("score", 0)),
                            "profile": str(item.get("profile", "")),
                            "model": str(item.get("model", "")),
                            "channel": str(item.get("channel", "")),
                        }
                    )

        return {
            "generated_at": self._now_iso(),
            "profile": profile_key,
            "top_models": top_models_sorted,
            "top_channels": top_channels,
            "total_feedback": total_feedback,
            "recent_events": recent_events,
            "last_route": dict(last_route) if isinstance(last_route, dict) else {},
        }

    def get_task_preflight(
        self,
        prompt: str,
        task_type: str = "chat",
        preferred_model: str | None = None,
        confirm_expensive: bool = False,
    ) -> dict:
        """
        Возвращает preflight-план выполнения задачи до реального запуска:
        - профиль и критичность;
        - предпочтительный канал/модель;
        - требования confirm-step;
        - предупреждения/риски;
        - ориентировочная маржинальная стоимость.
        """
        normalized_prompt = (prompt or "").strip()
        normalized_task_type = (task_type or "chat").strip().lower() or "chat"
        profile = self.classify_task_profile(normalized_prompt, normalized_task_type)
        recommendation = self._get_profile_recommendation(profile)
        is_critical = bool(recommendation.get("critical"))

        chosen_channel = recommendation.get("channel", "local")
        if self.force_mode == "force_local":
            chosen_channel = "local"
        elif self.force_mode == "force_cloud":
            chosen_channel = "cloud"
        else:
            prefer_cloud = is_critical or normalized_task_type == "reasoning"
            if recommendation.get("channel") == "cloud":
                prefer_cloud = True
            if self.cloud_soft_cap_reached and not is_critical:
                prefer_cloud = False
            chosen_channel = "cloud" if prefer_cloud else "local"

        if chosen_channel == "cloud":
            chosen_model = self._resolve_cloud_model(
                normalized_task_type,
                profile,
                preferred_model or recommendation.get("model"),
            )
        else:
            chosen_model = self.active_local_model or "local-auto"

        requires_confirm = bool(
            self.require_confirm_expensive and is_critical and chosen_channel == "cloud" and not confirm_expensive
        )
        can_run_now = not requires_confirm

        warnings: list[str] = []
        if chosen_channel == "local" and not self.is_local_available:
            warnings.append("Локальный канал сейчас offline; возможен fallback в cloud.")
        if self.cloud_soft_cap_reached and chosen_channel == "cloud":
            warnings.append("Cloud soft cap уже достигнут: проверь policy/лимиты перед запуском.")
        if requires_confirm:
            warnings.append("Для этой задачи обязателен confirm-step (`--confirm-expensive`).")
        feedback_hint = recommendation.get("feedback_hint", {})
        feedback_count = int(feedback_hint.get("count", 0)) if isinstance(feedback_hint, dict) else 0
        feedback_avg = float(feedback_hint.get("avg_score", 0.0)) if isinstance(feedback_hint, dict) else 0.0
        if feedback_count >= 3 and feedback_avg <= 2.5:
            warnings.append(
                f"У выбранной модели низкий пользовательский рейтинг ({feedback_avg}/5); "
                "рекомендуется сменить модель перед запуском."
            )

        marginal_cost_usd = (
            float(self.cloud_cost_per_call_usd)
            if chosen_channel == "cloud"
            else float(self.local_cost_per_call_usd)
        )

        reasons: list[str] = []
        if is_critical:
            reasons.append("Критичный профиль задачи.")
        if normalized_task_type == "reasoning":
            reasons.append("Reasoning-задача с повышенным приоритетом качества.")
        if self.force_mode == "force_local":
            reasons.append("Включен принудительный режим force_local.")
        elif self.force_mode == "force_cloud":
            reasons.append("Включен принудительный режим force_cloud.")
        if self.cloud_soft_cap_reached and not is_critical:
            reasons.append("Cloud soft cap активен: non-critical задачи сдвинуты в local.")
        if feedback_count >= 2:
            reasons.append(
                f"История качества для модели: {feedback_avg}/5 на {feedback_count} оценках."
            )
        if not reasons:
            reasons.append("Стандартная policy free-first hybrid.")

        return {
            "generated_at": self._now_iso(),
            "task_type": normalized_task_type,
            "profile": profile,
            "critical": is_critical,
            "prompt_preview": normalized_prompt[:240],
            "recommendation": recommendation,
            "execution": {
                "channel": chosen_channel,
                "model": chosen_model,
                "can_run_now": can_run_now,
                "requires_confirm_expensive": requires_confirm,
                "confirm_expensive_received": bool(confirm_expensive),
            },
            "policy": {
                "routing_policy": self.routing_policy,
                "force_mode": self.force_mode,
                "cloud_soft_cap_reached": bool(self.cloud_soft_cap_reached),
                "local_available": bool(self.is_local_available),
            },
            "cost_hint": {
                "marginal_call_cost_usd": round(marginal_cost_usd, 6),
                "cloud_cost_per_call_usd": float(self.cloud_cost_per_call_usd),
                "local_cost_per_call_usd": float(self.local_cost_per_call_usd),
            },
            "warnings": warnings,
            "reasons": reasons,
            "next_step": (
                "Запусти задачу с флагом --confirm-expensive."
                if requires_confirm
                else "Можно запускать задачу."
            ),
        }

    def get_usage_summary(self) -> dict:
        """
        Возвращает агрегированный usage-срез для Ops панели и алертов.
        """
        channels = self._usage_report.get("channels", {}) if isinstance(self._usage_report, dict) else {}
        local_calls = int(channels.get("local", 0))
        cloud_calls = int(channels.get("cloud", 0))
        total_calls = local_calls + cloud_calls

        cloud_share = round((cloud_calls / total_calls), 3) if total_calls > 0 else 0.0
        local_share = round((local_calls / total_calls), 3) if total_calls > 0 else 0.0

        models = self._usage_report.get("models", {}) if isinstance(self._usage_report, dict) else {}
        top_models = sorted(
            ((name, int(count)) for name, count in models.items()),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
        profiles = self._usage_report.get("profiles", {}) if isinstance(self._usage_report, dict) else {}
        top_profiles = sorted(
            ((name, int(count)) for name, count in profiles.items()),
            key=lambda item: item[1],
            reverse=True,
        )[:5]

        cloud_remaining = max(0, int(self.cloud_soft_cap_calls) - cloud_calls)
        return {
            "totals": {
                "all_calls": total_calls,
                "local_calls": local_calls,
                "cloud_calls": cloud_calls,
            },
            "ratios": {
                "local_share": local_share,
                "cloud_share": cloud_share,
            },
            "soft_cap": {
                "cloud_soft_cap_calls": int(self.cloud_soft_cap_calls),
                "cloud_soft_cap_reached": bool(self.cloud_soft_cap_reached),
                "cloud_remaining_calls": cloud_remaining,
            },
            "top_models": [{"model": name, "count": count} for name, count in top_models],
            "top_profiles": [{"profile": name, "count": count} for name, count in top_profiles],
        }

    def get_ops_alerts(self) -> dict:
        """
        Возвращает список активных алертов и общее состояние ops-контура.
        """
        summary = self.get_usage_summary()
        alerts: list[dict[str, str]] = []
        cloud_calls = int(summary["totals"]["cloud_calls"])
        local_calls = int(summary["totals"]["local_calls"])
        soft_cap = int(summary["soft_cap"]["cloud_soft_cap_calls"])
        remaining = int(summary["soft_cap"]["cloud_remaining_calls"])
        cloud_share = float(summary["ratios"]["cloud_share"])

        if bool(summary["soft_cap"]["cloud_soft_cap_reached"]):
            alerts.append(
                {
                    "severity": "high",
                    "code": "cloud_soft_cap_reached",
                    "message": "Достигнут лимит CLOUD_SOFT_CAP_CALLS, не-критичные задачи уйдут в локалку.",
                }
            )
        elif soft_cap > 0 and cloud_calls >= int(soft_cap * 0.8):
            alerts.append(
                {
                    "severity": "medium",
                    "code": "cloud_soft_cap_near",
                    "message": f"Cloud usage близко к лимиту: осталось {remaining} вызовов.",
                }
            )

        if cloud_calls >= 20 and cloud_share >= 0.75:
            alerts.append(
                {
                    "severity": "medium",
                    "code": "cloud_share_high",
                    "message": "Высокая доля облачных вызовов; проверь политику free-first и локальные модели.",
                }
            )

        if local_calls == 0 and cloud_calls > 0:
            alerts.append(
                {
                    "severity": "low",
                    "code": "local_usage_absent",
                    "message": "Локальный канал не используется; проверь LM Studio/Ollama и маршрутизацию.",
                }
            )

        # Качественный guardrail: если модель стабильно получает низкие оценки.
        store = self._ensure_feedback_store()
        low_quality_models: list[str] = []
        feedback_profiles = store.get("profiles", {})
        if isinstance(feedback_profiles, dict):
            for profile_name, pdata in feedback_profiles.items():
                if not isinstance(pdata, dict):
                    continue
                models = pdata.get("models", {})
                if not isinstance(models, dict):
                    continue
                for model_name, mdata in models.items():
                    if not isinstance(mdata, dict):
                        continue
                    mcount = int(mdata.get("count", 0))
                    mavg = float(mdata.get("avg", 0.0))
                    if mcount >= 3 and mavg <= 2.5:
                        low_quality_models.append(f"{profile_name}:{model_name}({mavg}/5, n={mcount})")
                        if len(low_quality_models) >= 2:
                            break
                if len(low_quality_models) >= 2:
                    break
        if low_quality_models:
            alerts.append(
                {
                    "severity": "medium",
                    "code": "model_quality_degraded",
                    "message": "Есть модели с низким user-feedback: " + "; ".join(low_quality_models),
                }
            )

        # Бюджетные guardrails (оценка на горизонте forecast вызовов).
        cost_report = self.get_cost_report(monthly_calls_forecast=self.monthly_calls_forecast)
        monthly = cost_report.get("monthly_forecast", {})
        forecast_total = float(monthly.get("forecast_total_cost", 0.0))
        budget = max(0.0, float(self.cloud_monthly_budget_usd))
        if budget > 0:
            ratio = forecast_total / budget if budget else 0.0
            if ratio >= 1.0:
                alerts.append(
                    {
                        "severity": "high",
                        "code": "cloud_budget_exceeded_forecast",
                        "message": (
                            f"Прогноз облачных расходов ({forecast_total:.2f}$) превышает бюджет "
                            f"({budget:.2f}$) на текущем профиле нагрузки."
                        ),
                    }
                )
            elif ratio >= 0.9:
                alerts.append(
                    {
                        "severity": "medium",
                        "code": "cloud_budget_near_forecast",
                        "message": (
                            f"Прогноз облачных расходов ({forecast_total:.2f}$) близок к бюджету "
                            f"({budget:.2f}$)."
                        ),
                    }
                )

        acknowledged = self._ops_state.get("acknowledged", {}) if isinstance(self._ops_state, dict) else {}
        for alert in alerts:
            code = str(alert.get("code", "")).strip()
            ack_meta = acknowledged.get(code, {})
            if isinstance(ack_meta, dict) and ack_meta:
                alert["acknowledged"] = True
                alert["ack"] = {
                    "ts": str(ack_meta.get("ts", "")),
                    "actor": str(ack_meta.get("actor", "")),
                    "note": str(ack_meta.get("note", "")),
                }
            else:
                alert["acknowledged"] = False

        payload = {
            "status": "alert" if alerts else "ok",
            "alerts": alerts,
            "summary": summary,
            "cost_report": cost_report,
        }
        self._append_ops_history(payload)
        return payload

    def get_cost_report(self, monthly_calls_forecast: int = 5000) -> dict:
        """
        Возвращает оценочный cost-report по текущему usage.
        """
        summary = self.get_usage_summary()
        totals = summary.get("totals", {})
        local_calls = int(totals.get("local_calls", 0))
        cloud_calls = int(totals.get("cloud_calls", 0))
        total_calls = int(totals.get("all_calls", local_calls + cloud_calls))

        cloud_cost = round(cloud_calls * float(self.cloud_cost_per_call_usd), 6)
        local_cost = round(local_calls * float(self.local_cost_per_call_usd), 6)
        total_cost = round(cloud_cost + local_cost, 6)
        avg_cost_per_call = round((total_cost / total_calls), 6) if total_calls > 0 else 0.0

        forecast = max(0, int(monthly_calls_forecast))
        cloud_share = float(summary.get("ratios", {}).get("cloud_share", 0.0))
        local_share = float(summary.get("ratios", {}).get("local_share", 0.0))
        forecast_cloud_calls = round(forecast * cloud_share)
        forecast_local_calls = round(forecast * local_share)
        forecast_cloud_cost = round(forecast_cloud_calls * float(self.cloud_cost_per_call_usd), 6)
        forecast_local_cost = round(forecast_local_calls * float(self.local_cost_per_call_usd), 6)
        forecast_total_cost = round(forecast_cloud_cost + forecast_local_cost, 6)

        return {
            "costs_usd": {
                "cloud_calls_cost": cloud_cost,
                "local_calls_cost": local_cost,
                "total_cost": total_cost,
                "avg_cost_per_call": avg_cost_per_call,
            },
            "pricing": {
                "cloud_cost_per_call_usd": float(self.cloud_cost_per_call_usd),
                "local_cost_per_call_usd": float(self.local_cost_per_call_usd),
            },
            "monthly_forecast": {
                "forecast_calls": forecast,
                "forecast_cloud_calls": forecast_cloud_calls,
                "forecast_local_calls": forecast_local_calls,
                "forecast_cloud_cost": forecast_cloud_cost,
                "forecast_local_cost": forecast_local_cost,
                "forecast_total_cost": forecast_total_cost,
            },
            "usage_summary": summary,
            "budget": {
                "cloud_monthly_budget_usd": float(self.cloud_monthly_budget_usd),
                "forecast_ratio": round((forecast_total_cost / float(self.cloud_monthly_budget_usd)), 4)
                if float(self.cloud_monthly_budget_usd) > 0
                else 0.0,
            },
        }

    def acknowledge_ops_alert(self, code: str, actor: str = "owner", note: str = "") -> dict:
        """Помечает alert как подтверждённый оператором."""
        normalized_code = (code or "").strip()
        if not normalized_code:
            raise ValueError("code_required")

        ack = self._ops_state.setdefault("acknowledged", {})
        ack[normalized_code] = {
            "ts": self._now_iso(),
            "actor": (actor or "owner").strip() or "owner",
            "note": (note or "").strip(),
        }
        self._save_json(self._ops_state_path, self._ops_state)
        return {"ok": True, "code": normalized_code, "ack": ack[normalized_code]}

    def clear_ops_alert_ack(self, code: str) -> dict:
        """Снимает подтверждение alert кода."""
        normalized_code = (code or "").strip()
        if not normalized_code:
            raise ValueError("code_required")

        ack = self._ops_state.setdefault("acknowledged", {})
        existed = normalized_code in ack
        ack.pop(normalized_code, None)
        self._save_json(self._ops_state_path, self._ops_state)
        return {"ok": True, "code": normalized_code, "removed": existed}

    def get_ops_history(self, limit: int = 30) -> dict:
        """Возвращает историю ops snapshot-ов."""
        safe_limit = max(1, min(int(limit), 200))
        history = self._ops_state.get("history", []) if isinstance(self._ops_state, dict) else []
        if not isinstance(history, list):
            history = []
        return {
            "items": history[-safe_limit:],
            "count": min(len(history), safe_limit),
            "total": len(history),
        }

    def get_ops_report(self, history_limit: int = 20, monthly_calls_forecast: int | None = None) -> dict:
        """
        Возвращает единый ops-отчет для API/команд:
        usage + alerts + costs + history.
        """
        forecast = int(monthly_calls_forecast) if monthly_calls_forecast is not None else int(self.monthly_calls_forecast)
        usage = self.get_usage_summary()
        alerts = self.get_ops_alerts()
        costs = self.get_cost_report(monthly_calls_forecast=forecast)
        history = self.get_ops_history(limit=history_limit)
        return {
            "generated_at": self._now_iso(),
            "usage": usage,
            "alerts": alerts,
            "costs": costs,
            "history": history,
        }

    def get_ops_executive_summary(self, monthly_calls_forecast: int | None = None) -> dict:
        """
        Возвращает компактный executive summary для оператора:
        KPI, риски и рекомендации в одном объекте.
        """
        forecast = int(monthly_calls_forecast) if monthly_calls_forecast is not None else int(self.monthly_calls_forecast)
        usage = self.get_usage_summary()
        alerts_payload = self.get_ops_alerts()
        alerts = alerts_payload.get("alerts", [])
        costs = self.get_cost_report(monthly_calls_forecast=forecast)

        totals = usage.get("totals", {})
        ratios = usage.get("ratios", {})
        soft_cap = usage.get("soft_cap", {})
        budget = costs.get("budget", {})
        monthly = costs.get("monthly_forecast", {})

        severities = [str(item.get("severity", "low")).lower() for item in alerts if isinstance(item, dict)]
        risk_level = "low"
        if "high" in severities:
            risk_level = "high"
        elif "medium" in severities:
            risk_level = "medium"

        recommendations: list[str] = []
        cloud_share = float(ratios.get("cloud_share", 0.0))
        budget_ratio = float(budget.get("forecast_ratio", 0.0))
        alert_codes = {
            str(item.get("code", ""))
            for item in alerts
            if isinstance(item, dict)
        }
        if bool(soft_cap.get("cloud_soft_cap_reached")):
            recommendations.append("Снизить cloud-нагрузку: увести non-critical профили в local.")
        elif cloud_share >= 0.75:
            recommendations.append("Пересмотреть профили routing policy: уменьшить долю cloud.")
        if budget_ratio >= 1.0:
            recommendations.append("Срочно пересмотреть месячный forecast/budget или понизить cloud тариф задач.")
        elif budget_ratio >= 0.9:
            recommendations.append("Бюджет на грани: применить throttling дорогих cloud прогонов.")
        if "model_quality_degraded" in alert_codes:
            recommendations.append("Обновить модельные пресеты: у части моделей устойчиво низкий feedback.")
        if int(totals.get("local_calls", 0)) == 0 and int(totals.get("cloud_calls", 0)) > 0:
            recommendations.append("Проверить LM Studio/Ollama: локальный канал сейчас не используется.")
        if not recommendations:
            recommendations.append("Контур стабильный: поддерживать текущую policy и мониторинг.")

        return {
            "generated_at": self._now_iso(),
            "risk_level": risk_level,
            "kpi": {
                "calls_total": int(totals.get("all_calls", 0)),
                "cloud_share": cloud_share,
                "forecast_total_cost": float(monthly.get("forecast_total_cost", 0.0)),
                "budget_ratio": budget_ratio,
                "active_alerts": len(alerts),
            },
            "alerts_brief": [
                {
                    "severity": str(item.get("severity", "info")),
                    "code": str(item.get("code", "")),
                    "acknowledged": bool(item.get("acknowledged", False)),
                }
                for item in alerts[:8]
                if isinstance(item, dict)
            ],
            "recommendations": recommendations[:6],
        }

    def prune_ops_history(self, max_age_days: int = 30, keep_last: int = 100) -> dict:
        """
        Очищает историю ops snapshot:
        - удаляет записи старше max_age_days,
        - но сохраняет минимум keep_last последних записей.
        """
        safe_age_days = max(1, int(max_age_days))
        safe_keep_last = max(1, int(keep_last))
        history = self._ops_state.get("history", []) if isinstance(self._ops_state, dict) else []
        if not isinstance(history, list):
            history = []

        before_count = len(history)
        if before_count == 0:
            return {
                "ok": True,
                "before": 0,
                "after": 0,
                "removed": 0,
                "max_age_days": safe_age_days,
                "keep_last": safe_keep_last,
            }

        cutoff_ts = datetime.now(timezone.utc).timestamp() - (safe_age_days * 86400)
        forced_keep_indices = set(range(max(0, before_count - safe_keep_last), before_count))
        kept: list[dict[str, Any]] = []

        for idx, item in enumerate(history):
            if idx in forced_keep_indices:
                kept.append(item)
                continue
            ts_raw = str(item.get("ts", "")).strip()
            if not ts_raw:
                continue
            ts_norm = ts_raw.replace("Z", "+00:00")
            try:
                item_ts = datetime.fromisoformat(ts_norm).timestamp()
            except Exception:
                # Некорректные timestamp-ы убираем при очистке.
                continue
            if item_ts >= cutoff_ts:
                kept.append(item)

        self._ops_state["history"] = kept
        self._save_json(self._ops_state_path, self._ops_state)
        after_count = len(kept)
        return {
            "ok": True,
            "before": before_count,
            "after": after_count,
            "removed": max(0, before_count - after_count),
            "max_age_days": safe_age_days,
            "keep_last": safe_keep_last,
        }

    def _append_ops_history(self, payload: dict) -> None:
        """Сохраняет краткий snapshot ops-алертов в историю."""
        history = self._ops_state.setdefault("history", [])
        if not isinstance(history, list):
            self._ops_state["history"] = []
            history = self._ops_state["history"]

        alerts = payload.get("alerts", []) if isinstance(payload, dict) else []
        snapshot = {
            "ts": self._now_iso(),
            "status": str(payload.get("status", "unknown")),
            "alerts_count": len(alerts) if isinstance(alerts, list) else 0,
            "codes": [str(item.get("code", "")) for item in (alerts or []) if isinstance(item, dict)],
            "cloud_calls": int(payload.get("summary", {}).get("totals", {}).get("cloud_calls", 0)),
            "local_calls": int(payload.get("summary", {}).get("totals", {}).get("local_calls", 0)),
        }
        history.append(snapshot)
        if len(history) > 500:
            del history[: len(history) - 500]
        self._save_json(self._ops_state_path, self._ops_state)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

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
