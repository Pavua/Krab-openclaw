# -*- coding: utf-8 -*-
"""
translator_session_state.py — persisted session state для Translator.

Хранит control-level состояние текущей сессии: статус, метки, мут, timeline и т.п.
Не является live source-of-truth звонка — это product-layer snapshot для команд и UI.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def default_translator_session_state() -> dict[str, Any]:
    """Возвращает каноническое idle-состояние translator session."""
    return {
        "session_status": "idle",
        "session_id": "",
        "active_session_label": "",
        "translation_muted": False,
        "active_chats": [],  # чаты в которых translator активен (per-chat opt-in)
        "last_language_pair": "",
        "last_translated_original": "",
        "last_translated_translation": "",
        "history": [],  # последние 20 переводов: {src_lang, tgt_lang, original, translation, latency_ms, timestamp}
        "last_event": "session_idle",
        "updated_at": "",
        "stats": {"total_translations": 0, "total_latency_ms": 0},
        "timeline_summary": {"total": 0, "line_events": 0, "control_events": 0},
        "timeline_preview": [],
        "timeline_event_count": 0,
    }


def load_translator_session_state(path: Path) -> dict[str, Any]:
    """Загружает persisted session state или возвращает defaults при отсутствии файла."""
    try:
        if path.exists():
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                base = default_translator_session_state()
                base.update(data)
                return base
    except Exception:
        pass
    return default_translator_session_state()


def apply_translator_session_update(
    changes: dict[str, Any],
    *,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Применяет изменения к базовому state и нормализует типы.

    Автоматически обновляет `updated_at` при любых изменениях.
    """
    result: dict[str, Any] = dict(base or default_translator_session_state())
    if not changes:
        return result

    for key, value in changes.items():
        existing = result.get(key)
        if isinstance(existing, bool):
            if isinstance(value, bool):
                result[key] = value
            elif isinstance(value, str):
                result[key] = value.strip().lower() in {"1", "true", "yes", "on"}
            else:
                result[key] = bool(value)
        elif isinstance(existing, list):
            result[key] = list(value) if value is not None else []
        elif isinstance(existing, dict):
            if isinstance(value, dict):
                merged = dict(existing)
                merged.update(value)
                result[key] = merged
            else:
                result[key] = value
        elif isinstance(existing, int):
            try:
                result[key] = int(value) if value is not None else 0
            except (TypeError, ValueError):
                result[key] = existing
        else:
            result[key] = value

    result["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return result


HISTORY_MAX = 20  # максимальное количество записей в history


def append_translator_history_entry(
    state: dict[str, Any],
    *,
    src_lang: str,
    tgt_lang: str,
    original: str,
    translation: str,
    latency_ms: int,
) -> dict[str, Any]:
    """
    Добавляет новую запись в history переводов, ограничивая список до HISTORY_MAX.

    Возвращает обновлённый state (не мутирует переданный).
    """
    entry = {
        "src_lang": src_lang,
        "tgt_lang": tgt_lang,
        "original": original[:300],
        "translation": translation[:300],
        "latency_ms": latency_ms,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    history: list[dict] = list(state.get("history") or [])
    history.append(entry)
    if len(history) > HISTORY_MAX:
        history = history[-HISTORY_MAX:]
    return {**state, "history": history}


def save_translator_session_state(path: Path, state: dict[str, Any]) -> None:
    """Сохраняет session state в JSON-файл, создавая parent-каталоги при необходимости."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
