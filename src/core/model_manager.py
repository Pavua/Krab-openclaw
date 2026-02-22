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
import difflib
import aiohttp
from pathlib import Path
import re
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Literal, Optional, Dict, Any, List, Set, AsyncGenerator
# from src.core.rag_engine import RAGEngine # Deprecated

# Настройка логгера
import structlog
logger = structlog.get_logger("ModelRouter")

from src.core.openclaw_client import OpenClawClient
from src.core.agent_swarm import SwarmManager
from src.core.stream_client import OpenClawStreamClient, StreamFailure
from src.core.cost_engine import CostEngine

class ModelRouter:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.lm_studio_url = config.get("LM_STUDIO_URL", "http://localhost:1234/v1").rstrip("/")
        if "/v1" not in self.lm_studio_url:
            self.lm_studio_url += "/v1"

        self.ollama_url = config.get("OLLAMA_URL", "http://localhost:11434/api")
        self.gemini_key = config.get("GEMINI_API_KEY")

        # Статусы доступности
        self.is_local_available = False
        self.local_engine = None  # 'lm-studio' or 'ollama'
        self.active_local_model = None

        # Кеш для health-check (чтобы не дёргать API на каждый запрос).
        # Важно: слишком частый опрос `/api/v1/models` может сбивать idle-TTL LM Studio.
        self._health_cache_ts = 0
        try:
            self._health_cache_ttl = max(5, int(config.get("LOCAL_HEALTH_CACHE_TTL_SEC", 30)))
        except (ValueError, TypeError):
            self._health_cache_ttl = 30

        # Режим health-check:
        # - "light": фоново проверяем только доступность сервера (без скана моделей),
        #            а полный скан моделей делаем редко.
        # - "models": всегда проверяем через /api/v1/models (старое поведение).
        self._health_probe_mode = str(config.get("LOCAL_HEALTH_PROBE_MODE", "light")).strip().lower()
        if self._health_probe_mode not in {"light", "models"}:
            self._health_probe_mode = "light"
        self._health_full_scan_ts = 0
        try:
            self._health_full_scan_interval = max(
                60,
                int(config.get("LOCAL_HEALTH_FULL_SCAN_SECONDS", 3600)),
            )
        except (ValueError, TypeError):
            self._health_full_scan_interval = 3600

        # OpenClaw Client (Cloud Model Gateway)
        self.openclaw_client = OpenClawClient(
            base_url=config.get("OPENCLAW_BASE_URL", "http://localhost:18789"),
            api_key=config.get("OPENCLAW_API_KEY")
        )
        # Stream Client для WebSocket/SSE
        self.stream_client = OpenClawStreamClient(
            base_url=self.lm_studio_url,
            api_key="none"
        )
        logger.info("☁️ OpenClaw & Stream Clients configured")

        # RAG Engine (Deprecated, use OpenClaw)
        self.rag = None # RAGEngine()

        # Persona Manager (назначается в main.py)
        self.persona = None
        self.tools = None  # Назначается в main.py (ToolHandler)

        # Agent Swarm Manager
        self.swarm = SwarmManager(model_router=self)

        # Пул моделей — читаем из .env, дефолты как fallback
        self.models = {
            "chat": config.get("GEMINI_CHAT_MODEL", "gemini-2.0-flash"),
            "thinking": config.get("GEMINI_THINKING_MODEL", "gemini-2.0-flash-thinking-exp-01-21"),
            "pro": config.get("GEMINI_PRO_MODEL", "gemini-3-pro-preview"),
            "coding": config.get("GEMINI_CODING_MODEL", "gemini-2.0-flash"),
        }
        # Контекстные cloud-модели (опционально).
        # Если переменная пустая — используем базовые self.models[*].
        self.chat_model_group = str(config.get("GEMINI_CHAT_MODEL_GROUP", "")).strip()
        self.chat_model_owner_private = str(config.get("GEMINI_CHAT_MODEL_OWNER_PRIVATE", "")).strip()
        self.chat_model_owner_private_important = str(
            config.get("GEMINI_CHAT_MODEL_OWNER_PRIVATE_IMPORTANT", "")
        ).strip()
        self.owner_private_always_pro = str(
            config.get("MODEL_OWNER_PRIVATE_ALWAYS_PRO", "0")
        ).strip().lower() in {"1", "true", "yes", "on"}

        # [R11] Cost Engine для контроля бюджета
        self.cost_engine = CostEngine(config)

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
            "gemini-1.5-flash",         # Проверенный fallback
            "gemini-1.5-pro"            # Стабильная pro
        ]
        
        # Предпочтительная локальная модель (из .env) — если указана,
        # _ensure_chat_model_loaded() будет пытаться загрузить именно её,
        # а не первую попавшуюся LLM (что приводило к дефолту на qwen 7b).
        self.local_preferred_model = config.get("LOCAL_PREFERRED_MODEL", "").strip()
        # Модель для кодинга (если отличается от chat-модели)
        self.local_coding_model = config.get("LOCAL_CODING_MODEL", "").strip()
        # Защита от шторма автозагрузки локальной модели:
        # если LM Studio жива, но модель не загружена, пробуем auto-load
        # не чаще заданного интервала.
        try:
            self.local_autoload_cooldown_sec = max(
                5,
                int(config.get("LOCAL_AUTOLOAD_COOLDOWN_SEC", 30)),
            )
        except Exception:
            self.local_autoload_cooldown_sec = 30
        self._last_local_autoload_ts = 0.0

        # ═══════════════════════════════════════════════════════════════
        # [PHASE 15.1] Context Window Manager Metadata
        # Лимиты токенов для разных моделей (входной контекст)
        self.CONTEXT_WINDOWS = {
            "gemini-2.0-flash": 1048576,
            "gemini-2.0-pro-exp": 2097152,
            "gemini-1.5-pro": 2097152,
            "gemini-1.5-flash": 1048576,
            "gpt-4": 128000,
            "qwen": 32768,
            "llama-3": 8192,
            "mistral": 32768,
            "deepseek": 64000,
            "default": 8192
        }

        # Smart Memory Planner: управление RAM и авто-загрузка/выгрузка
        # ═══════════════════════════════════════════════════════════════
        try:
            self.max_ram_gb = float(config.get("MAX_RAM_GB", 36))
        except (ValueError, TypeError):
            self.max_ram_gb = 36.0
        try:
            self.lm_studio_max_ram_gb = float(config.get("LM_STUDIO_MAX_RAM_GB", self.max_ram_gb * 0.5))
        except (ValueError, TypeError):
            self.lm_studio_max_ram_gb = self.max_ram_gb * 0.5
        try:
            self.auto_unload_idle_min = int(config.get("AUTO_UNLOAD_IDLE_MIN", 30))
        except (ValueError, TypeError):
            self.auto_unload_idle_min = 30

        # LRU-трекер: {model_id: timestamp последнего использования}
        self._model_last_used: Dict[str, float] = {}

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
        # Точечные ориентиры стоимости по типам облачных моделей (для runway-планирования).
        # Можно переопределять в .env для твоего реального тарифа.
        try:
            self.model_cost_flash_lite_usd = float(config.get("MODEL_COST_FLASH_LITE_USD", self.cloud_cost_per_call_usd * 0.7))
        except Exception:
            self.model_cost_flash_lite_usd = float(self.cloud_cost_per_call_usd * 0.7)
        try:
            self.model_cost_flash_usd = float(config.get("MODEL_COST_FLASH_USD", self.cloud_cost_per_call_usd))
        except Exception:
            self.model_cost_flash_usd = float(self.cloud_cost_per_call_usd)
        try:
            self.model_cost_pro_usd = float(config.get("MODEL_COST_PRO_USD", self.cloud_cost_per_call_usd * 3.0))
        except Exception:
            self.model_cost_pro_usd = float(self.cloud_cost_per_call_usd * 3.0)
        try:
            self.monthly_calls_forecast = int(config.get("MONTHLY_CALLS_FORECAST", 5000))
        except Exception:
            self.monthly_calls_forecast = 5000

        # Политика локального параллелизма: 1 heavy + 1 light.
        self._local_heavy_slot = asyncio.Semaphore(1)
        self._local_light_slot = asyncio.Semaphore(1)

        self.local_timeout_seconds = float(config.get("LOCAL_CHAT_TIMEOUT_SECONDS", 900))
        self.local_include_reasoning = str(config.get("LOCAL_INCLUDE_REASONING", "1")).strip().lower() in {
            "1", "true", "yes", "on"
        }
        try:
            self.local_reasoning_max_chars = int(config.get("LOCAL_REASONING_MAX_CHARS", 2000))
            if self.local_reasoning_max_chars < 200:
                self.local_reasoning_max_chars = 200
        except Exception:
            self.local_reasoning_max_chars = 2000
        try:
            self.local_stream_total_timeout_seconds = float(
                config.get("LOCAL_STREAM_TOTAL_TIMEOUT_SECONDS", 75.0)
            )
            if self.local_stream_total_timeout_seconds <= 0:
                self.local_stream_total_timeout_seconds = 75.0
        except Exception:
            self.local_stream_total_timeout_seconds = 75.0
        try:
            self.local_stream_sock_read_timeout_seconds = float(
                config.get("LOCAL_STREAM_SOCK_READ_TIMEOUT_SECONDS", 20.0)
            )
            if self.local_stream_sock_read_timeout_seconds <= 0:
                self.local_stream_sock_read_timeout_seconds = 20.0
        except Exception:
            self.local_stream_sock_read_timeout_seconds = 20.0
        self.local_stream_fallback_to_cloud = str(
            config.get("LOCAL_STREAM_FALLBACK_TO_CLOUD", "1")
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.last_cloud_error: Optional[str] = None
        self.last_cloud_model: Optional[str] = None
        self.last_local_load_error: Optional[str] = None
        self.last_local_load_error_human: Optional[str] = None
        self.lms_gpu_offload = str(config.get("LM_STUDIO_GPU_OFFLOAD", "")).strip().lower()
        self.cloud_priority_models = self._parse_cloud_priority(config.get(
            "MODEL_CLOUD_PRIORITY_LIST",
            "gemini-2.5-flash,gemini-2.5-pro,google/gemini-2.5-flash,google/gemini-2.5-pro,openai/gpt-4o-mini"
        ))
        # Ограничение длины cloud-ротации, чтобы не зависать на десятках кандидатов.
        # Особенно критично при force_cloud и сетевых деградациях.
        try:
            self.cloud_max_candidates_per_request = max(
                1,
                int(config.get("MODEL_CLOUD_MAX_CANDIDATES_PER_REQUEST", 4)),
            )
        except Exception:
            self.cloud_max_candidates_per_request = 4
        try:
            self.cloud_max_candidates_force_cloud = max(
                1,
                int(config.get("MODEL_CLOUD_MAX_CANDIDATES_FORCE_CLOUD", 3)),
            )
        except Exception:
            self.cloud_max_candidates_force_cloud = 3
        # Fail-fast guardrails для cloud-вызовов:
        # ограничиваем длительность одного HTTP-запроса и общий бюджет времени
        # для force_cloud-ветки, чтобы user не видел вечное «🤔 Думаю...».
        try:
            self.cloud_request_timeout_seconds = max(
                5,
                int(config.get("CLOUD_REQUEST_TIMEOUT_SECONDS", 22)),
            )
        except Exception:
            self.cloud_request_timeout_seconds = 22
        try:
            self.cloud_fail_fast_budget_seconds = max(
                1,
                int(config.get("CLOUD_FAIL_FAST_BUDGET_SECONDS", 40)),
            )
        except Exception:
            self.cloud_fail_fast_budget_seconds = 40
        # Provider probe может быть полезен для диагностики, но тормозит прод-ответы.
        # По умолчанию выключен в обычном chat-контуре.
        self.cloud_probe_on_chat_error = str(
            config.get("CLOUD_PROVIDER_PROBE_ON_CHAT_ERROR", "0")
        ).strip().lower() in {"1", "true", "yes", "on"}

        # R15: Cloud Preflight Cache (provider -> (expiration_ts, error_msg))
        self._preflight_cache: dict[str, tuple[float, str]] = {}
        self._preflight_ttl_seconds = 300  # 5 минут блокировки при фатальной ошибке

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
        # Последний успешный stream-маршрут (отдельно от route_query/route_tool).
        self._last_stream_route: Dict[str, Any] = {}
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

    @staticmethod
    def _normalize_chat_role(raw_role: str | None) -> str:
        """
        Нормализует произвольные роли контекста в допустимый набор OpenAI/LM Studio:
        user | assistant | system | tool.
        """
        role = str(raw_role or "user").strip().lower()
        if role in {"user", "assistant", "system", "tool"}:
            return role
        if role in {"model", "ai", "bot", "assistant_reply", "vision_analysis"}:
            return "assistant"
        if role in {"context", "memory", "note", "analysis"}:
            return "system"
        return "user"

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

    def _normalize_cloud_model_name(self, model_name: Optional[str]) -> str:
        """
        Нормализует cloud model id:
        - снимает префикс `models/`,
        - убирает нестабильные `-exp` (кроме thinking),
        - подставляет стабильный chat-маршрут по умолчанию.
        """
        if not model_name:
            return ""
        normalized = str(model_name).strip()
        if not normalized:
            return ""

        if normalized.startswith("models/"):
            normalized = normalized.split("models/", 1)[1].strip()

        lowered = normalized.lower()
        if "-exp" in lowered and "thinking" not in lowered:
            return self.models.get("chat", "gemini-2.5-flash")

        return normalized

    def _sanitize_model_text(self, text: Optional[str]) -> str:
        """
        Удаляет служебные маркеры модели и подчищает форматирование
        перед отправкой ответа в Telegram/память.
        """
        if not text:
            return ""

        cleaned = str(text)

        # Удаляем только служебные маркеры box, но не полезный текст внутри.
        cleaned = cleaned.replace("<|begin_of_box|>", "")
        cleaned = cleaned.replace("<|end_of_box|>", "")
        # [HOTFIX] Удаляем оставшиеся тех-теги формата <|...|>.
        cleaned = re.sub(r"<\|[^|>]+?\|>", "", cleaned)
        cleaned = cleaned.replace("</s>", "").replace("<s>", "")

        # Точечная фильтрация строк с утечкой служебных артефактов.
        blocked_fragments = (
            "begin_of_box",
            "end_of_box",
            "no_reply",
            "heartbeat_ok",
            "i will now call the",
            "memory_get",
            "memory_search",
            "sessions_spawn",
            "session_send",
            "sessions_send",
            "\"action\": \"sessions_send\"",
            "\"action\":\"sessions_send\"",
            "\"sessionkey\"",
            "\"default channel",
            "## /users/",
            "# agents.md - workspace agents",
            "## agent list",
            "### default agents",
            "</tool_call>",
            "```json",
        )
        filtered_lines: list[str] = []
        for line in cleaned.splitlines():
            low = line.strip().lower()
            if low in {"```", "```json", "```text", "```yaml"}:
                continue
            if any(fragment in low for fragment in blocked_fragments):
                continue
            filtered_lines.append(line)
        cleaned = "\n".join(filtered_lines)

        # Схлопываем подряд идущие дубли строк.
        deduped_lines: list[str] = []
        last_norm = ""
        repeat_count = 0
        for line in cleaned.splitlines():
            normalized = re.sub(r"\s+", " ", line).strip().lower()
            if normalized and normalized == last_norm:
                repeat_count += 1
            else:
                last_norm = normalized
                repeat_count = 1
            if repeat_count <= 2:
                deduped_lines.append(line)
        cleaned = "\n".join(deduped_lines)

        # Финальная нормализация пустых строк и краёв.
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned

    def _is_local_only_model_identifier(self, model_name: Optional[str]) -> bool:
        """
        Эвристика: определяет явно локальные ID, которые не стоит пробовать в cloud.
        """
        if not model_name:
            return False
        lowered = model_name.strip().lower()
        if not lowered:
            return False
        local_markers = (
            "-mlx",
            "_mlx",
            ".mlx",
            ".gguf",
            "gguf",
            "q4_",
            "q5_",
            "q6_",
            "q8_",
            "lm-studio",
            "ollama",
            "local-model",
        )
        return any(marker in lowered for marker in local_markers)

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

    def _is_lmstudio_model_loaded(self, entry: Dict[str, Any]) -> bool:
        """
        Определяет признак загруженной модели LM Studio.

        Почему так:
        В разных версиях LM Studio loaded-статус приходит в разных полях
        (`loaded_instances`, `loaded`, `state`, `status`, `availability`).
        Если читать только одно поле, !status может ошибочно показывать
        `no_model_loaded`, хотя модель уже отвечает в /chat/completions.
        """
        if not isinstance(entry, dict):
            return False

        loaded_instances = entry.get("loaded_instances")
        if isinstance(loaded_instances, list) and len(loaded_instances) > 0:
            return True

        explicit_bool = entry.get("loaded")
        if isinstance(explicit_bool, bool):
            return explicit_bool

        state_fields = []
        for key in ("state", "status", "availability"):
            raw = entry.get(key)
            if raw is None:
                continue
            state_fields.append(str(raw).strip().lower())

        positive_tokens = {"ready", "loaded", "active", "running", "online"}
        negative_tokens = {"unloaded", "not_loaded", "not loaded", "idle_unloaded", "evicted", "offline"}

        for state in state_fields:
            if state in positive_tokens:
                return True
            if state in negative_tokens:
                return False

        return False

    def _is_runtime_error_message(self, text: Optional[str]) -> bool:
        """
        Определяет, является ли ответ (от Cloud или Local) явной ошибкой рантайма.
        [R12] Unified detector for local/cloud errors.
        """
        if not text:
            return True
        lowered = text.strip().lower()
        
        # Общие паттерны ошибок
        error_indicators = (
            "❌", "⚠️", "llm error", "error:", "exception:", "crashed",
            "connection refused", "connection error", "network error",
            "failed to fetch", "internal server error",
            "500 internal", "502 bad gateway", "503 service unavailable", "504 gateway timeout",
            "failed to connect", "upstream connect error", "socket timeout",
            "read timeout", "timed out", "empty response", "no response"
        )
        if any(indicator in lowered for indicator in error_indicators):
            return True

        # Локальные специфичные ошибки (LM Studio / Ollama)
        local_errors = (
            "no models loaded", "please load a model", "failed to load model",
            "not loaded", "is not found", "model not active", "server is initializing",
            "insufficient vram", "out of memory"
        )
        if any(err in lowered for err in local_errors):
            return True

        # Cloud специфичные ошибки (Gemini / OpenClaw)
        cloud_errors = (
            "billing error", "out of credits", "quota exceeded", "api key invalid",
            "safety filter", "blocked by safety", "finish_reason: safety",
            "401 unauthorized", "403 forbidden", "429 too many requests"
        )
        if any(err in lowered for err in cloud_errors):
            return True

        # JSON ошибки
        if lowered.startswith("{") and '"error"' in lowered:
            return True

        return False

    def _is_cloud_error_message(self, text: Optional[str]) -> bool:
        """Обратная совместимость для существующих вызовов."""
        return self._is_runtime_error_message(text)

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

    def _is_fatal_cloud_auth_error(self, text: Optional[str]) -> bool:
        """
        Определяет фатальные cloud-ошибки, при которых нет смысла перебирать
        остальные модели этого же провайдера в текущем запросе.
        """
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False

        fatal_markers = (
            "unauthorized",
            "invalid api key",
            "incorrect api key",
            "api key was reported as leaked",
            "permission_denied",
            "forbidden",
            "generative language api has not been used",
            "api has not been used in project",
            "it is disabled",
            "enable it by visiting",
            "quota exceeded",
            "out of credits",
            "insufficient balance",
            "billing error",
        )
        return any(marker in lowered for marker in fatal_markers)

    def _classify_cloud_error(self, text: Optional[str]) -> dict:
        """
        Классифицирует cloud-ошибку в стабильный код + человекочитаемую сводку.
        Используется в !status/!diagnose и в user-facing fallback-ответах.
        """
        lowered = str(text or "").strip().lower()
        if not lowered:
            return {
                "code": "none",
                "summary": "облачный провайдер временно недоступен",
                "retryable": True,
            }

        if "reported as leaked" in lowered:
            return {
                "code": "api_key_leaked",
                "summary": "ключ провайдера помечен как скомпрометированный (leaked) — нужен новый API key",
                "retryable": False,
            }
        if "invalid api key" in lowered or "incorrect api key" in lowered:
            return {
                "code": "api_key_invalid",
                "summary": "API key провайдера невалидный",
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
                "summary": "Generative Language API не включён в Google Cloud проекте для этого ключа",
                "retryable": False,
            }
        if "permission_denied" in lowered or "403" in lowered:
            return {
                "code": "permission_denied",
                "summary": "доступ к облачной модели отклонён провайдером (403)",
                "retryable": False,
            }
        if "unauthorized" in lowered or "401" in lowered:
            return {
                "code": "unauthorized",
                "summary": "ошибка авторизации cloud-провайдера (401)",
                "retryable": False,
            }
        
        if "quota" in lowered or "billing" in lowered or "limit" in lowered:
            return {
                "code": "quota",
                "summary": "превышена квота или проблема с биллингом",
                "retryable": False,
            }

        if "not found" in lowered or "404" in lowered:
            return {
                "code": "model_not_found",
                "summary": "модель не найдена в текущем регионе/аккаунте",
                "retryable": False,
            }

        if "fail-fast budget" in lowered:
            return {
                "code": "timeout",
                "summary": "превышено время ожидания ответа от облачного канала",
                "retryable": True,
            }

        if "connection error" in lowered or "timeout" in lowered or "network" in lowered:
            return {
                "code": "network",
                "summary": "проблема с сетью или таймаут соединения",
                "retryable": True,
            }

        return {
            "code": "unknown",
            "summary": lowered[:120] if lowered else "неизвестная ошибка",
            "retryable": True,
        }

    def _check_cloud_preflight(self, provider: str) -> Optional[str]:
        """
        R15: Проверяет, нет ли в кэше активной фатальной ошибки для провайдера.
        Возвращает сообщение об ошибке, если провайдер заблокирован.
        """
        cached = self._preflight_cache.get(provider)
        if not cached:
            return None
        
        expiration, error_msg = cached
        if time.time() < expiration:
            return f"Preflight: провайдер '{provider}' заблокирован (R15 Gate): {error_msg}"
        
        # Истекло
        self._preflight_cache.pop(provider, None)
        return None

    def _categorize_cloud_error(self, text: Optional[str]) -> str:
        """
        Возвращает строковую категорию cloud-ошибки для тестов и логов.

        Категории:
        - auth_fatal    — leaked/invalid key, permission denied, unauthorized
        - api_disabled  — API not enabled in Google Cloud project
        - quota         — quota exceeded, billing, out of credits
        - model_not_found — 404 NOT_FOUND
        - network       — connection error, timeout
        - unknown       — прочее

        Зачем: _classify_cloud_error() возвращает dict — удобно внутри роутера.
        Этот метод нужен для тестов и быстрого if/match в обработчиках.
        """
        info = self._classify_cloud_error(text)
        code = str(info.get("code") or "unknown")
        # Унифицируем несколько кодов под общую категорию auth_fatal.
        if code in {"api_key_leaked", "api_key_invalid", "unauthorized", "permission_denied"}:
            return "auth_fatal"
        if code == "api_disabled":
            return "api_disabled"
        if code in {"quota_or_billing", "quota"}:
            return "quota"
        if code == "model_not_found":
            return "model_not_found"
        if code in {"network_error", "timeout", "network"}:
            return "network"
        return "unknown"

    def _summarize_cloud_error_for_user(self, text: Optional[str]) -> str:
        """
        Возвращает короткую пользовательскую формулировку cloud-ошибки
        без сырого JSON/stacktrace.
        """
        info = self._classify_cloud_error(text)
        return str(info.get("summary") or "облачный провайдер вернул ошибку")

    def get_last_cloud_error_info(self) -> dict:
        """
        Публичный runtime-диагностический объект по последней cloud-ошибке.
        Нужен командам статуса/диагностики и веб-панели.
        """
        raw = str(self.last_cloud_error or "").strip()
        info = self._classify_cloud_error(raw)
        return {
            "has_error": bool(raw),
            "code": str(info.get("code") or "none"),
            "summary": str(info.get("summary") or ""),
            "retryable": bool(info.get("retryable", True)),
            "last_provider_model": str(getattr(self, "last_cloud_model", "") or ""),
            "raw_excerpt": raw[:320] if raw else "",
        }

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
        route_reason: str = "",
        route_detail: str = "",
        force_mode: Optional[str] = None,
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
            "route_reason": (route_reason or "").strip()[:80],
            "route_detail": (route_detail or "").strip()[:240],
            "force_mode": str(force_mode or self.force_mode or "auto").strip() or "auto",
            "local_available": bool(self.is_local_available),
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

    def _remember_last_stream_route(
        self,
        profile: str,
        task_type: str,
        channel: str,
        model_name: str,
        prompt: str = "",
        route_reason: str = "",
        route_detail: str = "",
        force_mode: Optional[str] = None,
    ) -> None:
        """
        Сохраняет метаданные последнего успешного stream-ответа.
        Нужен для быстрой диагностики в !status без чтения логов.
        """
        self._last_stream_route = {
            "ts": self._now_iso(),
            "profile": (profile or "chat").strip().lower() or "chat",
            "task_type": (task_type or "chat").strip().lower() or "chat",
            "channel": self._normalize_channel(channel),
            "model": (model_name or "unknown").strip() or "unknown",
            "prompt_preview": (prompt or "").strip()[:160],
            "route_reason": (route_reason or "").strip()[:80],
            "route_detail": (route_detail or "").strip()[:240],
            "force_mode": str(force_mode or self.force_mode or "auto").strip() or "auto",
            "local_available": bool(self.is_local_available),
        }

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

    def _should_use_pro_for_owner_private(self, prompt: str, chat_type: str, is_owner: bool) -> bool:
        """
        Политика качества для владельца в личке:
        если обсуждение про проект/планирование/критичную работу,
        в cloud-ветке поднимаем приоритет PRO-модели.
        """
        if not is_owner:
            return False
        if (chat_type or "").strip().lower() != "private":
            return False

        text = (prompt or "").lower()
        pro_markers = (
            "проект",
            "план",
            "roadmap",
            "архитект",
            "важн",
            "критич",
            "прод",
            "production",
            "миграц",
            "рефактор",
            "стратег",
            "бюджет",
        )
        return any(marker in text for marker in pro_markers)

    @staticmethod
    def _is_group_chat(chat_type: str) -> bool:
        """
        Определяет групповые типы чатов для отдельной бюджетной модели.
        """
        normalized = (chat_type or "").strip().lower()
        return normalized in {"group", "supergroup"}

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

        # Дефолты — LOCAL FIRST для всех профилей, кроме критичных
        default_model = self.models.get("chat", "gemini-2.0-flash")
        default_channel = "local"  # Local First стратегия

        if profile in {"security", "infra", "review"}:
            default_model = self.models.get("pro", self.models.get("thinking", self.models["chat"]))
            default_channel = "cloud"
        elif profile == "code":
            default_model = self.models.get("coding", self.models["chat"])
            default_channel = "local"
        elif profile == "moderation":
            default_model = self.models.get("chat", "gemini-2.0-flash")
            default_channel = "local"
        elif profile == "chat":
            # Обычный чат — ВСЕГДА local first
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

        # Для критичных профилей — routing_memory имеет приоритет.
        # Для обычных (chat, code, moderation) — default_channel важнее,
        # чтобы стратегия Local First соблюдалась.
        is_critical_profile = profile in {"security", "infra", "review"}
        resolved_channel = (
            (top_channel or default_channel) if is_critical_profile
            else default_channel
        )

        return {
            "profile": profile,
            "model": selected_model,
            "channel": resolved_channel,
            "critical": self._is_critical_profile(profile),
            "feedback_hint": {
                "avg_score": feedback_hint.get("avg", 0.0),
                "count": feedback_hint.get("count", 0),
            },
        }

    def _resolve_cloud_model(
        self,
        task_type: str,
        profile: str,
        preferred_model: Optional[str] = None,
        chat_type: str = "private",
        is_owner: bool = False,
        prompt: str = "",
    ) -> str:
        """Выбирает облачную модель с учетом профиля и предпочтений."""
        if preferred_model and "gemini" in preferred_model:
            return preferred_model
        if profile in {"security", "infra", "review"}:
            return self.models.get("pro", self.models.get("thinking", self.models["chat"]))
        if profile == "code":
            return self.models.get("coding", self.models["chat"])
        if task_type == "reasoning":
            return self.models.get("thinking", self.models["chat"])
        # Для owner/private держим приоритет качества:
        # - важные запросы в pro,
        # - при явном флаге always_pro — всегда pro в личке владельца.
        if is_owner and (chat_type or "").strip().lower() == "private":
            if self.owner_private_always_pro:
                return self.models.get("pro", self.models["chat"])
            if self._should_use_pro_for_owner_private(prompt, chat_type, is_owner):
                return self.chat_model_owner_private_important or self.models.get("pro", self.models["chat"])
            if self.chat_model_owner_private:
                return self.chat_model_owner_private

        # Для групповых чатов можно выделить отдельную более бюджетную модель.
        if self._is_group_chat(chat_type) and self.chat_model_group:
            return self.chat_model_group

        return self.models.get(task_type, self.models["chat"])

    def _build_cloud_candidates(
        self,
        task_type: str,
        profile: str,
        preferred_model: Optional[str] = None,
        chat_type: str = "private",
        is_owner: bool = False,
        prompt: str = "",
    ) -> List[str]:
        """
        Формирует последовательность моделей для cloud-подсистемы.
        """
        base = self._resolve_cloud_model(
            task_type=task_type,
            profile=profile,
            preferred_model=preferred_model,
            chat_type=chat_type,
            is_owner=is_owner,
            prompt=prompt,
        )
        candidates: list[str] = []
        seen: Set[str] = set()

        def add(model_name: Optional[str]) -> None:
            if not model_name:
                return
            normalized = self._normalize_cloud_model_name(model_name)
            if not normalized or normalized in seen:
                return
            if self._is_local_only_model_identifier(normalized):
                logger.info("Пропускаю локальный model_id в cloud candidate list", model=normalized, profile=profile)
                return
            seen.add(normalized)
            candidates.append(normalized)

        add(preferred_model or "")
        add(base)
        for extra in self.cloud_priority_models:
            add(extra)

        max_candidates = (
            int(self.cloud_max_candidates_force_cloud)
            if self.force_mode == "force_cloud"
            else int(self.cloud_max_candidates_per_request)
        )
        if len(candidates) > max_candidates:
            dropped = candidates[max_candidates:]
            candidates = candidates[:max_candidates]
            logger.info(
                "Cloud candidate list truncated",
                max_candidates=max_candidates,
                force_mode=self.force_mode,
                kept=candidates,
                dropped_count=len(dropped),
            )

        return candidates

    async def unload_models_manual(self) -> bool:
        """
        [R11] Принудительная выгрузка всех локальных моделей (для освобождения RAM).
        Используется Watchdog-ом при критической нехватке памяти.
        """
        logger.warning("🚨 Manual model unload requested (Soft Healing)")
        base_root = self._lm_studio_api_root()
        url = f"{base_root}/api/v1/models/unload"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"all": True}, timeout=10) as resp:
                    if resp.status == 200:
                        logger.info("✅ All local models unloaded successfully")
                        self.active_local_model = None
                        self.is_local_available = False
                        return True
                    else:
                        logger.error(f"❌ Failed to unload models: status {resp.status}")
        except Exception as e:
            logger.error(f"❌ Error during model unload: {e}")
        return False

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

        # В light-режиме не трогаем /models на каждом health-check,
        # чтобы не мешать idle-unload в LM Studio.
        need_full_scan = (
            force
            or self._health_probe_mode == "models"
            or self._health_full_scan_ts <= 0
            or (now - self._health_full_scan_ts) >= self._health_full_scan_interval
        )
        if not need_full_scan and self._health_probe_mode == "light":
            lm_server_alive = await self._light_ping_local_server(base_root)
            if lm_server_alive:
                # Сервер жив — сохраняем предыдущее состояние локалки без скана моделей.
                # Детальный пересчёт loaded-моделей произойдёт на force-check или редком full-scan.
                return self.is_local_available
            logger.warning("Light health probe: LM Studio server недоступен, делаю fallback-проверку.")

        if need_full_scan:
            self._health_full_scan_ts = now

        # Сначала проверяем, есть ли РЕАЛЬНО загруженная модель через /api/v1/models
        # (в 0.3.x загруженные модели имеют специфические поля или это единственный способ)
        try:
            models = await self._scan_local_models()
            loaded_models = [m for m in models if m.get("loaded")]
            
            if loaded_models:
                self.local_engine = "lm-studio"
                self.is_local_available = True
                self.active_local_model = loaded_models[0]["id"]
                self.last_local_load_error = None
                self.last_local_load_error_human = None
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
                        self.last_local_load_error = "no_model_loaded"
                        self.last_local_load_error_human = "⚠️ LM Studio доступна, но ни одна модель не загружена."
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
                            self.last_local_load_error = None
                            self.last_local_load_error_human = None
                            return True
        except Exception:
            pass

        self.is_local_available = False
        self.local_engine = None
        self.active_local_model = None
        if not self.last_local_load_error:
            self.last_local_load_error = "local_engine_unreachable"
        if not self.last_local_load_error_human:
            self.last_local_load_error_human = "⚠️ Локальный движок недоступен (LM Studio/Ollama unreachable)."
        return False

    async def _light_ping_local_server(self, base_root: str) -> bool:
        """
        Лёгкий probe доступности LM Studio без сканирования списка моделей.

        Почему так:
        - `/api/v1/models` полезен для диагностики, но слишком частый вызов
          может мешать авто-выгрузке по idle TTL;
        - здесь проверяем только «сервер жив / сервер мёртв» через штатные endpoint.
        """
        timeout = aiohttp.ClientTimeout(total=2)
        # Не используем /health: в ряде версий LM Studio это шумит в логах
        # сообщением "Unexpected endpoint or method".
        probe_paths = ("/v1/models", "/api/v1/models", "/")
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for path in probe_paths:
                    try:
                        async with session.get(f"{base_root}{path}") as resp:
                            if resp.status < 500:
                                return True
                    except Exception:
                        continue
        except Exception:
            return False
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
                            # LM Studio 0.3.x: /api/v1/models → {"models": [...]}
                            # OpenAI compat:    /v1/models    → {"data": [...]}
                            normalized = payload.get("models") or payload.get("data") or []
                        elif isinstance(payload, list):
                            normalized = payload

                        models = []
                        for m in normalized:
                            # LM Studio 0.3.x использует "key" как ID модели
                            identifier = m.get("key") or self._extract_model_id(m) or m.get("id", "")
                            if not identifier: continue
                            
                            # В LM Studio поля loaded-статуса зависят от версии API.
                            is_loaded = self._is_lmstudio_model_loaded(m)
                            
                            # Определяем тип по полю "type" из API или по имени
                            model_type = m.get("type", "")
                            if model_type == "embedding" or "embedding" in identifier.lower():
                                mtype = "embedding"
                            else:
                                mtype = "llm"
                            
                            models.append({
                                "id": identifier,
                                "type": mtype,
                                "name": m.get("display_name", m.get("name", identifier)),
                                "loaded": is_loaded,
                                # Размер берём из наиболее вероятных полей LM Studio/OpenAI-совместимого ответа.
                                "size_bytes": (
                                    m.get("size_on_disk")
                                    or m.get("size_bytes")
                                    or m.get("size")
                                    or 0
                                ),
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
        Пытается загрузить LLM модель через REST API.
        Приоритет: LOCAL_PREFERRED_MODEL → instruct/chat → любая LLM.
        """
        # Сначала проверяем текущий статус — может, уже загружена нужная модель
        if await self.check_local_health(force=True):
            if self.active_local_model and "embed" not in self.active_local_model.lower():
                return True

        models = await self._scan_local_models()
        llm_models = [m for m in models if m["type"] == "llm"]

        if not llm_models:
            logger.warning("⚠️ Нет LLM-моделей в LM Studio для загрузки.")
            return False

        chat_candidate = None

        # Приоритет 1: preferred model из конфига (LOCAL_PREFERRED_MODEL)
        # — решает проблему дефолта на qwen 7b
        if self.local_preferred_model:
            matching = [
                m["id"] for m in llm_models
                if self.local_preferred_model.lower() in m["id"].lower()
            ]
            if matching:
                chat_candidate = matching[0]
                logger.info(f"⭐ Выбрана preferred модель: {chat_candidate}")

        # Приоритет 2: instruct/chat модели (обычно лучше для диалога)
        if not chat_candidate:
            for m in llm_models:
                mid = m["id"].lower()
                if "instruct" in mid or "chat" in mid:
                    chat_candidate = m["id"]
                    logger.info(f"🔄 Выбрана instruct/chat модель: {chat_candidate}")
                    break

        # Приоритет 3: любая LLM (fallback)
        if not chat_candidate:
            chat_candidate = llm_models[0]["id"]
            logger.info(f"🔄 Fallback на первую LLM: {chat_candidate}")

        return await self._smart_load(chat_candidate, reason="ensure_chat")

    async def _maybe_autoload_local_model(self, reason: str = "") -> bool:
        """
        Пытается автоматически загрузить локальную модель, если:
        - режим не force_cloud,
        - LM Studio доступна, но loaded-модели нет.
        """
        if self.force_mode == "force_cloud":
            return False
        if self.local_engine != "lm-studio":
            return False
        if self.last_local_load_error != "no_model_loaded":
            return False

        now = time.time()
        if (now - float(self._last_local_autoload_ts)) < float(self.local_autoload_cooldown_sec):
            return False

        self._last_local_autoload_ts = now
        logger.info(
            "Auto-load локальной модели: старт",
            reason=reason or "unspecified",
            cooldown_sec=self.local_autoload_cooldown_sec,
        )
        loaded = await self._ensure_chat_model_loaded()
        # Обновляем health после попытки, чтобы is_local_available/active_local_model были актуальны.
        await self.check_local_health(force=True)
        logger.info(
            "Auto-load локальной модели: завершен",
            loaded=bool(loaded),
            active_local_model=self.active_local_model,
            local_available=self.is_local_available,
        )
        return bool(loaded)

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

    @staticmethod
    def _format_size_gb(size_bytes: int) -> str:
        """Форматирует размер модели в гигабайты для UI-команд."""
        try:
            value = float(size_bytes)
        except Exception:
            return "n/a"
        if value <= 0:
            return "n/a"
        return f"{round(value / (1024 ** 3), 2)} GB"

    async def list_local_models_verbose(self) -> List[Dict[str, Any]]:
        """
        Возвращает расширенный список локальных моделей:
        id, loaded, type, size_bytes, size_human.
        """
        raw = await self._scan_local_models()
        result: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for entry in raw:
            model_id = self._extract_model_id(entry) if isinstance(entry, dict) else None
            if not model_id:
                continue
            if model_id in seen:
                continue
            seen.add(model_id)
            size_bytes = 0
            if isinstance(entry, dict):
                try:
                    size_bytes = int(entry.get("size_bytes") or entry.get("size_on_disk") or entry.get("size") or 0)
                except Exception:
                    size_bytes = 0
            result.append(
                {
                    "id": model_id,
                    "loaded": bool(entry.get("loaded", False)) if isinstance(entry, dict) else False,
                    "type": str(entry.get("type", "llm")) if isinstance(entry, dict) else "llm",
                    "size_bytes": int(size_bytes),
                    "size_human": self._format_size_gb(size_bytes),
                }
            )
        return sorted(result, key=lambda item: item["id"])

    def _suggest_local_model_ids(self, requested: str, available_ids: List[str], limit: int = 5) -> List[str]:
        """Подбирает релевантные подсказки model_id по строке пользователя."""
        if not requested or not available_ids:
            return []

        requested_lower = requested.lower()
        substring_matches = [model_id for model_id in available_ids if requested_lower in model_id.lower()]
        if substring_matches:
            return substring_matches[:limit]

        close = difflib.get_close_matches(requested, available_ids, n=limit, cutoff=0.35)
        if close:
            return close

        return available_ids[:limit]

    def _resolve_local_model_id(self, requested: str, available_ids: List[str]) -> Optional[str]:
        """Возвращает канонический model_id, если он присутствует в скане LM Studio."""
        if not requested:
            return None

        requested_clean = requested.strip()
        if not requested_clean:
            return None

        if requested_clean in available_ids:
            return requested_clean

        lowered = requested_clean.lower()
        for model_id in available_ids:
            if model_id.lower() == lowered:
                return model_id

        # Допускаем однозначное совпадение по суффиксу/префиксу.
        fuzzy_matches = [
            model_id for model_id in available_ids
            if model_id.lower().endswith(lowered) or model_id.lower().startswith(lowered)
        ]
        if len(fuzzy_matches) == 1:
            return fuzzy_matches[0]

        return None

    def _build_lms_load_command(self, lms_path: str, model_name: str) -> List[str]:
        """
        Формирует совместимую с текущим lms CLI команду загрузки.
        Допустимые значения --gpu: off|max|число от 0 до 1.
        """
        cmd = [lms_path, "load", model_name, "-y"]
        gpu = self.lms_gpu_offload
        if gpu in {"off", "max"}:
            cmd.extend(["--gpu", gpu])
            return cmd

        if gpu:
            try:
                gpu_value = float(gpu)
                if 0.0 <= gpu_value <= 1.0:
                    cmd.extend(["--gpu", str(gpu_value)])
                else:
                    logger.warning("LM_STUDIO_GPU_OFFLOAD вне диапазона 0..1, опция игнорируется", value=gpu)
            except ValueError:
                logger.warning("LM_STUDIO_GPU_OFFLOAD имеет невалидный формат, опция игнорируется", value=gpu)

        return cmd

    async def load_local_model(self, model_name: str) -> bool:
        """
        Загружает модель в LM Studio через REST API (0.3.x).
        """
        requested_model = (model_name or "").strip()
        self.last_local_load_error = None
        self.last_local_load_error_human = None
        if not requested_model:
            self.last_local_load_error = "model_id_empty"
            self.last_local_load_error_human = "⚠️ Не указан model_id для загрузки в LM Studio."
            logger.warning("⚠️ Пустой model_id для load_local_model.")
            return False

        # Dry precheck: проверяем model_id по /api/v1/models до POST /load.
        available_ids = await self.list_local_models()
        resolved_model = self._resolve_local_model_id(requested_model, available_ids)
        if not resolved_model:
            suggestions = self._suggest_local_model_ids(requested_model, available_ids)
            self.last_local_load_error = f"model_not_found_precheck:{requested_model}"
            self.last_local_load_error_human = (
                f"⚠️ Модель `{requested_model}` не найдена в LM Studio scan. "
                "Проверь точный id через !model scan."
            )
            logger.warning(
                "⚠️ Dry precheck: model_id отсутствует в LM Studio scan",
                requested=requested_model,
                suggestions=suggestions,
                scanned_count=len(available_ids),
            )
            return False

        base = self._lm_studio_api_root()
        # В 0.3.x эндпоинт загрузки: POST /api/v1/models/load
        url = f"{base}/api/v1/models/load"
        last_rest_error_text = ""

        try:
            logger.info(f"🚀 Loading model via REST API: {resolved_model}")
            # LM Studio 0.3.x: POST /api/v1/models/load
            # Принимает {"model": "id"} — без gpu_offload (вызывает unrecognized_keys)
            timeout = aiohttp.ClientTimeout(total=120)  # Загрузка может быть долгой
            async with aiohttp.ClientSession(timeout=timeout) as session:
                payload = {
                    "model": resolved_model
                }
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        logger.info(f"✅ REST API Load Success: {resolved_model}")
                        self.active_local_model = resolved_model
                        self.is_local_available = True
                        self.last_local_load_error = None
                        self.last_local_load_error_human = None
                        return True
                    text = await resp.text()
                    last_rest_error_text = text
                    
                    # [HOTFIX v11.4.2] Распознавание фатальной ошибки LM Studio
                    if "Utility process" in text or "snapshot of system resources failed" in text:
                        self.last_local_load_error = "lms_resource_error"
                        logger.error("❌ КРИТИЧЕСКАЯ ОШИБКА LM STUDIO: Сбой системных ресурсов (Utility process). ТРЕБУЕТСЯ ПЕРЕЗАГРУЗКА LM STUDIO.")
                        # Чтобы пользователь увидел это через !model status
                        self.last_local_load_error_human = (
                            "⚠️ LM Studio: ошибка Utility process / snapshot resources. "
                            "Полностью перезапусти LM Studio и повтори загрузку модели."
                        )
                    else:
                        self.last_local_load_error = f"rest_load_failed:{resp.status}:{text[:220]}"
                        self.last_local_load_error_human = (
                            f"⚠️ LM Studio load failed (HTTP {resp.status}). "
                            "Проверь логи LM Studio и корректность model_id."
                        )
                    
                    suggestions = self._suggest_local_model_ids(requested_model, available_ids)
                    logger.warning(
                        "⚠️ REST API Load failed",
                        status=resp.status,
                        requested=requested_model,
                        resolved=resolved_model,
                        details=text[:1200],
                        suggestions=suggestions,
                    )
                    lowered = text.lower()
                    if "model_not_found" in lowered or "not found" in lowered:
                        logger.warning(
                            "❗ LM Studio вернул model_not_found. Используйте точный model_id из `!model scan`.",
                            requested=requested_model,
                            suggestions=suggestions,
                        )
        except Exception as e:
            self.last_local_load_error = f"rest_load_exception:{e}"
            self.last_local_load_error_human = "⚠️ Ошибка запроса к LM Studio во время загрузки модели."
            logger.error(f"❌ REST API Load Exception: {e}")

        # Fallback to CLI for backwards compatibility
        lms_path = os.path.expanduser("~/.lmstudio/bin/lms")
        if os.path.exists(lms_path):
            try:
                cmd = self._build_lms_load_command(lms_path, resolved_model)
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                if proc.returncode == 0:
                    self.active_local_model = resolved_model
                    self.is_local_available = True
                    self.last_local_load_error = None
                    self.last_local_load_error_human = None
                    logger.info("✅ CLI fallback load success", command=" ".join(cmd), model=resolved_model)
                    return True
                self.last_local_load_error = f"cli_load_failed:{proc.returncode}"
                self.last_local_load_error_human = (
                    f"⚠️ CLI fallback загрузки завершился с кодом {proc.returncode}."
                )
                logger.warning(
                    "⚠️ CLI fallback load failed",
                    command=" ".join(cmd),
                    returncode=proc.returncode,
                    requested=requested_model,
                    resolved=resolved_model,
                    rest_error=last_rest_error_text[:300] if last_rest_error_text else "",
                )
            except Exception as exc:
                self.last_local_load_error = f"cli_load_exception:{exc}"
                self.last_local_load_error_human = "⚠️ Исключение во время CLI fallback загрузки LM Studio."
                logger.warning("⚠️ CLI fallback load exception", error=str(exc), requested=requested_model)
        else:
            if not self.last_local_load_error:
                self.last_local_load_error = "lms_cli_not_found"
            if not self.last_local_load_error_human:
                self.last_local_load_error_human = "⚠️ LM Studio CLI не найден по пути ~/.lmstudio/bin/lms."
            logger.warning("⚠️ CLI fallback недоступен: ~/.lmstudio/bin/lms не найден.")

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
                payload["model"] = model_name
            
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

    # ═══════════════════════════════════════════════════════════════
    # Smart Memory Planner: мониторинг памяти и авто-управление
    # ═══════════════════════════════════════════════════════════════

    def _touch_model_usage(self, model_id: str) -> None:
        """
        Обновляет метку времени последнего использования модели (LRU-трекинг).
        Вызывается каждый раз, когда модель участвует в генерации.
        """
        import time
        self._model_last_used[model_id] = time.time()

    async def _get_system_memory_gb(self) -> Dict[str, float]:
        """
        Получает информацию о системной памяти через macOS sysctl / vm_stat.
        Возвращает: {"total": X, "used": Y, "free": Z} в гигабайтах.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "sysctl", "-n", "hw.memsize",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            total_bytes = int(stdout.decode().strip())
            total_gb = total_bytes / (1024 ** 3)

            # vm_stat даёт статистику по страницам (каждая 16384 байт на ARM mac)
            proc2 = await asyncio.create_subprocess_exec(
                "vm_stat",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout2, _ = await proc2.communicate()
            vm_text = stdout2.decode()

            # Парсим размер страницы и количество свободных/inactive страниц
            import re
            page_size = 16384  # дефолт для Apple Silicon
            ps_match = re.search(r"page size of (\d+) bytes", vm_text)
            if ps_match:
                page_size = int(ps_match.group(1))

            free_pages = 0
            inactive_pages = 0
            for line in vm_text.split("\n"):
                if "Pages free" in line:
                    m = re.search(r"(\d+)", line.split(":")[1])
                    if m:
                        free_pages = int(m.group(1))
                elif "Pages inactive" in line:
                    m = re.search(r"(\d+)", line.split(":")[1])
                    if m:
                        inactive_pages = int(m.group(1))

            free_gb = (free_pages + inactive_pages) * page_size / (1024 ** 3)
            used_gb = total_gb - free_gb

            return {"total": round(total_gb, 2), "used": round(used_gb, 2), "free": round(free_gb, 2)}
        except Exception as e:
            logger.warning(f"⚠️ Не удалось получить системную память: {e}")
            return {"total": self.max_ram_gb, "used": 0, "free": self.max_ram_gb}

    async def _get_loaded_models_memory(self) -> List[Dict[str, Any]]:
        """
        Получает список загруженных моделей с оценкой потребления RAM.
        Использует LM Studio API для получения размеров.
        Возвращает: [{"id": "model-name", "size_gb": 4.3, "loaded": True, "last_used": timestamp}]
        """
        models = await self._scan_local_models()
        result = []
        for m in models:
            model_id = m.get("id", "unknown")
            # LM Studio не всегда даёт точный размер, оцениваем по имени
            size_gb = self._estimate_model_size_gb(model_id)
            last_used = self._model_last_used.get(model_id, 0)
            result.append({
                "id": model_id,
                "type": m.get("type", "unknown"),
                "loaded": m.get("loaded", False),
                "size_gb": size_gb,
                "last_used": last_used,
            })
        return result

    def _estimate_model_size_gb(self, model_name: str) -> float:
        """
        Оценивает размер модели в GB на основе имени (параметры в миллиардах).
        Эвристика: 1B параметр ≈ 0.5-1 GB в зависимости от квантизации.
        Используется когда API не предоставляет точный размер.
        """
        import re
        lowered = model_name.lower()
        # Ищем паттерны вида 7b, 13b, 70b и т.д.
        match = re.search(r"(\d+\.?\d*)b", lowered)
        if match:
            params_b = float(match.group(1))
            # MLX/GGUF квантизация: ~0.6 GB на миллиард параметров (4-bit)
            if "mlx" in lowered or "4bit" in lowered or "q4" in lowered:
                return round(params_b * 0.6, 1)
            # 8-bit квантизация
            elif "8bit" in lowered or "q8" in lowered:
                return round(params_b * 1.0, 1)
            # FP16 (полная точность)
            elif "fp16" in lowered or "f16" in lowered:
                return round(params_b * 2.0, 1)
            # Дефолт (4-bit GGUF — самый распространённый)
            return round(params_b * 0.7, 1)

        # Известные модели без параметров в имени
        known_sizes = {
            "glm-4.6v": 5.0, "glm-4": 5.0,
            "phi-3": 2.5, "phi-4": 8.0,
            "llama-3.2-1b": 0.8, "llama-3.2-3b": 2.0,
        }
        for key, size in known_sizes.items():
            if key in lowered:
                return size

        # Совсем не знаем — дефолт 4 GB (средний размер для 7B модели)
        return 4.0

    async def _can_fit_model(self, model_name: str) -> bool:
        """
        Проверяет, поместится ли новая модель в пределах лимита RAM для LM Studio.
        """
        loaded = await self._get_loaded_models_memory()
        current_usage = sum(m["size_gb"] for m in loaded if m["loaded"])
        new_model_size = self._estimate_model_size_gb(model_name)
        projected = current_usage + new_model_size

        logger.info(
            "🧠 Memory check",
            current_loaded_gb=round(current_usage, 1),
            new_model_gb=round(new_model_size, 1),
            projected_gb=round(projected, 1),
            limit_gb=self.lm_studio_max_ram_gb,
        )
        return projected <= self.lm_studio_max_ram_gb

    async def _evict_idle_models(self, needed_gb: float = 0) -> float:
        """
        Выгружает неактивные модели по LRU (Least Recently Used).
        Возвращает количество освобождённых GB.

        Стратегия:
        1. Сначала выгружаем модели idle > AUTO_UNLOAD_IDLE_MIN
        2. Если всё ещё не хватает — выгружаем по LRU (самые давно использованные)
        3. Никогда не выгружаем preferred модель, если она единственная загруженная
        """
        import time
        loaded = await self._get_loaded_models_memory()
        loaded_models = [m for m in loaded if m["loaded"] and m["type"] == "llm"]

        if len(loaded_models) <= 1:
            logger.info("📌 Только 1 модель загружена, выгрузка не требуется.")
            return 0.0

        freed_gb = 0.0
        idle_threshold = time.time() - (self.auto_unload_idle_min * 60)

        # Сортируем по last_used (самые давние — первые кандидаты на выгрузку)
        candidates = sorted(loaded_models, key=lambda m: m["last_used"])

        for model in candidates:
            if freed_gb >= needed_gb and needed_gb > 0:
                break  # Хватает места

            model_id = model["id"]

            # Защита: не выгружаем preferred модель, если она единственная оставшаяся
            remaining = len(loaded_models) - 1
            if remaining <= 0:
                break
            if model_id == self.active_local_model and remaining <= 1:
                continue

            # Проверяем idle time (или если нужно место)
            is_idle = model["last_used"] < idle_threshold or model["last_used"] == 0
            if is_idle or needed_gb > 0:
                reason = "idle" if is_idle else "memory_pressure"
                logger.info(f"♻️ Выгрузка модели: {model_id} (причина: {reason}, size: {model['size_gb']} GB)")
                success = await self.unload_local_model(model_id)
                if success:
                    freed_gb += model["size_gb"]
                    loaded_models = [m for m in loaded_models if m["id"] != model_id]
                    # Обновляем active_local_model если было выгружено
                    if self.active_local_model == model_id:
                        self.active_local_model = None

        if freed_gb > 0:
            logger.info(f"🧹 Освобождено {round(freed_gb, 1)} GB RAM (модели: {len(candidates)} → {len(loaded_models)})")
        return freed_gb

    async def _smart_load(self, model_name: str, reason: str = "chat") -> bool:
        """
        Интеллектуальная загрузка модели с проверкой памяти и LRU-выгрузкой.

        1. Если модель уже загружена — просто обновляем LRU и возвращаем True
        2. Если помещается — загружаем
        3. Если не помещается — выгружаем idle модели, пробуем снова
        4. Если всё равно не помещается — ошибка

        Args:
            model_name: ID модели для загрузки
            reason: причина (chat / coding / forced)
        """
        # Проверяем, не загружена ли уже
        loaded = await self._get_loaded_models_memory()
        for m in loaded:
            if m["id"] == model_name and m["loaded"]:
                self._touch_model_usage(model_name)
                self.active_local_model = model_name
                logger.info(f"✅ Модель {model_name} уже загружена, обновляем LRU (reason: {reason})")
                return True

        # Проверяем, поместится ли
        if await self._can_fit_model(model_name):
            logger.info(f"📥 Загружаем {model_name} (reason: {reason}), RAM позволяет")
            success = await self.load_local_model(model_name)
            if success:
                self._touch_model_usage(model_name)
            return success

        # Не помещается — пробуем выгрузить idle
        new_size = self._estimate_model_size_gb(model_name)
        current_usage = sum(m["size_gb"] for m in loaded if m["loaded"])
        needed_gb = (current_usage + new_size) - self.lm_studio_max_ram_gb + 0.5  # +0.5 GB запас

        logger.warning(f"⚠️ Не хватает RAM для {model_name} ({new_size} GB). Нужно освободить {round(needed_gb, 1)} GB")
        freed = await self._evict_idle_models(needed_gb)

        if freed >= needed_gb or await self._can_fit_model(model_name):
            logger.info(f"📥 Загружаем {model_name} после освобождения памяти")
            success = await self.load_local_model(model_name)
            if success:
                self._touch_model_usage(model_name)
            return success

        logger.error(f"❌ Не удалось освободить достаточно RAM для {model_name}")
        return False

    async def get_memory_status(self) -> str:
        """
        Возвращает человекочитаемый статус памяти для команды !model memory.
        """
        sys_mem = await self._get_system_memory_gb()
        loaded = await self._get_loaded_models_memory()
        loaded_models = [m for m in loaded if m["loaded"]]
        model_usage = sum(m["size_gb"] for m in loaded_models)

        import time
        lines = [
            "🧠 **Smart Memory Planner**",
            f"",
            f"💻 Системная RAM: {sys_mem['used']}/{sys_mem['total']} GB (свободно: {sys_mem['free']} GB)",
            f"🤖 Лимит LM Studio: {round(model_usage, 1)}/{self.lm_studio_max_ram_gb} GB",
            f"⏱ Авто-выгрузка idle: {self.auto_unload_idle_min} мин",
            f"",
            f"**Загруженные модели:**",
        ]

        if not loaded_models:
            lines.append("  └─ (нет загруженных)")
        else:
            for m in loaded_models:
                last_used = self._model_last_used.get(m["id"], 0)
                if last_used > 0:
                    idle_min = int((time.time() - last_used) / 60)
                    idle_str = f"{idle_min} мин назад"
                else:
                    idle_str = "нет данных"
                active = " ⭐" if m["id"] == self.active_local_model else ""
                lines.append(f"  └─ `{m['id']}` — ~{m['size_gb']} GB (idle: {idle_str}){active}")

        return "\n".join(lines)

    async def _scan_cloud_models_via_openclaw_cli(self, all_catalog: bool = True) -> List[Dict[str, Any]]:
        """
        Сканирует Cloud-каталог через `openclaw models list`.

        Почему так:
        - HTTP endpoint Gateway в некоторых сборках отдаёт SPA HTML для `/v1/models`,
          из-за чего прямой REST-скан возвращает пусто.
        - CLI использует нативный транспорт OpenClaw и корректно отдаёт JSON-каталог.
        """
        cmd = ["openclaw", "models", "list"]
        if all_catalog:
            cmd.append("--all")
        cmd.append("--json")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        except asyncio.TimeoutError:
            return []
        except Exception:
            return []

        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="ignore").strip()
            if err:
                self.last_cloud_error = err
            return []

        raw = (stdout or b"").decode("utf-8", errors="ignore").strip()
        if not raw:
            return []

        try:
            payload = json.loads(raw)
        except Exception:
            return []

        items = payload.get("models", []) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    async def list_cloud_models(self) -> List[str]:
        """Сканирует Cloud-модели (OpenClaw) с приоритетом CLI-каталога."""
        if not self.openclaw_client:
            return ["Ошибка: OpenClaw клиент не инициализирован"]

        try:
            # 1) Основной путь: OpenClaw CLI каталог (устойчивее чем HTTP /v1/models).
            cli_models = await self._scan_cloud_models_via_openclaw_cli(all_catalog=True)
            available: List[str] = []
            configured: List[str] = []

            for item in cli_models:
                model_id = str(item.get("key") or item.get("id") or "").strip()
                if not model_id:
                    continue
                # Local модели не относим к cloud-списку.
                if bool(item.get("local")):
                    continue
                if bool(item.get("missing")):
                    continue

                tags = item.get("tags", []) if isinstance(item.get("tags"), list) else []
                if bool(item.get("available")):
                    available.append(model_id)
                elif "configured" in tags or "default" in tags:
                    # Если available пуст (например, ключ невалиден), показываем хотя бы
                    # реально сконфигурированные cloud-model_id.
                    configured.append(model_id)

            result = sorted(set(available))
            if not result and configured:
                result = sorted(set(configured))
            if result:
                self.last_cloud_error = None
                return result

            # 2) Fallback: прямой HTTP get_models (для совместимости).
            raw_models = await self.openclaw_client.get_models()
            models: List[str] = []
            for m in raw_models:
                if isinstance(m, dict) and "id" in m:
                    mid = str(m["id"]).strip()
                    if mid:
                        models.append(mid)
                elif isinstance(m, str):
                    mid = m.strip()
                    if mid:
                        models.append(mid)

            self.last_cloud_error = None
            return sorted(set(models))
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
        Вызов локальной модели через стриминг с программной отсечкой (Hard Truncation).
        Это гарантирует защиту от бесконечной генерации, даже если сервер игнорирует max_tokens.
        """
        try:
            system_msg = "You are a helpful assistant."
            if self.persona:
                system_msg = self.persona.get_current_prompt(chat_type, is_owner)

            if self.local_engine == 'lm-studio':
                base_url = self.lm_studio_url
            else:
                base_url = self.ollama_url.replace('/api', '/v1')

            if "/v1" not in base_url:
                base_url = base_url.rstrip("/") + "/v1"
            base_url = base_url.replace("/v1/v1", "/v1")

            messages = [{"role": "system", "content": system_msg}]
            if context:
                for idx, msg in enumerate(context):
                    if not isinstance(msg, dict): continue
                    mrole = self._normalize_chat_role(msg.get("role"))
                    content = msg.get("content") or msg.get("text") or msg.get("message")
                    if content: messages.append({"role": mrole, "content": str(content)})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": self.active_local_model or "local-model",
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 2048,
                "stop": ["<|im_end|>", "###", "</s>"],
                "stream": True,
                "include_reasoning": self.local_include_reasoning
            }

            headers = {"Content-Type": "application/json"}
            timeout = aiohttp.ClientTimeout(total=300, sock_read=60) # Увеличиваем стабильность
            
            full_content = []
            collected_chars = 0
            MAX_CHARS_LIMIT = 8000 # Примерно 2048 токенов
            
            start_t = time.time()
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(f"{base_url}/chat/completions", json=payload, headers=headers) as response:
                    if response.status != 200:
                        err = await response.text()
                        logger.error(f"Local LLM HTTP {response.status}: {err}")
                        return None

                    # Читаем чанки вручную для возможности разрыва
                    async for line in response.content:
                        line = line.decode("utf-8").strip()
                        if not line or line == "data: [DONE]":
                            continue
                        
                        if line.startswith("data: "):
                            try:
                                chunk_data = json.loads(line[6:])
                                delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    full_content.append(content)
                                    collected_chars += len(content)
                                    
                                    # КРИТИЧЕСКАЯ ОТСЕЧКА
                                    if collected_chars > MAX_CHARS_LIMIT:
                                        logger.warning(f"⚠️ HARD TRUNCATION: Model {self.active_local_model} exceeded {MAX_CHARS_LIMIT} chars. Breaking stream.")
                                        break
                            except Exception:
                                continue
            
            final_text = "".join(full_content)
            duration = time.time() - start_t
            
            cleaned = self._sanitize_model_text(final_text)
            if cleaned:
                logger.info("Local LLM (Stream+Truncate) success", 
                            duration=round(duration, 2), 
                            chars=len(cleaned),
                            truncated=collected_chars > MAX_CHARS_LIMIT)
                return cleaned
            return None

        except Exception as e:
            err_msg = str(e).lower()
            logger.error(f"Local LLM Stream Error: {e}")
            self._stats["local_failures"] += 1
            
            # Если это явная ошибка рантайма (Connection Refused),
            # возвращаем её как текст ошибки для детектора в роутере
            if "connection refused" in err_msg or "failed to connect" in err_msg:
                return f"Error: Local engine connection refused ({e})"
            return None

    async def route_query(self,
                          prompt: str,
                          task_type: Literal['coding', 'chat', 'reasoning', 'creative', 'moderation', 'security', 'infra', 'review'] = 'chat',
                          context: list = None,
                          chat_type: str = "private",
                          is_owner: bool = False,
                          use_rag: bool = True,
                          preferred_model: Optional[str] = None,
                          confirm_expensive: bool = False,
                          skip_swarm: bool = False):
        """
        Главный метод маршрутизации запроса с Auto-Fallback, RAG и policy-роутингом.
        [R12] Resilience Hardening: single fallback limit + explicit telemetry.
        """
        fallback_happened = False
        
        profile = self.classify_task_profile(prompt, task_type)
        recommendation = self._get_profile_recommendation(profile)
        is_critical = recommendation["critical"]
        prefer_pro_for_owner_private = self._should_use_pro_for_owner_private(
            prompt=prompt,
            chat_type=chat_type,
            is_owner=is_owner,
        )

        # 0. RAG Lookup
        if use_rag and self.rag:
            rag_context = self.rag.query(prompt)
            if rag_context:
                prompt = f"### ДОПОЛНИТЕЛЬНЫЕ ДАННЫЕ ИЗ ТВОЕЙ ПАМЯТИ (RAG):\n{rag_context}\n\n### ТЕКУЩИЙ ЗАПРОС:\n{prompt}"

        # 0.1. Tool Orchestration
        if self.tools and not skip_swarm:
            # Предотвращаем рекурсию через skip_swarm=True в Swarm
            tool_data = await self.tools.execute_tool_chain(prompt, skip_swarm=True)
            if tool_data:
                prompt = f"### ДАННЫЕ ИЗ ИНСТРУМЕНТОВ:\n{tool_data}\n\n### ТЕКУЩИЙ ЗАПРОС:\n{prompt}"

        # Smart Memory Planner
        if self.force_mode != "force_cloud" and self.is_local_available:
            preferred = preferred_model or self.local_preferred_model
            if task_type == "coding" and self.local_coding_model:
                preferred = self.local_coding_model
            if preferred:
                await self._smart_load(preferred, reason=task_type)

        await self.check_local_health()
        if not self.is_local_available:
            await self._maybe_autoload_local_model(reason=f"route_query:{task_type}")

        async def _run_local(route_reason: str = "local_primary", route_detail: str = "") -> Any:
            if not self.is_local_available:
                return "unavailable", None
            async with self._acquire_local_slot(self.active_local_model):
                logger.info(
                    "Routing to LOCAL",
                    model=self.active_local_model,
                    profile=profile,
                    reason=route_reason
                )
                local_response = await self._call_local_llm(prompt, context, chat_type, is_owner)
                
                if self._is_runtime_error_message(local_response):
                    logger.warning("Local LLM Runtime Error", model=self.active_local_model, error=local_response)
                    return "runtime_error", local_response

                if local_response and local_response.strip():
                    # Guardrail: минимальная длина для не-пустых ответов
                    if len(local_response.strip()) < 1 and not skip_swarm:
                        return "empty_output", "Local response was too short"
                        
                    self._touch_model_usage(self.active_local_model or "local-model")
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
                        route_reason=route_reason,
                        route_detail=route_detail,
                        force_mode=self.force_mode,
                    )
                    return "ok", local_response
                return "empty_output", None

        async def _run_cloud(route_reason: str = "cloud_primary", route_detail: str = ""):
            if self.require_confirm_expensive and is_critical and not confirm_expensive:
                return "confirm_needed", "⚠️ Для критичной задачи требуется подтверждение дорогого облачного прогона."
            
            cloud_preferred = preferred_model or recommendation.get("model")
            if self.cost_engine:
                cloud_preferred = self.cost_engine.get_recommended_model(profile, cloud_preferred)

            if prefer_pro_for_owner_private:
                cloud_preferred = self.models.get("pro", cloud_preferred)

            force_cloud_mode = self.force_mode == "force_cloud"
            deadline = (
                time.monotonic() + float(self.cloud_fail_fast_budget_seconds)
                if force_cloud_mode
                else None
            )

            for i, candidate in enumerate(
                self._build_cloud_candidates(
                    task_type=task_type,
                    profile=profile,
                    preferred_model=cloud_preferred,
                    chat_type=chat_type,
                    is_owner=is_owner,
                    prompt=prompt,
                )
            ):
                provider = candidate.split("/", 1)[0]
                preflight_error = self._check_cloud_preflight(provider)
                if preflight_error:
                    logger.warning("Cloud preflight rejected candidate", candidate=candidate, error=preflight_error)
                    if force_cloud_mode:
                        return "preflight_blocked", f"❌ {preflight_error}"
                    continue

                if "-exp" in candidate and "gemini-2.0" in candidate:
                    candidate = candidate.replace("-exp", "")
                if deadline is not None and time.monotonic() >= deadline:
                    self.last_cloud_error = (
                        "Cloud fail-fast budget exceeded "
                        f"({self.cloud_fail_fast_budget_seconds}s)"
                    )
                    logger.warning(
                        "Cloud routing stopped by fail-fast budget",
                        budget_seconds=self.cloud_fail_fast_budget_seconds,
                        attempt=i + 1,
                        reason=route_reason,
                    )
                    break
                    
                logger.info("Routing to CLOUD", model=candidate, profile=profile, reason=route_reason)
                max_retries_cloud = 0 if force_cloud_mode else (1 if i == 0 else 0)
                response = await self._call_gemini(prompt, candidate, context, chat_type, is_owner, max_retries=max_retries_cloud)
                
                if self._is_runtime_error_message(response):
                    logger.warning("Cloud candidate %s failed", candidate, error=response)
                    self._mark_cloud_soft_cap_if_needed(str(response))
                    self.last_cloud_error = str(response)
                    self.last_cloud_model = candidate
                    if self._is_fatal_cloud_auth_error(response):
                        logger.error(
                            "Cloud routing aborted: fatal auth/billing error",
                            model=candidate,
                            error=str(response)[:280],
                        )
                        break
                    continue
                
                # Cloud Success Guardrail
                if not response or len(response.strip()) < 2:
                    logger.warning("Cloud candidate %s returned empty/junk", candidate)
                    continue

                self.last_cloud_error = None
                self.last_cloud_model = candidate
                return candidate, response
            
            return "all_candidates_failed", self.last_cloud_error or "Cloud API failure"

        # --- Execution starts here ---

        if self.force_mode == "force_local":
            l_status, l_resp = await _run_local(route_reason="force_local", route_detail="forced by mode")
            if l_status == "ok": return l_resp
            return f"❌ Ошибка алгоритма Local ({l_status}): {l_resp}" if is_owner else "❌ Локальные модели сейчас недоступны."

        if self.force_mode == "force_cloud":
            c_res = await _run_cloud(route_reason="force_cloud", route_detail="forced by mode")
            if isinstance(c_res, tuple) and c_res[0] != "all_candidates_failed" and c_res[0] != "preflight_blocked":
                # Finalize cloud normally
                candidate, response = c_res
                self._remember_model_choice(profile, candidate, "cloud")
                self._update_usage_report(profile, candidate, "cloud")
                self._remember_last_route(profile=profile, task_type=task_type, channel="cloud",
                                          model_name=candidate, prompt=prompt, route_reason="force_cloud")
                return response
            err_msg = self.last_cloud_error or 'Unknown cloud failure'
            if isinstance(c_res, tuple) and c_res[0] == "preflight_blocked":
                err_msg = c_res[1] # Use the preflight error message directly
            summary = self._summarize_cloud_error_for_user(err_msg)
            return (
                f"❌ Ошибка Cloud (force_cloud): {summary}."
                if is_owner
                else "❌ Облачный сервис временно недоступен. Пожалуйста, попробуй позже."
            )

        # Auto Mode Strategy
        force_local_due_cost = self.cloud_soft_cap_reached and not is_critical
        prefer_cloud = is_critical or task_type == "reasoning"
        if force_local_due_cost: prefer_cloud = False

        local_status = "skipped"
        local_response = None

        # 1. Try Local First if appropriate
        if not prefer_cloud and self.is_local_available:
            local_status, local_response = await _run_local(route_reason="local_primary", route_detail="auto mode")
            if local_status == "ok":
                return local_response

        # 2. Fallback to Cloud or Try Cloud Primary
        if local_status != "ok":
            fallback_happened = (local_status != "skipped")
            c_reason = "cloud_primary" if local_status == "skipped" else "local_fallback_cloud"
            c_detail = f"local_status={local_status}"
            
            cloud_result = await _run_cloud(route_reason=c_reason, route_detail=c_detail)
            
            if isinstance(cloud_result, tuple) and cloud_result[0] != "all_candidates_failed" and cloud_result[0] != "preflight_blocked":
                candidate, c_response = cloud_result
                # Finalize
                self._remember_model_choice(profile, candidate, "cloud")
                self._update_usage_report(profile, candidate, "cloud")
                self._remember_last_route(
                    profile=profile, task_type=task_type, channel="cloud",
                    model_name=candidate, prompt=prompt,
                    route_reason=c_reason, route_detail=c_detail
                )
                return c_response
            
            # 3. Last resort: Local again ONLY if we haven't successfully run it and cloud failed
            if self.is_local_available and not fallback_happened:
                # This would be cloud->local fallback (not requested to be limited, but let's be careful)
                l_status, l_resp = await _run_local(route_reason="cloud_fallback_local", route_detail="cloud failed")
                if l_status == "ok":
                    return l_resp

        err_msg = self.last_cloud_error or "Все каналы (Local/Cloud) недоступны или вернули ошибку."
        summary = self._summarize_cloud_error_for_user(err_msg)
        return (
            f"❌ Ошибка маршрутизации: {summary}."
            if is_owner
            else "❌ В данный момент генерация ответа не удалась из-за системной ошибки. Пожалуйста, попробуй позже."
        )

    async def route_stream(self,
                          prompt: str,
                          task_type: Literal['coding', 'chat', 'reasoning'] = 'chat',
                          context: list = None,
                          chat_type: str = "private",
                          is_owner: bool = False,
                          preferred_model: Optional[str] = None,
                          confirm_expensive: bool = False) -> AsyncGenerator[str, None]:
        """
        [PHASE 17.8] Потоковая маршрутизация с защитой local stream и cloud fallback.
        """
        await self.check_local_health()
        if not self.is_local_available:
            await self._maybe_autoload_local_model(reason=f"route_stream:{task_type}")
        profile = self.classify_task_profile(prompt, task_type)
        recommendation = self._get_profile_recommendation(profile)
        force_cloud_mode = self.force_mode == "force_cloud"
        prefer_pro_for_owner_private = self._should_use_pro_for_owner_private(
            prompt=prompt,
            chat_type=chat_type,
            is_owner=is_owner,
        )

        async def _stream_cloud_fallback(failure_reason: str, failure_detail: str) -> AsyncGenerator[str, None]:
            """
            Fallback при сбое local stream.
            Важный контракт: reasoning и внутренние причины не публикуем как пользовательский ответ.
            """
            async def _try_local_recovery_without_reasoning() -> Optional[str]:
                """
                Аварийный локальный recovery:
                повторяем запрос без reasoning, чтобы избежать cloud-зависимости,
                если LM Studio доступна, но stream-фаза сорвалась guardrail-детектором.
                """
                prev_reasoning_flag = self.local_include_reasoning
                try:
                    self.local_include_reasoning = False
                    recovered = await self._call_local_llm(
                        prompt=prompt,
                        context=context,
                        chat_type=chat_type,
                        is_owner=is_owner,
                    )
                    cleaned = self._sanitize_model_text(recovered or "")
                    return cleaned or None
                except Exception as local_exc:
                    logger.warning("Local recovery (reasoning-off) failed", error=str(local_exc))
                    return None
                finally:
                    self.local_include_reasoning = prev_reasoning_flag

            # В force_cloud локальный recovery запрещён по контракту.
            allow_local_recovery = not force_cloud_mode

            if allow_local_recovery and failure_reason in {"reasoning_loop", "reasoning_limit", "stream_timeout"}:
                recovered_local = await _try_local_recovery_without_reasoning()
                if recovered_local:
                    logger.info(
                        "Local stream recovery succeeded without reasoning",
                        reason=failure_reason,
                        model=self.active_local_model,
                    )
                    yield recovered_local
                    return

            if not self.local_stream_fallback_to_cloud:
                logger.warning(
                    "Local stream failed, cloud fallback disabled by config",
                    reason=failure_reason,
                    detail=failure_detail,
                )
                yield (
                    f"⚠️ Локальный стрим остановлен ({failure_reason}). "
                    "Cloud fallback отключён конфигурацией."
                )
                return

            logger.warning(
                "Local stream failed, switching to cloud fallback",
                reason=failure_reason,
                detail=failure_detail,
                profile=profile,
                model=self.active_local_model,
            )

            cloud_preferred = preferred_model or recommendation.get("model")
            if prefer_pro_for_owner_private:
                cloud_preferred = self.models.get("pro", cloud_preferred)
            deadline = (
                time.monotonic() + float(self.cloud_fail_fast_budget_seconds)
                if failure_reason == "force_cloud"
                else None
            )
            for candidate in self._build_cloud_candidates(
                task_type=task_type,
                profile=profile,
                preferred_model=cloud_preferred,
                chat_type=chat_type,
                is_owner=is_owner,
                prompt=prompt,
            ):
                provider = candidate.split("/", 1)[0]
                preflight_error = self._check_cloud_preflight(provider)
                if preflight_error:
                    logger.warning("Cloud stream preflight rejected candidate", candidate=candidate, error=preflight_error)
                    if force_cloud_mode:
                        yield f"❌ {preflight_error}"
                        return
                    continue

                if "-exp" in candidate and "gemini-2.0" in candidate:
                    candidate = candidate.replace("-exp", "")
                if deadline is not None and time.monotonic() >= deadline:
                    self.last_cloud_error = (
                        "Cloud stream fail-fast budget exceeded "
                        f"({self.cloud_fail_fast_budget_seconds}s)"
                    )
                    logger.warning(
                        "Cloud stream fallback stopped by fail-fast budget",
                        budget_seconds=self.cloud_fail_fast_budget_seconds,
                    )
                    break

                logger.info("Routing to CLOUD (Stream Fallback)", model=candidate, profile=profile, reason=failure_reason)
                retries = 0 if failure_reason == "force_cloud" else 1
                response = await self._call_gemini(
                    prompt,
                    candidate,
                    context,
                    chat_type,
                    is_owner,
                    max_retries=retries,
                )
                
                # [R12] Используем унифицированный детектор ошибок
                if self._is_runtime_error_message(response):
                    err_msg = str(response or "Cloud error")
                    self.last_cloud_error = err_msg
                    self.last_cloud_model = candidate
                    self._mark_cloud_soft_cap_if_needed(err_msg)
                    logger.warning("Cloud fallback candidate failed", model=candidate, error=err_msg[:200])
                    if self._is_fatal_cloud_auth_error(err_msg):
                        logger.error(
                            "Cloud stream fallback aborted: fatal auth/billing error",
                            model=candidate,
                            error=err_msg[:280],
                        )
                        break
                    
                    # Проверка на NOT_FOUND (мисконфигурация шлюза)
                    lowered = err_msg.lower()
                    if "not found" in lowered or "not_found" in lowered:
                         logger.error("OpenClaw provider model mapping error", candidate=candidate)
                         if allow_local_recovery:
                             recovered_local = await _try_local_recovery_without_reasoning()
                             if recovered_local:
                                 yield recovered_local
                                 return
                    continue

                if not response or len(response.strip()) < 2:
                    logger.warning("Cloud fallback candidate %s returned empty/junk", candidate)
                    continue

                cleaned = self._sanitize_model_text(response)
                if not cleaned:
                    continue

                self.last_cloud_error = None
                self.last_cloud_model = candidate
                # Сохраняем телеметрию
                route_reason = "local_stream_failed_cloud_fallback" if failure_reason != "force_cloud" else "force_cloud"
                route_detail = f"{failure_reason}: {failure_detail}".strip()[:240]
                
                self._remember_model_choice(profile, candidate, "cloud")
                self._update_usage_report(profile, candidate, "cloud")
                self._remember_last_route(
                    profile=profile, task_type=task_type, channel="cloud", model_name=candidate,
                    prompt=prompt, route_reason=route_reason, route_detail=route_detail, force_mode=self.force_mode
                )
                self._remember_last_stream_route(
                    profile=profile, task_type=task_type, channel="cloud", model_name=candidate,
                    prompt=prompt, route_reason=route_reason, route_detail=route_detail, force_mode=self.force_mode
                )
                yield cleaned
                return

            last_error_text = str(self.last_cloud_error or "").strip()
            err_msg = last_error_text or "облачный провайдер временно недоступен."
            summary = self._summarize_cloud_error_for_user(err_msg)
            msg = (
                f"❌ Ошибка Cloud ({failure_reason}): {summary}."
                if is_owner
                else "❌ Облачный сервис временно недоступен. Пожалуйста, попробуй позже."
            )
            yield msg
            return

        # Жёсткий cloud-only режим: локальный стрим полностью пропускаем.
        if force_cloud_mode:
            async for chunk in _stream_cloud_fallback(
                failure_reason="force_cloud",
                failure_detail="local stream bypassed by force_cloud mode",
            ):
                yield chunk
            return

        if not self.is_local_available:
            # Fallback на обычный route_query если стриминг недоступен для облака в данном контексте
            res = await self.route_query(
                prompt=prompt,
                task_type=task_type,
                context=context,
                chat_type=chat_type,
                is_owner=is_owner,
                preferred_model=preferred_model,
                confirm_expensive=confirm_expensive,
            )
            yield res
            return

        # Подготовка системного промпта
        system_msg = "You are a helpful assistant."
        if hasattr(self, "persona") and self.persona:
            system_msg = self.persona.get_current_prompt(chat_type, is_owner)

        # Сборка сообщений
        messages = [{"role": "system", "content": system_msg}]
        if context:
            from src.core.context_manager import ContextKeeper
            for msg in context:
                # Нормализация роли для предотвращения ошибок типа 'vision_analysis' в LM Studio
                role = ContextKeeper._normalize_role(msg.get("role"))
                content = msg.get("text") or msg.get("content") or ""
                if content:
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.active_local_model or "local-model",
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 2048,
            "include_reasoning": self.local_include_reasoning,
            "stream": True,
            "stop": ["<|endoftext|>", "<|user|>", "<|observation|>", "Observation:", "User:", "###", "---"],
            "presence_penalty": 0.1,
            "frequency_penalty": 0.1,
            # Внутренние поля клиента стрима (не отправляются в LM Studio).
            "_krab_max_chars": 4000,
            "_krab_max_reasoning_chars": self.local_reasoning_max_chars,
            "_krab_total_timeout_seconds": self.local_stream_total_timeout_seconds,
            "_krab_sock_read_timeout_seconds": self.local_stream_sock_read_timeout_seconds,
        }

        emitted_chunks = 0
        try:
            async with self._acquire_local_slot(self.active_local_model):
                async for chunk in self.stream_client.stream_chat(payload):
                    emitted_chunks += 1
                    yield chunk
                if emitted_chunks > 0:
                    local_model = self.active_local_model or payload.get("model") or "local-model"
                    self._remember_last_stream_route(
                        profile=profile,
                        task_type=task_type,
                        channel="local",
                        model_name=str(local_model),
                        prompt=prompt,
                        route_reason="local_stream_primary",
                        route_detail="stream completed on local model",
                        force_mode=self.force_mode,
                    )
                return
        except StreamFailure as e:
            logger.warning(
                "Local stream guardrail/failure triggered",
                reason=e.reason,
                detail=e.technical_message,
                emitted_chunks=emitted_chunks,
                model=self.active_local_model,
            )
            async for cloud_chunk in _stream_cloud_fallback(e.reason, e.technical_message):
                yield cloud_chunk
            return
        except Exception as e:
            logger.error("Streaming error in route_stream", error=f"{type(e).__name__}: {e}")
            async for cloud_chunk in _stream_cloud_fallback("connection_error", f"{type(e).__name__}: {e}"):
                yield cloud_chunk
            return

    async def _call_gemini(self, prompt: str, model_name: str, context: list = None,
                           chat_type: str = "private", is_owner: bool = False, max_retries: int = 2) -> str:
        """
        Вызов Cloud модели через OpenClaw Gateway.
        """
        # [HOTFIX v11.4.2] Глобальный фильтер проблемных моделей (УСИЛЕННЫЙ)
        if model_name and ("-exp" in model_name or "gemini-2.0-flash-exp" in model_name):
            if "thinking" not in model_name: # Thinking пока только exp
                stable_chat_model = self.models.get("chat", "gemini-2.5-flash")
                logger.info(f"Filtering out problematic model: {model_name} -> {stable_chat_model}")
                model_name = stable_chat_model
        
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
                role = self._normalize_chat_role(msg.get("role", "user"))
                messages.append({"role": role, "content": msg.get("text", "")})
        
        messages.append({"role": "user", "content": prompt})

        for attempt in range(max_retries + 1):
            try:
                response_text = await self.openclaw_client.chat_completions(
                    messages,
                    model=model_name,
                    timeout_seconds=self.cloud_request_timeout_seconds,
                    probe_provider_on_error=self.cloud_probe_on_chat_error,
                )
                cleaned_response = self._sanitize_model_text(response_text)
                normalized = (cleaned_response or "").strip()
                error_detected = self._is_cloud_error_message(normalized)
                billing_issue = self._is_cloud_billing_error(normalized)
                fatal_auth_error = self._is_fatal_cloud_auth_error(normalized)

                if error_detected or billing_issue:
                    self._mark_cloud_soft_cap_if_needed(normalized or "пустой ответ")
                    fatal_auth_error = self._is_fatal_cloud_auth_error(normalized)
                    if fatal_auth_error:
                        # R15: Кэшируем фатальную ошибку для Preflight Gate
                        provider = model_name.split("/", 1)[0]
                        self._preflight_cache[provider] = (time.time() + self._preflight_ttl_seconds, response_text)
                        
                        self._stats["cloud_failures"] += 1
                        return f"❌ Ошибка Cloud: {response_text}"

                    category = self._categorize_cloud_error(normalized)
                    if not category.get("retryable", True):
                        provider = model_name.split("/", 1)[0]
                        self._preflight_cache[provider] = (time.time() + self._preflight_ttl_seconds, category.get("summary", "fatal error"))

                    if attempt < max_retries:
                        logger.warning(f"OpenClaw Attempt {attempt+1} failed: {response_text}")
                        await asyncio.sleep(2 ** (attempt + 1))
                        continue
                        
                    self._stats["cloud_failures"] += 1
                    if billing_issue:
                        return f"❌ Ошибка биллинга (OpenClaw): Похоже, на аккаунте закончились средства или достигнут лимит провайдера. Проверьте баланс на шлюзе. (Детали: {response_text})"
                    return f"❌ Ошибка Cloud: {response_text}"

                self._stats["cloud_calls"] += 1
                return cleaned_response

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
                                 use_rag: bool = True,
                                 skip_swarm: bool = False):
        """
        Версия route_query с поддержкой стриминга (пока только для Cloud).
        """
        # 1. Сначала делаем всю подготовку (RAG, Tools) - такая же как в route_query
        if use_rag and self.rag:
            rag_context = self.rag.query(prompt)
            if rag_context:
                prompt = f"### ДОПОЛНИТЕЛЬНЫЕ ДАННЫЕ ИЗ ТВОЕЙ ПАМЯТИ (RAG):\n{rag_context}\n\n### ТЕКУЩИЙ ЗАПРОС:\n{prompt}"

        if self.tools and not skip_swarm:
            tool_data = await self.tools.execute_tool_chain(prompt, skip_swarm=True)
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
        if self.force_mode == 'force_local' or (
            self.force_mode != 'force_cloud'
            and self.is_local_available
            and task_type in ['chat', 'coding']
        ):
             try:
                 full_res = await self.route_query(prompt, task_type, context, chat_type, is_owner, use_rag=False)
                 if full_res and full_res.strip():
                     yield full_res
                 else:
                     logger.warning("Local internal route returned empty content")
                     yield "⚠️ Локальная модель вернула пустой ответ."
             except Exception as e:
                 logger.error(f"Fallback routing in stream failed: {e}")
                 yield f"❌ Ошибка маршрутизации: {e}"
             return

        # 4. Стриминг через облако (Gemini) с Fallback
        preferred = self.models.get(task_type, self.models["chat"])
        candidates = self._build_cloud_candidates(
            task_type=task_type,
            profile=self.classify_task_profile(prompt, task_type),
            preferred_model=preferred,
            chat_type=chat_type,
            is_owner=is_owner,
            prompt=prompt
        )
        
        last_err = None
        for i, candidate in enumerate(candidates):
            if "-exp" in candidate and "gemini-2.0" in candidate:
                candidate = candidate.replace("-exp", "")
            
            try:
                # Временно псевдо-стриминг (полный ответ за раз)
                response = await self._call_gemini(
                    prompt, 
                    candidate, 
                    context, 
                    chat_type, 
                    is_owner, 
                    max_retries=(0 if self.force_mode == "force_cloud" else (1 if i == 0 else 0))
                )
                
                if self._is_runtime_error_message(response):
                    self.last_cloud_error = str(response)
                    self.last_cloud_model = candidate
                    self._mark_cloud_soft_cap_if_needed(str(response))
                    last_err = response
                    if self._is_fatal_cloud_auth_error(response):
                        logger.error(
                            "Cloud stream aborted: fatal auth/billing error",
                            model=candidate,
                            error=str(response)[:280],
                        )
                        break
                    continue
                
                if response and len(response.strip()) >= 2:
                    self.last_cloud_error = None
                    self.last_cloud_model = candidate
                    yield self._sanitize_model_text(response)
                    return
            except Exception as e:
                last_err = f"Error from OpenClaw generator: {e}"
                continue
                
        # Если все кандидаты упали
        err_out = last_err or "Cloud API failure"
        summary = self._summarize_cloud_error_for_user(err_out)
        yield (
            f"❌ Ошибка Cloud: {summary}."
            if is_owner
            else "❌ Облачный сервис временно недоступен. Пожалуйста, попробуй позже."
        )

    async def _call_gemini_stream(self, prompt: str, model_name: str, context: list = None,
                                  chat_type: str = "private", is_owner: bool = False):
        """
        Генератор для стриминга ответов из Cloud (Оставлен для совместимости API).
        """
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
        
        result["Cloud Reliability"] = {
            "ok": not bool(self.last_cloud_error),
            "status": "Errors Present" if self.last_cloud_error else "Stable",
            "last_error": self.last_cloud_error or "None",
            "last_provider_model": getattr(self, "last_cloud_model", "None"),
            "cloud_failures": self._stats.get("cloud_failures", 0),
            "force_mode": self.force_mode,
        }
        cloud_diag = self.get_last_cloud_error_info()
        result["Cloud Reliability"]["error_code"] = cloud_diag.get("code", "none")
        result["Cloud Reliability"]["error_summary"] = cloud_diag.get("summary", "")
        result["Cloud Reliability"]["retryable"] = bool(cloud_diag.get("retryable", True))

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

    def get_last_stream_route(self) -> dict:
        """Возвращает метаданные последнего успешного stream-ответа."""
        return dict(self._last_stream_route) if isinstance(self._last_stream_route, dict) else {}

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
            # НЕ перебиваем recommendation.channel для non-critical:
            # Local First стратегия уже зашита в _get_profile_recommendation
            if self.cloud_soft_cap_reached and not is_critical:
                prefer_cloud = False
            chosen_channel = "cloud" if prefer_cloud else "local"

        if chosen_channel == "cloud":
            chosen_model = self._resolve_cloud_model(
                task_type=normalized_task_type,
                profile=profile,
                preferred_model=preferred_model or recommendation.get("model"),
                chat_type="private",
                is_owner=False,
                prompt=normalized_prompt,
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

    @staticmethod
    def _humanize_route_reason(route_reason: str, route_channel: str = "") -> str:
        """Возвращает человекочитаемое объяснение кода причины роутинга."""
        code = str(route_reason or "").strip().lower()
        channel = str(route_channel or "").strip().lower()
        reason_map = {
            "force_local": "Выбран локальный канал из-за принудительного режима force_local.",
            "force_cloud": "Выбран облачный канал из-за принудительного режима force_cloud.",
            "local_primary": "Сработала стратегия local-first: локальная модель доступна.",
            "local_stream_primary": "Потоковый ответ завершён локально (stream local-primary).",
            "local_unavailable": "Локальный канал недоступен, выполнен fallback в cloud.",
            "local_failed_cloud_fallback": "Локальный запуск завершился ошибкой, выполнен fallback в cloud.",
            "policy_prefer_cloud": "Политика роутинга выбрала cloud для текущего профиля задачи.",
            "cloud_selected": "Выбран облачный канал по рекомендации роутера.",
            "cloud_failed_local_fallback": "Cloud-запуск завершился ошибкой, выполнен fallback в local.",
            "critical_cloud_review": "Для критичного профиля включён cloud-review для качества результата.",
        }
        if code in reason_map:
            return reason_map[code]
        if channel == "local":
            return "Маршрут выполнен через local-канал по текущей policy."
        if channel == "cloud":
            return "Маршрут выполнен через cloud-канал по текущей policy."
        return "Причина маршрутизации не была явно зафиксирована."

    def get_route_explain(
        self,
        *,
        prompt: str = "",
        task_type: str = "chat",
        preferred_model: str | None = None,
        confirm_expensive: bool = False,
    ) -> dict:
        """
        Возвращает explainability-срез по выбору модели/канала.

        Что внутри:
        1) last_route с route_reason/route_detail;
        2) policy snapshot (force_mode, soft-cap, доступность local);
        3) preflight (если передан prompt);
        4) explainability_score — насколько прозрачен маршрут.
        """
        last_route = self.get_last_route()
        route_reason = str(last_route.get("route_reason", "")).strip() if isinstance(last_route, dict) else ""
        route_detail = str(last_route.get("route_detail", "")).strip() if isinstance(last_route, dict) else ""
        route_channel = str(last_route.get("channel", "")).strip() if isinstance(last_route, dict) else ""

        policy_snapshot = {
            "routing_policy": self.routing_policy,
            "force_mode": self.force_mode,
            "cloud_soft_cap_reached": bool(self.cloud_soft_cap_reached),
            "local_available": bool(self.is_local_available),
        }

        preflight_payload: dict[str, Any] | None = None
        normalized_prompt = str(prompt or "").strip()
        if normalized_prompt:
            preflight_payload = self.get_task_preflight(
                prompt=normalized_prompt,
                task_type=task_type,
                preferred_model=preferred_model,
                confirm_expensive=confirm_expensive,
            )

        explainability_score = 0
        if isinstance(last_route, dict) and last_route:
            explainability_score += 40
        if route_reason:
            explainability_score += 30
        if route_detail:
            explainability_score += 10
        if preflight_payload is not None:
            explainability_score += 20
        explainability_score = max(0, min(100, explainability_score))

        if explainability_score >= 80:
            transparency_level = "high"
        elif explainability_score >= 50:
            transparency_level = "medium"
        else:
            transparency_level = "low"

        return {
            "generated_at": self._now_iso(),
            "last_route": last_route if isinstance(last_route, dict) else {},
            "reason": {
                "code": route_reason or "unknown",
                "detail": route_detail or "",
                "human": self._humanize_route_reason(route_reason, route_channel),
            },
            "policy": policy_snapshot,
            "preflight": preflight_payload,
            "explainability_score": explainability_score,
            "transparency_level": transparency_level,
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

    def get_credit_runway_report(
        self,
        credits_usd: float = 300.0,
        horizon_days: int = 80,
        reserve_ratio: float = 0.1,
        monthly_calls_forecast: int | None = None,
    ) -> dict:
        """
        Считает «дорожку расхода» кредита:
        - целевой бюджет в день (чтобы дожить до horizon_days),
        - оценка текущего daily burn-rate,
        - runway в днях при текущем профиле,
        - сценарные лимиты вызовов/день по Flash Lite / Flash / Pro.
        """
        safe_credits = max(0.0, float(credits_usd))
        safe_days = max(1, int(horizon_days))
        safe_reserve = min(0.95, max(0.0, float(reserve_ratio)))
        usable_budget = round(safe_credits * (1.0 - safe_reserve), 6)
        daily_target_budget = round(usable_budget / safe_days, 6)

        forecast_calls = (
            int(monthly_calls_forecast)
            if monthly_calls_forecast is not None
            else int(self.monthly_calls_forecast)
        )
        cost_report = self.get_cost_report(monthly_calls_forecast=forecast_calls)
        costs = cost_report.get("costs_usd", {})
        monthly = cost_report.get("monthly_forecast", {})
        pricing = cost_report.get("pricing", {})

        current_avg_cost = max(0.0, float(costs.get("avg_cost_per_call", 0.0)))
        # Если статистики пока нет — используем cloud baseline.
        if current_avg_cost <= 0:
            current_avg_cost = max(0.000001, float(pricing.get("cloud_cost_per_call_usd", self.cloud_cost_per_call_usd)))

        forecast_monthly_total = max(0.0, float(monthly.get("forecast_total_cost", 0.0)))
        estimated_daily_burn = round(forecast_monthly_total / 30.0, 6)
        if estimated_daily_burn <= 0:
            # Деградационный fallback: считаем от целевого бюджета.
            estimated_daily_burn = daily_target_budget

        runway_days_at_current = (
            round(safe_credits / estimated_daily_burn, 2)
            if estimated_daily_burn > 0
            else float("inf")
        )
        recommended_calls_per_day = int(daily_target_budget / current_avg_cost) if current_avg_cost > 0 else 0

        def _calls_per_day(unit_cost: float) -> int:
            safe_unit = max(0.000001, float(unit_cost))
            return int(daily_target_budget / safe_unit)

        scenarios = {
            "flash_lite": {
                "unit_cost_usd": round(float(self.model_cost_flash_lite_usd), 6),
                "max_calls_per_day": _calls_per_day(self.model_cost_flash_lite_usd),
            },
            "flash": {
                "unit_cost_usd": round(float(self.model_cost_flash_usd), 6),
                "max_calls_per_day": _calls_per_day(self.model_cost_flash_usd),
            },
            "pro": {
                "unit_cost_usd": round(float(self.model_cost_pro_usd), 6),
                "max_calls_per_day": _calls_per_day(self.model_cost_pro_usd),
            },
        }

        return {
            "credits_usd": safe_credits,
            "horizon_days": safe_days,
            "reserve_ratio": safe_reserve,
            "usable_budget_usd": usable_budget,
            "daily_target_budget_usd": daily_target_budget,
            "estimated_daily_burn_usd": estimated_daily_burn,
            "runway_days_at_current_burn": runway_days_at_current,
            "current_avg_cost_per_call_usd": round(current_avg_cost, 6),
            "recommended_calls_per_day": recommended_calls_per_day,
            "forecast_calls_monthly": forecast_calls,
            "scenarios": scenarios,
            "cost_report": cost_report,
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
