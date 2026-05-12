# -*- coding: utf-8 -*-
"""Tests for AgentEngineRouter (Wave 16-B, Hermes Phase B).

Покрывает: resolve_engine, get/set_chat_override, get/set_room_engine,
env fallback, invalid engine validation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.core.agent_engine_router as router_mod
from src.core.agent_engine_router import (
    get_chat_override,
    get_room_engine,
    resolve_engine,
    set_chat_override,
    set_room_engine,
)

# ---------------------------------------------------------------------------
# Fixtures — перенаправляем пути на tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Изолируем disk I/O — каждый тест работает с tmp_path."""
    overrides = tmp_path / "agent_engine_overrides.json"
    swarm_eng = tmp_path / "swarm_engine.json"
    monkeypatch.setattr(router_mod, "OVERRIDES_PATH", overrides)
    monkeypatch.setattr(router_mod, "SWARM_ENGINE_PATH", swarm_eng)


# ---------------------------------------------------------------------------
# resolve_engine
# ---------------------------------------------------------------------------


def test_resolve_engine_default_openclaw(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без переопределений дефолт — openclaw."""
    monkeypatch.delenv("KRAB_AGENT_ENGINE", raising=False)
    result = resolve_engine()
    assert result == "openclaw"


def test_resolve_engine_chat_override() -> None:
    """Per-chat override возвращается корректно."""
    set_chat_override(12345, "hermes")
    result = resolve_engine(chat_id=12345)
    assert result == "hermes"


def test_resolve_engine_room_policy() -> None:
    """Per-room policy учитывается при отсутствии chat override."""
    set_room_engine("traders", "hermes")
    result = resolve_engine(room="traders")
    assert result == "hermes"


def test_resolve_engine_priority_chat_over_room() -> None:
    """Chat override имеет приоритет над room policy."""
    set_chat_override(99, "openclaw")
    set_room_engine("coders", "hermes")
    # chat_id=99 (openclaw) > room coders (hermes) -> openclaw wins
    result = resolve_engine(chat_id=99, room="coders")
    assert result == "openclaw"


def test_resolve_engine_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_AGENT_ENGINE=hermes задаёт дефолт через env."""
    monkeypatch.setenv("KRAB_AGENT_ENGINE", "hermes")
    result = resolve_engine()
    assert result == "hermes"


def test_resolve_engine_env_default_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_AGENT_ENGINE=auto тоже валиден."""
    monkeypatch.setenv("KRAB_AGENT_ENGINE", "auto")
    result = resolve_engine()
    assert result == "auto"


# ---------------------------------------------------------------------------
# set/get chat override
# ---------------------------------------------------------------------------


def test_set_chat_override_persists(tmp_path: Path) -> None:
    """Override сохраняется на диск и читается обратно."""
    set_chat_override(-100500, "hermes")
    val = get_chat_override(-100500)
    assert val == "hermes"


def test_set_chat_override_clear() -> None:
    """engine=None снимает override."""
    set_chat_override(777, "hermes")
    assert get_chat_override(777) == "hermes"
    set_chat_override(777, None)
    assert get_chat_override(777) is None


def test_get_chat_override_missing() -> None:
    """Отсутствующий chat_id -> None."""
    assert get_chat_override(9999) is None


# ---------------------------------------------------------------------------
# set/get room engine
# ---------------------------------------------------------------------------


def test_set_room_engine_persists() -> None:
    """Room engine сохраняется на диск."""
    set_room_engine("analysts", "hermes")
    assert get_room_engine("analysts") == "hermes"


def test_set_room_engine_case_insensitive() -> None:
    """Room name нормализуется к нижнему регистру."""
    set_room_engine("TRADERS", "auto")
    assert get_room_engine("traders") == "auto"
    assert get_room_engine("TRADERS") == "auto"


def test_set_room_engine_clear() -> None:
    """engine=None снимает room policy."""
    set_room_engine("creative", "hermes")
    set_room_engine("creative", None)
    assert get_room_engine("creative") is None


# ---------------------------------------------------------------------------
# Fallback / validation
# ---------------------------------------------------------------------------


def test_resolve_engine_invalid_env_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Невалидный KRAB_AGENT_ENGINE -> openclaw."""
    monkeypatch.setenv("KRAB_AGENT_ENGINE", "lm_studio")
    result = resolve_engine()
    assert result == "openclaw"


def test_invalid_engine_raises_in_setter() -> None:
    """Невалидный engine вызывает ValueError."""
    with pytest.raises(ValueError, match="invalid engine"):
        set_chat_override(1, "gpt5")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="invalid engine"):
        set_room_engine("team", "gpt5")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Edge: пустой файл / корраптный JSON
# ---------------------------------------------------------------------------


def test_resolve_engine_corrupt_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Корраптный JSON не роняет resolve_engine."""
    overrides = tmp_path / "agent_engine_overrides.json"
    overrides.write_text("{not valid json!!!", encoding="utf-8")
    monkeypatch.setattr(router_mod, "OVERRIDES_PATH", overrides)
    monkeypatch.delenv("KRAB_AGENT_ENGINE", raising=False)
    # Не падает, возвращает openclaw
    assert resolve_engine(chat_id=1) == "openclaw"
