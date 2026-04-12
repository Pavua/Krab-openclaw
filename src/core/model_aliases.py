# -*- coding: utf-8 -*-
"""
Model alias resolution and presets (extracted from old handlers/commands.py).
Used by the web panel and command handlers for human-friendly model names.
"""

from __future__ import annotations

MODEL_FRIENDLY_ALIASES: dict[str, str] = {
    # Gemini
    "gemini-flash": "google/gemini-2.5-flash",
    "gemini-2.5-flash": "google/gemini-2.5-flash",
    "gemini-flash-latest": "google/gemini-2.5-flash",
    # На 2026-03-08 актуальный "умный" preview-таргет — 3.1 pro.
    # Старые алиасы `gemini-3-pro*` сохраняем совместимыми и ведём на новый id.
    "gemini-3-pro": "google/gemini-3.1-pro-preview",
    "gemini-3-pro-latest": "google/gemini-3.1-pro-preview",
    "gemini-pro": "google/gemini-3.1-pro-preview",
    "gemini-pro-latest": "google/gemini-3.1-pro-preview",
    "gemini-3.1-pro": "google/gemini-3.1-pro-preview",
    "gemini-3.1-pro-preview": "google/gemini-3.1-pro-preview",
    "gemini-2.5-pro": "google/gemini-2.5-pro",
    # OpenAI
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "gpt-4.1-mini": "openai/gpt-4.1-mini",
    "gpt-5-mini": "openai/gpt-5-mini",
    "gpt-5": "openai/gpt-5",
    "gpt-5-chat": "openai/gpt-5-chat-latest",
    "gpt-5-codex": "openai/gpt-5-codex",
    "o3": "openai/o3",
    "o4-mini": "openai/o4-mini",
}


def normalize_model_alias(raw_model_name: str) -> tuple[str, str]:
    """
    Normalizes human-friendly model aliases to canonical provider/model format.
    Returns (resolved_id, info_message).
    """
    original = str(raw_model_name or "").strip()
    if not original:
        return "", ""

    canonical = "-".join(original.lower().replace("_", "-").split())
    resolved = MODEL_FRIENDLY_ALIASES.get(canonical, original)

    if "/" not in resolved:
        lowered = resolved.lower()
        if lowered.startswith("gemini"):
            resolved = f"google/{resolved}"
        elif lowered.startswith(("gpt", "o1", "o3", "o4", "o5", "codex")):
            resolved = f"openai/{resolved}"

    if resolved == original:
        return resolved, ""
    return resolved, f"ℹ️ Алиас: `{original}` → `{resolved}`"


def parse_model_set_request(args: list[str], valid_slots: list[str]) -> dict[str, str | bool]:
    """
    Parses `!model set` arguments in canonical and legacy format.
    """
    slots_sorted = sorted({str(slot).strip().lower() for slot in valid_slots if str(slot).strip()})
    usage = (
        "⚠️ Формат команды:\n"
        "`!model set <slot> <model_id>`\n"
        "Пример: `!model set chat zai-org/glm-4.6v-flash`"
    )

    if len(args) < 3:
        return {"ok": False, "error": usage}

    candidate_slot = str(args[1]).strip().lower()
    if candidate_slot in slots_sorted:
        slot = candidate_slot
        model_raw = " ".join(args[2:]).strip()
    else:
        slot = "chat"
        model_raw = " ".join(args[1:]).strip()

    if not model_raw:
        return {"ok": False, "error": usage}

    model_id, alias_msg = normalize_model_alias(model_raw)
    return {
        "ok": True,
        "slot": slot,
        "model_id": model_id,
        "alias_msg": alias_msg,
        "is_legacy": candidate_slot not in slots_sorted,
    }


def render_model_presets_text() -> str:
    """Renders user-friendly model presets for quick switching."""
    return (
        "🧩 **Быстрые пресеты моделей:**\n\n"
        "**Cloud (Gemini):**\n"
        "• `!model set chat gemini-flash` → `google/gemini-2.5-flash`\n"
        "• `!model set pro gemini-3-pro-latest` → `google/gemini-3.1-pro-preview`\n"
        "• `!model set thinking gemini-2.5-pro` → `google/gemini-2.5-pro`\n\n"
        "**Cloud (OpenAI):**\n"
        "• `!model set chat gpt-4o-mini` → `openai/gpt-4o-mini`\n"
        "• `!model set chat gpt-5-mini` → `openai/gpt-5-mini`\n"
        "• `!model set coding gpt-5-codex` → `openai/gpt-5-codex`\n\n"
        "**Local (LM Studio):**\n"
        "• `!model set chat zai-org/glm-4.6v-flash`\n\n"
        "После смены — проверка:\n"
        "• `!model`\n"
        "• `!model preflight chat Тест маршрута`\n"
    )
