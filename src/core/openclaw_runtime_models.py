# -*- coding: utf-8 -*-
"""
Хелперы чтения runtime-model truth из `~/.openclaw/openclaw.json`.

Зачем нужен отдельный модуль:
- repo-код Краба не должен полагаться на stale `.env` для выбора primary/fallback;
- owner UI, userbot и Python-клиент должны смотреть в один и тот же источник истины;
- так проще переживать дрейф между локальными env-переменными и live OpenClaw runtime.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_RUNTIME_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"


def read_runtime_model_defaults() -> dict[str, Any]:
    """Возвращает секцию `agents.defaults.model` из live OpenClaw runtime."""
    try:
        payload = json.loads(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}

    agents = payload.get("agents") if isinstance(payload, dict) else {}
    defaults = agents.get("defaults") if isinstance(agents, dict) else {}
    model_defaults = defaults.get("model") if isinstance(defaults, dict) else {}
    return dict(model_defaults) if isinstance(model_defaults, dict) else {}


def get_runtime_primary_model() -> str:
    """Возвращает live primary-модель OpenClaw или пустую строку."""
    model_defaults = read_runtime_model_defaults()
    return str(model_defaults.get("primary", "") or "").strip()


def get_runtime_fallback_models() -> list[str]:
    """Возвращает live fallback-цепочку OpenClaw без пустых значений."""
    model_defaults = read_runtime_model_defaults()
    return [
        str(item).strip()
        for item in (model_defaults.get("fallbacks") or [])
        if str(item or "").strip()
    ]
