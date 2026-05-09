# -*- coding: utf-8 -*-
"""
Tests для proactive-полей ChatResponsePolicy (Wave 39-B-2).

Покрывает:
- ENV-defaults для новых полей
- Backward-compat: load старого JSON без proactive-полей → defaults
- save/load round-trip: все 3 поля сохраняются и читаются
- Независимость per-chat: разные чаты не влияют друг на друга
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.core.chat_response_policy import (
    _PROACTIVE_AI_DEFAULT,
    _PROACTIVE_JOINS_DEFAULT,
    _PROACTIVE_MEDIA_DEFAULT,
    ChatResponsePolicy,
    ChatResponsePolicyStore,
)

# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "chat_response_policies.json"


@pytest.fixture
def store(store_path: Path) -> ChatResponsePolicyStore:
    return ChatResponsePolicyStore(path=store_path)


# ── ENV defaults ─────────────────────────────────────────────


def test_proactive_joins_default_env_true():
    """KRAB_PROACTIVE_JOINS_DEFAULT=1 → True по умолчанию (проверяем модульный default)."""
    # Модуль уже загружен; проверяем значение, определённое при импорте
    # (в CI env может быть разным, но import-time value должно совпадать с _PROACTIVE_JOINS_DEFAULT)
    p = ChatResponsePolicy(chat_id="1")
    assert p.proactive_joins is _PROACTIVE_JOINS_DEFAULT


def test_proactive_media_default_env_false():
    """KRAB_PROACTIVE_MEDIA_DEFAULT=0 → False по умолчанию."""
    p = ChatResponsePolicy(chat_id="1")
    assert p.proactive_media is _PROACTIVE_MEDIA_DEFAULT


def test_proactive_ai_default_env_false():
    """KRAB_PROACTIVE_AI_DEFAULT=0 → False по умолчанию."""
    p = ChatResponsePolicy(chat_id="1")
    assert p.proactive_ai is _PROACTIVE_AI_DEFAULT


def test_default_proactive_joins_is_true_when_no_env(monkeypatch):
    """Без env KRAB_PROACTIVE_JOINS_DEFAULT → True (по spec default=1)."""
    monkeypatch.delenv("KRAB_PROACTIVE_JOINS_DEFAULT", raising=False)
    from importlib import reload

    import src.core.chat_response_policy as m

    reload(m)
    assert m._PROACTIVE_JOINS_DEFAULT is True
    # restore после reload
    reload(m)


def test_default_proactive_media_is_false_when_no_env(monkeypatch):
    """Без env KRAB_PROACTIVE_MEDIA_DEFAULT → False (по spec default=0)."""
    monkeypatch.delenv("KRAB_PROACTIVE_MEDIA_DEFAULT", raising=False)
    from importlib import reload

    import src.core.chat_response_policy as m

    reload(m)
    assert m._PROACTIVE_MEDIA_DEFAULT is False
    reload(m)


def test_default_proactive_ai_is_false_when_no_env(monkeypatch):
    """Без env KRAB_PROACTIVE_AI_DEFAULT → False."""
    monkeypatch.delenv("KRAB_PROACTIVE_AI_DEFAULT", raising=False)
    from importlib import reload

    import src.core.chat_response_policy as m

    reload(m)
    assert m._PROACTIVE_AI_DEFAULT is False
    reload(m)


# ── Backward-compat: старый JSON без proactive-полей ─────────


def test_load_old_json_without_proactive_fields(tmp_path: Path):
    """Старый JSON-файл без proactive_* → при загрузке применяются ENV-дефолты."""
    old_data = {
        "42": {
            "chat_id": "42",
            "mode": "normal",
            "threshold_override": None,
            "negative_signals": 3,
            "positive_signals": 1,
            "last_negative_ts": None,
            "last_positive_ts": None,
            "last_auto_adjust_ts": None,
            "auto_adjust_enabled": True,
            "blocked_topics": [],
            "notes": "",
            "created_at": 1000.0,
            "updated_at": 1000.0,
            # proactive_* намеренно отсутствуют
        }
    }
    p = tmp_path / "old_policies.json"
    p.write_text(json.dumps(old_data))

    s = ChatResponsePolicyStore(path=p)
    policy = s.get_policy("42")

    # Поля загружены с ENV-дефолтами
    assert policy.proactive_joins is _PROACTIVE_JOINS_DEFAULT
    assert policy.proactive_media is _PROACTIVE_MEDIA_DEFAULT
    assert policy.proactive_ai is _PROACTIVE_AI_DEFAULT


def test_load_old_json_preserves_other_fields(tmp_path: Path):
    """Backward-compat: остальные поля из старого JSON сохраняются."""
    old_data = {
        "99": {
            "chat_id": "99",
            "mode": "cautious",
            "negative_signals": 7,
            "positive_signals": 2,
            "auto_adjust_enabled": False,
            "blocked_topics": ["spam"],
            "notes": "manual policy",
            "created_at": 500.0,
            "updated_at": 600.0,
        }
    }
    p = tmp_path / "old_policies2.json"
    p.write_text(json.dumps(old_data))

    s = ChatResponsePolicyStore(path=p)
    policy = s.get_policy("99")

    assert policy.mode.value == "cautious"
    assert policy.negative_signals == 7
    assert policy.auto_adjust_enabled is False
    assert "spam" in policy.blocked_topics
    assert policy.notes == "manual policy"


# ── Save/load round-trip ─────────────────────────────────────


def test_roundtrip_all_proactive_fields_true(store: ChatResponsePolicyStore, store_path: Path):
    """Все proactive-поля True сохраняются и читаются корректно."""
    store.update_policy("10", proactive_joins=True, proactive_media=True, proactive_ai=True)

    # Читаем из файла свежим store
    s2 = ChatResponsePolicyStore(path=store_path)
    p = s2.get_policy("10")
    assert p.proactive_joins is True
    assert p.proactive_media is True
    assert p.proactive_ai is True


def test_roundtrip_all_proactive_fields_false(store: ChatResponsePolicyStore, store_path: Path):
    """Все proactive-поля False сохраняются и читаются корректно."""
    store.update_policy("11", proactive_joins=False, proactive_media=False, proactive_ai=False)

    s2 = ChatResponsePolicyStore(path=store_path)
    p = s2.get_policy("11")
    assert p.proactive_joins is False
    assert p.proactive_media is False
    assert p.proactive_ai is False


def test_roundtrip_mixed_proactive_fields(store: ChatResponsePolicyStore, store_path: Path):
    """Смешанные значения proactive-полей сохраняются и читаются."""
    store.update_policy("12", proactive_joins=True, proactive_media=False, proactive_ai=True)

    s2 = ChatResponsePolicyStore(path=store_path)
    p = s2.get_policy("12")
    assert p.proactive_joins is True
    assert p.proactive_media is False
    assert p.proactive_ai is True


def test_roundtrip_json_contains_proactive_keys(store: ChatResponsePolicyStore, store_path: Path):
    """JSON-файл явно содержит все три proactive-ключа после save."""
    store.update_policy("20", proactive_joins=True, proactive_media=False, proactive_ai=False)

    raw = json.loads(store_path.read_text())
    assert "proactive_joins" in raw["20"]
    assert "proactive_media" in raw["20"]
    assert "proactive_ai" in raw["20"]
    assert raw["20"]["proactive_joins"] is True
    assert raw["20"]["proactive_media"] is False
    assert raw["20"]["proactive_ai"] is False


# ── Independent per-chat settings ────────────────────────────


def test_proactive_independent_per_chat(store: ChatResponsePolicyStore):
    """Разные чаты имеют независимые proactive-настройки."""
    store.update_policy("A", proactive_joins=True, proactive_media=True, proactive_ai=True)
    store.update_policy("B", proactive_joins=False, proactive_media=False, proactive_ai=False)

    pA = store.get_policy("A")
    pB = store.get_policy("B")

    assert pA.proactive_joins is True
    assert pB.proactive_joins is False
    assert pA.proactive_media is True
    assert pB.proactive_media is False
    assert pA.proactive_ai is True
    assert pB.proactive_ai is False


def test_proactive_update_one_chat_does_not_affect_another(store: ChatResponsePolicyStore):
    """Изменение proactive-флагов в одном чате не затрагивает другой."""
    store.update_policy("C", proactive_joins=True)
    store.update_policy("D", proactive_joins=True)

    # Меняем только чат C
    store.update_policy("C", proactive_joins=False)

    pC = store.get_policy("C")
    pD = store.get_policy("D")
    assert pC.proactive_joins is False
    assert pD.proactive_joins is True  # не тронут


def test_default_chat_no_proactive_fields_in_cache(store: ChatResponsePolicyStore):
    """get_policy без предыдущего update не создаёт запись в store."""
    p = store.get_policy("UNKNOWN")
    assert p.proactive_joins is _PROACTIVE_JOINS_DEFAULT
    assert p.proactive_media is _PROACTIVE_MEDIA_DEFAULT
    assert p.proactive_ai is _PROACTIVE_AI_DEFAULT
    # Не должно быть в _cache (lazy default)
    assert store.list_all() == []


# ── to_dict includes proactive fields ────────────────────────


def test_to_dict_includes_proactive_fields():
    """to_dict сериализует proactive-поля."""
    p = ChatResponsePolicy(
        chat_id="Z", proactive_joins=True, proactive_media=False, proactive_ai=True
    )
    d = p.to_dict()
    assert d["proactive_joins"] is True
    assert d["proactive_media"] is False
    assert d["proactive_ai"] is True


def test_from_dict_with_explicit_proactive_values():
    """from_dict с явными proactive-значениями применяет их, игнорируя ENV."""
    data = {
        "chat_id": "XY",
        "proactive_joins": False,
        "proactive_media": True,
        "proactive_ai": False,
    }
    p = ChatResponsePolicy.from_dict(data)
    assert p.proactive_joins is False
    assert p.proactive_media is True
    assert p.proactive_ai is False
