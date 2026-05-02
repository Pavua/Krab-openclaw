# -*- coding: utf-8 -*-
"""AgentEngineRouter — резолвит какой движок (OpenClaw/Hermes) использовать.

Wave 16-B Phase B: feature-flagged, default = openclaw для всех.
Real wiring в llm_flow.py — Phase C.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .agent_engine import EngineKind
from .logger import get_logger

logger = get_logger(__name__)

# Файл per-chat overrides: {chats: {chat_id: engine}}
OVERRIDES_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "agent_engine_overrides.json"
# Файл per-swarm-room policy: {room: engine}
SWARM_ENGINE_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "swarm_engine.json"

# Допустимые значения engine
VALID: frozenset[str] = frozenset({"openclaw", "hermes", "auto"})


def _load_json(path: Path) -> dict:
    """Читает JSON без raise. Возвращает {} при ошибке."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_json_atomic(path: Path, data: dict) -> None:
    """Атомарная запись JSON через tmp-файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Per-chat overrides
# ---------------------------------------------------------------------------


def get_chat_override(chat_id: int | str) -> EngineKind | None:
    """Возвращает per-chat override или None если не задан."""
    overrides = _load_json(OVERRIDES_PATH).get("chats", {})
    val = overrides.get(str(chat_id))
    if val in VALID:
        return val  # type: ignore[return-value]
    return None


def set_chat_override(chat_id: int | str, engine: EngineKind | None) -> None:
    """Устанавливает или снимает per-chat override.

    engine=None снимает override (reset to default).
    """
    data = _load_json(OVERRIDES_PATH)
    chats = data.setdefault("chats", {})
    if engine is None:
        chats.pop(str(chat_id), None)
    else:
        if engine not in VALID:
            raise ValueError(f"invalid engine: {engine!r}. Valid: {sorted(VALID)}")
        chats[str(chat_id)] = engine
    _save_json_atomic(OVERRIDES_PATH, data)


# ---------------------------------------------------------------------------
# Per-swarm-room policy
# ---------------------------------------------------------------------------


def get_room_engine(room: str) -> EngineKind | None:
    """Возвращает engine для swarm room или None."""
    rooms = _load_json(SWARM_ENGINE_PATH)
    val = rooms.get(room.lower())
    if val in VALID:
        return val  # type: ignore[return-value]
    return None


def set_room_engine(room: str, engine: EngineKind | None) -> None:
    """Устанавливает или снимает engine для swarm room.

    engine=None снимает политику (reset to default).
    """
    data = _load_json(SWARM_ENGINE_PATH)
    if engine is None:
        data.pop(room.lower(), None)
    else:
        if engine not in VALID:
            raise ValueError(f"invalid engine: {engine!r}. Valid: {sorted(VALID)}")
        data[room.lower()] = engine
    _save_json_atomic(SWARM_ENGINE_PATH, data)


# ---------------------------------------------------------------------------
# Резолвер
# ---------------------------------------------------------------------------


def resolve_engine(
    *,
    chat_id: int | str | None = None,
    room: str | None = None,
) -> EngineKind:
    """Резолвит engine по приоритету:
    1. Per-chat override (OVERRIDES_PATH)
    2. Per-swarm-room policy (SWARM_ENGINE_PATH)
    3. Env KRAB_AGENT_ENGINE (default: "openclaw")

    НЕ делает health probe (caller responsibility).
    Default openclaw если ничего не задано или env невалиден.
    """
    # 1. Per-chat override
    if chat_id is not None:
        ovr = get_chat_override(chat_id)
        if ovr is not None:
            logger.debug("engine_resolved_chat_override", chat_id=chat_id, engine=ovr)
            return ovr

    # 2. Per-room policy
    if room:
        room_engine = get_room_engine(room)
        if room_engine is not None:
            logger.debug("engine_resolved_room_policy", room=room, engine=room_engine)
            return room_engine

    # 3. Env-переменная
    env_val = os.environ.get("KRAB_AGENT_ENGINE", "openclaw").lower().strip()
    if env_val in VALID:
        return env_val  # type: ignore[return-value]

    # Fallback — openclaw всегда
    logger.debug("engine_resolved_default", env_val=env_val)
    return "openclaw"
