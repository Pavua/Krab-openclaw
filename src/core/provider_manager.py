"""
provider_manager.py — Менеджер провайдеров ИИ для Краб-юзербота.

Что это: единая точка управления ПРОВАЙДЕРАМИ и МОДЕЛЯМИ из всех
доступных источников: OAuth Gemini (VSCode/Antigravity), Gemini API,
OAuth OpenAI (ChatGPT Plus), OpenAI API, LM Studio local.

Хранит: активный провайдер, выбранную модель, fallback-цепочки,
настройки thinking/reasoning-режима.

Связан с:
- src/config.py                        — источник env-переменных
- src/core/cloud_gateway.py            — fallback-цепочки облака
- src/model_manager.py                 — выбор модели (local/cloud)
- src/handlers/command_handlers.py     — !provider команды
- src/web_app.py                       — REST API /api/provider/* для веб-панели
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum

from .logger import get_logger

logger = get_logger(__name__)


class ProviderType(str, Enum):
    """Все типы провайдеров, поддерживаемых Крабом."""

    GEMINI_OAUTH = "gemini_oauth"  # Google через OAuth (Antigravity/VSCode free quota)
    GEMINI_API = "gemini_api"  # Google через paid API key
    OPENAI_OAUTH = "openai_oauth"  # ChatGPT Plus через OAuth
    OPENAI_API = "openai_api"  # OpenAI через paid API key
    LM_STUDIO = "lm_studio"  # Локальные модели LM Studio
    AUTO = "auto"  # Авторежим: роутер выбирает сам


PROVIDER_DISPLAY_NAMES: dict[ProviderType, str] = {
    ProviderType.GEMINI_OAUTH: "☁️ Gemini OAuth (бесплатная квота)",
    ProviderType.GEMINI_API: "💳 Gemini API (платный ключ)",
    ProviderType.OPENAI_OAUTH: "🤖 OpenAI ChatGPT Plus (OAuth)",
    ProviderType.OPENAI_API: "💳 OpenAI API (платный ключ)",
    ProviderType.LM_STUDIO: "💻 LM Studio (локально, бесплатно)",
    ProviderType.AUTO: "🎯 Авто (роутер выбирает лучшее)",
}


# ═══════════════════════════════════════════════════════════════════════════════
# КАТАЛОГ МОДЕЛЕЙ — максимально расширенный
# ═══════════════════════════════════════════════════════════════════════════════

PROVIDER_MODELS: dict[ProviderType, list[dict]] = {
    # ── Gemini OAuth (Antigravity free VSCode quota) ───────────────────────────
    ProviderType.GEMINI_OAUTH: [
        # Gemini 3.x серия (через маскировку под VSCode/Antigravity)
        # ИСПРАВЛЕНО: gemini-3.1-pro не существует → default=False, gemini-3-flash — реальная рабочая модель
        {
            "id": "google-antigravity/gemini-3.1-pro",
            "name": "Gemini 3.1 Pro ⭐",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "pro",
        },
        {
            "id": "google-antigravity/gemini-3.1-ultra",
            "name": "Gemini 3.1 Ultra 🧠",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "ultra",
        },
        {
            "id": "google-antigravity/gemini-3-pro",
            "name": "Gemini 3 Pro (умный)",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "pro",
        },
        {
            "id": "google-antigravity/gemini-3-flash",
            "name": "Gemini 3 Flash ✅ (быстрый)",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "flash",
        },
        {
            "id": "google-antigravity/gemini-3-flash-thinking",
            "name": "Gemini 3 Flash Thinking 💭",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "flash",
        },
        # Gemini 2.x серия
        {
            "id": "google-antigravity/gemini-2.5-pro",
            "name": "Gemini 2.5 Pro",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "pro",
        },
        {
            "id": "google-antigravity/gemini-2.5-flash",
            "name": "Gemini 2.5 Flash",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "flash",
        },
        {
            "id": "google-antigravity/gemini-2.0-flash-thinking",
            "name": "Gemini 2.0 Flash Thinking 💭",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "flash",
        },
        # Gemini 1.x / Exp серия
        {
            "id": "google-antigravity/gemini-exp-1206",
            "name": "Gemini Exp 1206",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "exp",
        },
        {
            "id": "google-antigravity/gemini-exp-1121",
            "name": "Gemini Exp 1121",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "exp",
        },
    ],
    # ── Gemini API (paid Google AI Studio key) ────────────────────────────────
    ProviderType.GEMINI_API: [
        # Gemini 3.x (Preview / GA)
        # ИСПРАВЛЕНО: gemini-3-pro-preview не существует реально → default снят
        {
            "id": "gemini-3-pro-preview",
            "name": "Gemini 3 Pro Preview",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "pro",
        },
        {
            "id": "google/gemini-3-pro-preview",
            "name": "Gemini 3 Pro Preview (pfx)",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "pro",
        },
        {
            "id": "gemini-3-ultra",
            "name": "Gemini 3 Ultra 🧠",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "ultra",
        },
        {
            "id": "gemini-3-flash",
            "name": "Gemini 3 Flash",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "flash",
        },
        {
            "id": "gemini-3-flash-thinking-exp",
            "name": "Gemini 3 Flash Thinking 💭",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "flash",
        },
        # Gemini 2.5 — реальные рабочие модели
        {
            "id": "gemini-2.5-pro-preview",
            "name": "Gemini 2.5 Pro Preview",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "pro",
        },
        {
            "id": "gemini-2.5-flash-preview",
            "name": "Gemini 2.5 Flash Preview",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "flash",
        },
        {
            "id": "gemini-2.5-flash",
            "name": "Gemini 2.5 Flash ✅",
            "vision": True,
            "thinking": False,
            "default": True,
            "tier": "flash",
        },
        {
            "id": "google/gemini-2.5-flash",
            "name": "Gemini 2.5 Flash (pfx)",
            "vision": False,
            "thinking": False,
            "default": False,
            "tier": "flash",
        },
        # Gemini 2.0
        {
            "id": "gemini-2.0-flash",
            "name": "Gemini 2.0 Flash",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "flash",
        },
        {
            "id": "gemini-2.0-flash-thinking-exp",
            "name": "Gemini 2.0 Flash Thinking 💭",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "flash",
        },
        {
            "id": "gemini-2.0-pro-exp",
            "name": "Gemini 2.0 Pro Exp",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "pro",
        },
        # Gemini 1.5
        {
            "id": "gemini-1.5-pro",
            "name": "Gemini 1.5 Pro",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "pro",
        },
        {
            "id": "gemini-1.5-pro-latest",
            "name": "Gemini 1.5 Pro Latest",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "pro",
        },
        {
            "id": "gemini-1.5-flash",
            "name": "Gemini 1.5 Flash",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "flash",
        },
        {
            "id": "gemini-pro-latest",
            "name": "Gemini Pro Latest",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "pro",
        },
    ],
    # ── OpenAI ChatGPT Plus (OAuth через openai-codex) ─────────────────────────
    ProviderType.OPENAI_OAUTH: [
        # GPT-5 серия (2026)
        {
            "id": "openai/chatgpt-5",
            "name": "ChatGPT 5 ⭐ (новейший)",
            "vision": True,
            "thinking": True,
            "default": True,
            "tier": "flagship",
        },
        {
            "id": "openai/chatgpt-5.4",
            "name": "ChatGPT 5.4 🧠",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "flagship",
        },
        {
            "id": "openai/chatgpt-5.3-codex",
            "name": "ChatGPT 5.3 Codex 💻",
            "vision": False,
            "thinking": True,
            "default": False,
            "tier": "flagship",
        },
        {
            "id": "openai/gpt-5",
            "name": "GPT-5",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "flagship",
        },
        # o-серия (reasoning)
        {
            "id": "openai/o3",
            "name": "o3 🧠 (глубокое рассуждение)",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "reasoning",
        },
        {
            "id": "openai/o3-mini",
            "name": "o3 Mini (быстрое рассуждение)",
            "vision": False,
            "thinking": True,
            "default": False,
            "tier": "reasoning",
        },
        {
            "id": "openai/o3-mini-high",
            "name": "o3 Mini High (макс. глубина)",
            "vision": False,
            "thinking": True,
            "default": False,
            "tier": "reasoning",
        },
        {
            "id": "openai/o1",
            "name": "o1 (рассуждения)",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "reasoning",
        },
        {
            "id": "openai/o1-mini",
            "name": "o1 Mini",
            "vision": False,
            "thinking": True,
            "default": False,
            "tier": "reasoning",
        },
        {
            "id": "openai/o1-preview",
            "name": "o1 Preview",
            "vision": False,
            "thinking": True,
            "default": False,
            "tier": "reasoning",
        },
        # GPT-4.x серия
        {
            "id": "openai/gpt-4o",
            "name": "GPT-4o (умный + быстрый)",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "standard",
        },
        {
            "id": "openai/gpt-4o-mini",
            "name": "GPT-4o Mini (быстрый)",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "standard",
        },
        {
            "id": "openai/gpt-4-turbo",
            "name": "GPT-4 Turbo",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "standard",
        },
        # Codex / специализированные
        {
            "id": "openai/codex-mini",
            "name": "Codex Mini (код)",
            "vision": False,
            "thinking": False,
            "default": False,
            "tier": "code",
        },
        {
            "id": "openai/codex",
            "name": "Codex (программирование)",
            "vision": False,
            "thinking": False,
            "default": False,
            "tier": "code",
        },
    ],
    # ── OpenAI API (paid API key) ─────────────────────────────────────────────
    ProviderType.OPENAI_API: [
        # Флагманы
        {
            "id": "openai/gpt-4o",
            "name": "GPT-4o ⭐",
            "vision": True,
            "thinking": False,
            "default": True,
            "tier": "standard",
        },
        {
            "id": "openai/gpt-4o-mini",
            "name": "GPT-4o Mini (быстрый)",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "standard",
        },
        {
            "id": "openai/gpt-4-turbo",
            "name": "GPT-4 Turbo",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "standard",
        },
        # o-серия
        {
            "id": "openai/o3",
            "name": "o3 🧠",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "reasoning",
        },
        {
            "id": "openai/o3-mini",
            "name": "o3 Mini",
            "vision": False,
            "thinking": True,
            "default": False,
            "tier": "reasoning",
        },
        {
            "id": "openai/o1",
            "name": "o1",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "reasoning",
        },
        {
            "id": "openai/o1-mini",
            "name": "o1 Mini",
            "vision": False,
            "thinking": True,
            "default": False,
            "tier": "reasoning",
        },
        # GPT-5 (Future)
        {
            "id": "openai/gpt-5",
            "name": "GPT-5 (если доступен)",
            "vision": True,
            "thinking": True,
            "default": False,
            "tier": "flagship",
        },
    ],
    # ── LM Studio (локальные модели) ──────────────────────────────────────────
    ProviderType.LM_STUDIO: [
        {
            "id": "nvidia/nemotron-3-nano",
            "name": "Nemotron 3 Nano ⭐ (быстрый)",
            "vision": False,
            "thinking": False,
            "default": True,
            "tier": "nano",
        },
        {
            "id": "qwen/qwen2.5-7b-instruct",
            "name": "Qwen 2.5 7B",
            "vision": False,
            "thinking": False,
            "default": False,
            "tier": "small",
        },
        {
            "id": "qwen/qwen2.5-14b-instruct",
            "name": "Qwen 2.5 14B",
            "vision": False,
            "thinking": False,
            "default": False,
            "tier": "medium",
        },
        {
            "id": "qwen/qwen2.5-vl-7b-instruct",
            "name": "Qwen 2.5 VL 7B 📷 (vision)",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "small",
        },
        {
            "id": "llama/llama-3.2-11b-vision",
            "name": "Llama 3.2 11B Vision 📷",
            "vision": True,
            "thinking": False,
            "default": False,
            "tier": "medium",
        },
        {
            "id": "deepseek/deepseek-r1",
            "name": "DeepSeek R1 💭 (reasoning)",
            "vision": False,
            "thinking": True,
            "default": False,
            "tier": "reasoning",
        },
        {
            "id": "mistral/mistral-small",
            "name": "Mistral Small",
            "vision": False,
            "thinking": False,
            "default": False,
            "tier": "small",
        },
        # Динамически обнаруживаемые (добавляются при !model scan)
    ],
}


# ═══════════════════════════════════════════════════════════════════════════════
# THINKING / REASONING НАСТРОЙКИ
# ═══════════════════════════════════════════════════════════════════════════════


class ThinkingDepth(str, Enum):
    """Глубина reasoning при поддерживаемых моделях."""

    OFF = "off"  # Без рассуждений
    LOW = "low"  # Минимальные токены думания (быстро)
    MEDIUM = "medium"  # Баланс (по умолчанию)
    HIGH = "high"  # Максимальная глубина (медленно, но точнее)
    AUTO = "auto"  # Модель сама решает


THINKING_DEPTH_DISPLAY: dict[ThinkingDepth, str] = {
    ThinkingDepth.OFF: "⬛ Выкл (нет рассуждений)",
    ThinkingDepth.LOW: "🟦 Низкий (быстро)",
    ThinkingDepth.MEDIUM: "🟨 Средний (баланс)",
    ThinkingDepth.HIGH: "🟥 Высокий (глубокий анализ)",
    ThinkingDepth.AUTO: "🎯 Авто (модель решает)",
}

# Маппинг глубины на параметры API
THINKING_DEPTH_PARAMS: dict[ThinkingDepth, dict] = {
    ThinkingDepth.OFF: {"thinking": False, "budget_tokens": 0},
    ThinkingDepth.LOW: {"thinking": True, "budget_tokens": 1024},
    ThinkingDepth.MEDIUM: {"thinking": True, "budget_tokens": 8192},
    ThinkingDepth.HIGH: {"thinking": True, "budget_tokens": 32768},
    ThinkingDepth.AUTO: {"thinking": True, "budget_tokens": None},
}


# ═══════════════════════════════════════════════════════════════════════════════
# FALLBACK CONFIG
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class FallbackConfig:
    """Настройки fallback-цепочки провайдеров."""

    chain: list[ProviderType] = field(
        default_factory=lambda: [
            ProviderType.GEMINI_OAUTH,
            ProviderType.GEMINI_API,
            ProviderType.OPENAI_OAUTH,
            ProviderType.LM_STUDIO,
        ]
    )
    max_attempts: int = 3
    lm_studio_as_last_resort: bool = False


@dataclass
class QuotaInfo:
    """Информация об использовании квоты (токены/запросы)."""

    used_tokens: int = 0
    limit_tokens: int = 0  # 0 = безлимит или неизвестно
    requests_count: int = 0
    last_reset: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        limit = self.limit_tokens
        used = self.used_tokens
        percent = (used / limit * 100.0) if limit > 0 else 0.0
        return {
            "used": used,
            "limit": limit,
            "requests": self.requests_count,
            "percentage": round(min(100.0, percent), 1),
            "label": f"{used}/{limit or '∞'}",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# СОСТОЯНИЕ ПРОВАЙДЕРА
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ProviderState:
    """Текущее состояние: провайдер, модель, thinking-режим, fallback."""

    provider: ProviderType = ProviderType.AUTO
    model_id: str = ""
    vision_model_id: str = ""
    thinking_depth: ThinkingDepth = ThinkingDepth.AUTO
    # Максимальный output_tokens (0 = дефолт модели)
    max_output_tokens: int = 0
    # Temperature (0.0-2.0, -1 = дефолт)
    temperature: float = -1.0
    fallback: FallbackConfig = field(default_factory=FallbackConfig)
    # Квоты по типам провайдеров
    quotas: dict[ProviderType, QuotaInfo] = field(
        default_factory=lambda: {pt: QuotaInfo() for pt in ProviderType if pt != ProviderType.AUTO}
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER MANAGER (SINGLETON)
# ═══════════════════════════════════════════════════════════════════════════════


class ProviderManager:
    """
    Синглтон-менеджер провайдеров.

    Отвечает за:
    - Хранение текущего провайдера, модели, thinking-настроек
    - Формирование fallback-цепочек с фильтром доступности
    - Проверку доступности провайдеров (токены/ключи)
    - REST-совместимый экспорт состояния для веб-панели
    """

    _STATE_FILE = os.path.expanduser("~/.openclaw/krab_provider_state.json")

    def __init__(self) -> None:
        self._state = ProviderState()
        self._load_state()

    # ── Персистентность ───────────────────────────────────────────────────────

    def _load_state(self) -> None:
        if not os.path.exists(self._STATE_FILE):
            return
        try:
            with open(self._STATE_FILE) as f:
                data = json.load(f)
            self._state.provider = ProviderType(data.get("provider", ProviderType.AUTO.value))
            self._state.model_id = data.get("model_id", "")
            self._state.vision_model_id = data.get("vision_model_id", "")
            self._state.max_output_tokens = int(data.get("max_output_tokens", 0))
            self._state.temperature = float(data.get("temperature", -1.0))
            try:
                self._state.thinking_depth = ThinkingDepth(
                    data.get("thinking_depth", ThinkingDepth.AUTO.value)
                )
            except ValueError:
                self._state.thinking_depth = ThinkingDepth.AUTO
            fb = data.get("fallback", {})
            if fb.get("chain"):
                try:
                    self._state.fallback.chain = [ProviderType(p) for p in fb["chain"]]
                except ValueError:
                    pass
            self._state.fallback.max_attempts = int(fb.get("max_attempts", 3))
            self._state.fallback.lm_studio_as_last_resort = bool(
                fb.get("lm_studio_as_last_resort", False)
            )

            # Загрузка квот
            quotas_data = data.get("quotas", {})
            for pt_val, q_data in quotas_data.items():
                try:
                    pt = ProviderType(pt_val)
                    if pt in self._state.quotas:
                        q = self._state.quotas[pt]
                        q.used_tokens = q_data.get("used_tokens", 0)
                        q.limit_tokens = q_data.get("limit_tokens", 0)
                        q.requests_count = q_data.get("requests_count", 0)
                        q.last_reset = q_data.get("last_reset", time.time())
                except ValueError:
                    continue

            logger.info("provider_state_loaded", provider=self._state.provider.value)
        except Exception as exc:  # noqa: BLE001
            logger.warning("provider_state_load_failed", error=str(exc))

    def _save_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._STATE_FILE), exist_ok=True)
            data = {
                "provider": self._state.provider.value,
                "model_id": self._state.model_id,
                "vision_model_id": self._state.vision_model_id,
                "thinking_depth": self._state.thinking_depth.value,
                "max_output_tokens": self._state.max_output_tokens,
                "temperature": self._state.temperature,
                "fallback": {
                    "chain": [p.value for p in self._state.fallback.chain],
                    "max_attempts": self._state.fallback.max_attempts,
                    "lm_studio_as_last_resort": self._state.fallback.lm_studio_as_last_resort,
                },
                "quotas": {
                    pt.value: {
                        "used_tokens": q.used_tokens,
                        "limit_tokens": q.limit_tokens,
                        "requests_count": q.requests_count,
                        "last_reset": q.last_reset,
                    }
                    for pt, q in self._state.quotas.items()
                },
            }
            with open(self._STATE_FILE, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("provider_state_save_failed", error=str(exc))

    # ── Доступность провайдеров ───────────────────────────────────────────────

    def is_provider_available(self, provider: ProviderType) -> bool:
        auth_file = os.path.expanduser("~/.openclaw/agents/main/agent/auth-profiles.json")

        if provider == ProviderType.GEMINI_OAUTH:
            if not os.path.exists(auth_file):
                return False
            try:
                with open(auth_file) as f:
                    prof = json.load(f)
                return any(
                    p.get("provider") == "google-antigravity" and p.get("access")
                    for p in prof.get("profiles", {}).values()
                )
            except Exception:  # noqa: BLE001
                return False

        if provider == ProviderType.OPENAI_OAUTH:
            if not os.path.exists(auth_file):
                return False
            try:
                with open(auth_file) as f:
                    prof = json.load(f)
                return any(
                    p.get("provider") == "openai-codex" and p.get("access")
                    for p in prof.get("profiles", {}).values()
                )
            except Exception:  # noqa: BLE001
                return False

        if provider == ProviderType.GEMINI_API:
            return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))

        if provider == ProviderType.OPENAI_API:
            return bool(os.getenv("OPENAI_API_KEY"))

        if provider in (ProviderType.LM_STUDIO, ProviderType.AUTO):
            return True

        return False

    def get_available_providers(self) -> list[ProviderType]:
        return [p for p in ProviderType if self.is_provider_available(p)]

    # ── Getters / Setters ─────────────────────────────────────────────────────

    @property
    def active_provider(self) -> ProviderType:
        return self._state.provider

    @property
    def active_model_id(self) -> str:
        return self._state.model_id or self.get_default_model_for_provider(self._state.provider)

    @property
    def active_vision_model_id(self) -> str:
        if self._state.vision_model_id:
            return self._state.vision_model_id
        for m in PROVIDER_MODELS.get(self._state.provider, []):
            if m.get("vision"):
                return m["id"]
        return self.active_model_id

    @property
    def thinking_depth(self) -> ThinkingDepth:
        return self._state.thinking_depth

    @property
    def thinking_params(self) -> dict:
        """Параметры thinking для передачи в API."""
        return THINKING_DEPTH_PARAMS.get(self._state.thinking_depth, {})

    def get_default_model_for_provider(self, provider: ProviderType) -> str:
        for m in PROVIDER_MODELS.get(provider, []):
            if m.get("default"):
                return m["id"]
        models = PROVIDER_MODELS.get(provider, [])
        return models[0]["id"] if models else ""

    def get_models_for_provider(self, provider: ProviderType) -> list[dict]:
        return PROVIDER_MODELS.get(provider, [])

    # ── Управление состоянием ─────────────────────────────────────────────────

    def set_provider(self, provider: ProviderType, model_id: str = "") -> None:
        self._state.provider = provider
        self._state.model_id = model_id
        self._save_state()
        logger.info("provider_switched", provider=provider.value, model=model_id)

    def set_model(self, model_id: str) -> None:
        self._state.model_id = model_id
        self._save_state()
        logger.info("model_switched", model=model_id)

    def set_vision_model(self, model_id: str) -> None:
        self._state.vision_model_id = model_id
        self._save_state()

    def set_thinking_depth(self, depth: ThinkingDepth) -> None:
        self._state.thinking_depth = depth
        self._save_state()
        logger.info("thinking_depth_changed", depth=depth.value)

    def set_temperature(self, temp: float) -> None:
        self._state.temperature = max(-1.0, min(2.0, temp))
        self._save_state()

    def set_max_output_tokens(self, tokens: int) -> None:
        self._state.max_output_tokens = max(0, tokens)
        self._save_state()

    def set_fallback_chain(self, chain: list[ProviderType]) -> None:
        self._state.fallback.chain = chain
        self._save_state()

    def set_lm_studio_last_resort(self, enabled: bool) -> None:
        self._state.fallback.lm_studio_as_last_resort = enabled
        self._save_state()

    def report_usage(self, provider: ProviderType, tokens: int) -> None:
        """Регистрирует использование токенов для провайдера."""
        if provider == ProviderType.AUTO:
            return
        if provider in self._state.quotas:
            q = self._state.quotas[provider]
            q.used_tokens += tokens
            q.requests_count += 1
            # Сохраняем не на каждый запрос, а периодически или при выходе
            # Но для надежности пока будем сохранять чаще
            self._save_state()

    def get_fallback_chain(self) -> list[ProviderType]:
        available = set(self.get_available_providers())
        return [
            p
            for p in self._state.fallback.chain
            if p in available
            or (p == ProviderType.LM_STUDIO and self._state.fallback.lm_studio_as_last_resort)
        ]

    # ── Резолвинг для openclaw_client ─────────────────────────────────────────

    def resolve_config_for_provider(self, provider: ProviderType) -> dict:
        """Возвращает runtime-конфиг для openclaw_client."""
        thinking_p = self.thinking_params
        result: dict = {
            "model_id": self.get_default_model_for_provider(provider),
            "force_cloud": provider != ProviderType.LM_STUDIO,
            "provider_type": provider.value,
            "thinking_enabled": bool(thinking_p.get("thinking", False)),
            "thinking_budget_tokens": thinking_p.get("budget_tokens"),
        }
        if self._state.max_output_tokens > 0:
            result["max_output_tokens"] = self._state.max_output_tokens
        if self._state.temperature >= 0:
            result["temperature"] = self._state.temperature
        return result

    # ── REST API: экспорт/импорт состояния для веб-панели ─────────────────────

    def to_api_dict(self) -> dict:
        """Полное состояние для REST /api/provider."""
        available = set(self.get_available_providers())
        return {
            "active": {
                "provider": self._state.provider.value,
                "provider_display": PROVIDER_DISPLAY_NAMES.get(self._state.provider, ""),
                "model_id": self.active_model_id,
                "vision_model_id": self.active_vision_model_id,
                "thinking_depth": self._state.thinking_depth.value,
                "thinking_depth_display": THINKING_DEPTH_DISPLAY.get(
                    self._state.thinking_depth, ""
                ),
                "temperature": self._state.temperature,
                "max_output_tokens": self._state.max_output_tokens,
                "force_cloud": self._state.provider != ProviderType.LM_STUDIO,
                "quota": self._state.quotas.get(self._state.provider, QuotaInfo()).to_dict()
                if self._state.provider != ProviderType.AUTO
                else {},
            },
            "providers": [
                {
                    "id": p.value,
                    "name": PROVIDER_DISPLAY_NAMES.get(p, p.value),
                    "available": p in available,
                    "active": p == self._state.provider,
                    "models": PROVIDER_MODELS.get(p, []),
                    "quota": self._state.quotas.get(p, QuotaInfo()).to_dict(),
                }
                for p in ProviderType
                if p != ProviderType.AUTO
            ],
            "fallback": {
                "chain": [p.value for p in self._state.fallback.chain],
                "chain_display": [
                    PROVIDER_DISPLAY_NAMES.get(p, p.value) for p in self._state.fallback.chain
                ],
                "effective_chain": [p.value for p in self.get_fallback_chain()],
                "max_attempts": self._state.fallback.max_attempts,
                "lm_studio_as_last_resort": self._state.fallback.lm_studio_as_last_resort,
            },
            "thinking_options": [
                {"id": d.value, "display": THINKING_DEPTH_DISPLAY[d]} for d in ThinkingDepth
            ],
        }

    # ── Форматирование для Telegram ───────────────────────────────────────────

    def format_status(self) -> str:
        provider = self._state.provider
        display = PROVIDER_DISPLAY_NAMES.get(provider, str(provider))
        model = self.active_model_id or "дефолт"
        vision = self._state.vision_model_id or "(auto)"
        thinking = THINKING_DEPTH_DISPLAY.get(self._state.thinking_depth, "?")
        available = self.get_available_providers()
        chain = self.get_fallback_chain()

        avail_lines = "\n".join(
            f"  {'✅' if p in available else '❌'} {PROVIDER_DISPLAY_NAMES.get(p, p.value)}"
            for p in ProviderType
            if p != ProviderType.AUTO
        )

        quota = self._state.quotas.get(provider, QuotaInfo())
        quota_dict = quota.to_dict()
        quota_str = f"{quota.used_tokens} / {quota.limit_tokens or '∞'}"
        if quota.limit_tokens > 0:
            pct = quota_dict["percentage"]
            quota_str += f" ({pct}% 🔴)" if pct > 80 else f" ({pct}%)"

        chain_str = " → ".join(p.value for p in chain) or "нет"
        temp_str = (
            f"  {self._state.temperature:.1f}" if self._state.temperature >= 0 else "  дефолт"
        )
        tokens_str = (
            str(self._state.max_output_tokens) if self._state.max_output_tokens else "дефолт"
        )

        return (
            f"🔌 **Активный провайдер:** {display}\n"
            f"🧠 **Модель:** `{model}`\n"
            f"📷 **Vision:** `{vision}`\n"
            f"💰 **Квота:** `{quota_str}`\n"
            f"💭 **Thinking-режим:** {thinking}\n"
            f"🌡️ **Temperature:** `{temp_str}`\n"
            f"📏 **Max tokens:** `{tokens_str}`\n\n"
            f"📡 **Провайдеры:**\n{avail_lines}\n\n"
            f"🔗 **Fallback:** `{chain_str}`\n\n"
            f"_Команды:_ `!provider list`, `!provider set <p>`, "
            f"`!provider model <id>`, `!provider thinking <low/med/high/off>`, "
            f"`!provider fallback <p1> [p2]`, `!provider temp <0.0-2.0>`"
        )

    def format_provider_list(self) -> str:
        """Детальный список всех провайдеров с моделями (группировка по tier)."""
        available = set(self.get_available_providers())
        lines = ["🔌 **Все провайдеры и модели:**\n"]
        for ptype, pname in PROVIDER_DISPLAY_NAMES.items():
            if ptype == ProviderType.AUTO:
                continue
            status = "✅" if ptype in available else "❌"
            active_mark = " ← **АКТИВЕН**" if ptype == self._state.provider else ""
            lines.append(f"{status} **{pname}**{active_mark}")
            models = PROVIDER_MODELS.get(ptype, [])
            current_tier = None
            for m in models:
                tier = m.get("tier", "")
                if tier != current_tier:
                    current_tier = tier
                    tier_labels = {
                        "flagship": "🚀 Флагман",
                        "ultra": "🔮 Ultra",
                        "pro": "💼 Pro",
                        "standard": "📊 Standard",
                        "flash": "⚡ Flash",
                        "reasoning": "💭 Reasoning",
                        "code": "💻 Code",
                        "exp": "🧪 Experimental",
                        "nano": "⚡ Nano",
                        "small": "🔹 Small",
                        "medium": "🔷 Medium",
                    }
                    if tier in tier_labels:
                        lines.append(f"  ─── {tier_labels[tier]} ───")
                default_mark = " ⭐" if m.get("default") else ""
                vision_mark = " 📷" if m.get("vision") else ""
                think_mark = " 💭" if m.get("thinking") else ""
                cur = " ✅" if m["id"] == self._state.model_id else ""
                lines.append(
                    f"    `{m['id']}` — {m['name']}{default_mark}{vision_mark}{think_mark}{cur}"
                )
            lines.append("")
        lines.append(
            "Легенда: ⭐ дефолтная · 📷 vision (фото) · 💭 thinking · ✅ текущая\n"
            "Выбрать: `!provider set gemini_oauth` или `!provider model <id>`"
        )
        return "\n".join(lines)

    def format_thinking_help(self) -> str:
        current = self._state.thinking_depth
        lines = ["💭 **Thinking / Reasoning режим:**\n"]
        for d, display in THINKING_DEPTH_DISPLAY.items():
            cur = " ← **АКТИВЕН**" if d == current else ""
            params = THINKING_DEPTH_PARAMS[d]
            budget = params.get("budget_tokens")
            budget_str = (
                f"({budget} токенов)"
                if budget
                else "(авто)"
                if d == ThinkingDepth.AUTO
                else "(нет)"
            )
            lines.append(f"  `{d.value}` — {display} {budget_str}{cur}")
        lines.append(
            "\nМодели с thinking: `o3`, `o1`, `gemini-3.*-thinking`, `deepseek-r1`\n"
            "Установить: `!provider thinking high` / `!provider thinking off`"
        )
        return "\n".join(lines)


# ── Синглтон ──────────────────────────────────────────────────────────────────

provider_manager = ProviderManager()
