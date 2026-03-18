# -*- coding: utf-8 -*-
"""
translator_runtime_profile.py — persisted runtime profile для Translator.

Хранит настройки owner-уровня, которые не зависят от конкретного live-звонка:
языковую пару, режим перевода, стратегию голоса, быстрые фразы и т.п.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Allowed values (used for validation in command_handlers)
# ---------------------------------------------------------------------------

ALLOWED_LANGUAGE_PAIRS: frozenset[str] = frozenset({"es-ru", "es-en", "en-ru", "auto-detect"})
ALLOWED_TRANSLATION_MODES: frozenset[str] = frozenset({"bilingual", "auto_to_ru", "auto_to_en"})
ALLOWED_VOICE_STRATEGIES: frozenset[str] = frozenset({"voice-first", "subtitles-first"})

# ---------------------------------------------------------------------------
# Default profile
# ---------------------------------------------------------------------------

default_translator_runtime_profile: dict[str, Any] = {
    "language_pair": "es-ru",
    "translation_mode": "bilingual",
    "voice_strategy": "voice-first",
    "target_device": "iphone_companion",
    "ordinary_calls_enabled": True,
    "internet_calls_enabled": True,
    "subtitles_enabled": True,
    "timeline_enabled": True,
    "summary_enabled": True,
    "diagnostics_enabled": False,
    "quick_phrases": [],
}

# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def load_translator_runtime_profile(path: Path) -> dict[str, Any]:
    """Загружает persisted profile или возвращает defaults при отсутствии файла."""
    try:
        if path.exists():
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                merged = dict(default_translator_runtime_profile)
                merged.update(data)
                return merged
    except Exception:
        pass
    return dict(default_translator_runtime_profile)


def normalize_translator_runtime_profile(
    changes: dict[str, Any],
    *,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Мёрджит изменения в base и нормализует типы.

    Возвращает полный нормализованный профиль.
    """
    result: dict[str, Any] = dict(base or default_translator_runtime_profile)
    for key, value in changes.items():
        if key not in result:
            # Ignore unknown keys to avoid schema drift
            continue
        # Coerce booleans
        if isinstance(result[key], bool):
            if isinstance(value, bool):
                result[key] = value
            elif isinstance(value, str):
                result[key] = value.strip().lower() in {"1", "true", "yes", "on"}
            else:
                result[key] = bool(value)
        elif isinstance(result[key], list):
            result[key] = list(value) if value is not None else []
        elif isinstance(result[key], str):
            result[key] = str(value) if value is not None else ""
        else:
            result[key] = value
    return result


def save_translator_runtime_profile(path: Path, profile: dict[str, Any]) -> None:
    """Сохраняет profile в JSON-файл, создавая parent-каталоги при необходимости."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
