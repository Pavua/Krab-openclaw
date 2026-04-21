"""Skill scope registry (Chado §4 P2).

Tracks which skills are active for which context (global / per-chat / per-swarm-team).
Sits OVER command_registry (which has stage field from W2.2).

Public:
- register_scope(skill_name, scope="global", *, chat_ids=None, team=None)
- is_allowed(skill_name, *, chat_id=None, team=None) -> bool
- list_for_scope(chat_id=None, team=None) -> list[str]
- reset()  # for tests

Persist: ~/.openclaw/krab_runtime_state/skill_scopes.json

Example:
    register_scope("experimental_feature", scope="chat", chat_ids=[OWNER_CHAT_ID])
    is_allowed("experimental_feature", chat_id=123) -> False
    is_allowed("experimental_feature", chat_id=OWNER_CHAT_ID) -> True
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import structlog

_log = structlog.get_logger(__name__)

Scope = Literal["global", "chat", "team", "disabled"]

_PERSIST_PATH = Path("~/.openclaw/krab_runtime_state/skill_scopes.json").expanduser()


@dataclass
class ScopeEntry:
    skill_name: str
    scope: Scope = "global"
    chat_ids: frozenset[int] = field(default_factory=frozenset)
    teams: frozenset[str] = field(default_factory=frozenset)

    def to_dict(self) -> dict:
        return {
            "skill_name": self.skill_name,
            "scope": self.scope,
            "chat_ids": sorted(self.chat_ids),
            "teams": sorted(self.teams),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScopeEntry":
        return cls(
            skill_name=d["skill_name"],
            scope=d.get("scope", "global"),
            chat_ids=frozenset(int(x) for x in d.get("chat_ids", [])),
            teams=frozenset(d.get("teams", [])),
        )


# ---------------------------------------------------------------------------
# Registry state
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_registry: dict[str, ScopeEntry] = {}
_loaded = False


def _ensure_loaded() -> None:
    """Lazy-load from disk once."""
    global _loaded
    if _loaded:
        return
    _load()
    _loaded = True


def _load() -> None:
    """Load persisted scopes from disk (called under lock)."""
    global _registry
    if not _PERSIST_PATH.exists():
        return
    try:
        data = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
        _registry = {
            entry["skill_name"]: ScopeEntry.from_dict(entry)
            for entry in data
            if "skill_name" in entry
        }
        _log.debug("skill_scope.loaded", count=len(_registry))
    except Exception as exc:  # noqa: BLE001
        _log.warning("skill_scope.load_error", error=str(exc))


def _persist() -> None:
    """Persist current registry to disk (called under lock)."""
    try:
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = [entry.to_dict() for entry in _registry.values()]
        _PERSIST_PATH.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("skill_scope.persist_error", error=str(exc))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def register_scope(
    skill_name: str,
    scope: Scope = "global",
    *,
    chat_ids: list[int] | None = None,
    team: str | None = None,
) -> ScopeEntry:
    """Register or update the scope for a skill.

    Args:
        skill_name: Unique skill identifier (e.g. "experimental_feature").
        scope: One of "global", "chat", "team", "disabled".
        chat_ids: Required when scope="chat". List of allowed chat IDs.
        team: Required when scope="team". Swarm team name.

    Returns:
        The created/updated ScopeEntry.
    """
    entry = ScopeEntry(
        skill_name=skill_name,
        scope=scope,
        chat_ids=frozenset(chat_ids or []),
        teams=frozenset([team] if team else []),
    )
    with _lock:
        _ensure_loaded()
        _registry[skill_name] = entry
        _persist()
    _log.info("skill_scope.registered", skill=skill_name, scope=scope)
    return entry


def is_allowed(
    skill_name: str,
    *,
    chat_id: int | None = None,
    team: str | None = None,
) -> bool:
    """Check if a skill is allowed in the given context.

    Unknown skills (not registered) are allowed by default — no gate unless registered.

    Args:
        skill_name: Skill to check.
        chat_id: Current chat context (None = outside any chat).
        team: Current swarm team context (None = not in swarm).

    Returns:
        True if allowed, False otherwise.
    """
    with _lock:
        _ensure_loaded()
        entry = _registry.get(skill_name)

    if entry is None:
        return True  # не зарегистрировано → пропускаем

    if entry.scope == "disabled":
        return False

    if entry.scope == "global":
        return True

    if entry.scope == "chat":
        return chat_id is not None and chat_id in entry.chat_ids

    if entry.scope == "team":
        return team is not None and team in entry.teams

    return False


def list_for_scope(
    chat_id: int | None = None,
    team: str | None = None,
) -> list[str]:
    """Return skill names allowed in the given context.

    Only registered skills are returned; unregistered skills are always allowed
    but not enumerated here.

    Args:
        chat_id: Filter by chat context.
        team: Filter by swarm team context.

    Returns:
        Sorted list of allowed registered skill names.
    """
    with _lock:
        _ensure_loaded()
        snapshot = dict(_registry)

    result = [
        name
        for name, entry in snapshot.items()
        if _entry_allowed(entry, chat_id=chat_id, team=team)
    ]
    return sorted(result)


def _entry_allowed(
    entry: ScopeEntry,
    *,
    chat_id: int | None,
    team: str | None,
) -> bool:
    if entry.scope == "disabled":
        return False
    if entry.scope == "global":
        return True
    if entry.scope == "chat":
        return chat_id is not None and chat_id in entry.chat_ids
    if entry.scope == "team":
        return team is not None and team in entry.teams
    return False


def reset() -> None:
    """Clear the in-memory registry and mark as unloaded. For tests."""
    global _registry, _loaded
    with _lock:
        _registry = {}
        _loaded = True  # evite reload from disk during test
