# -*- coding: utf-8 -*-
"""Тесты Named Entity Memory (Идея 13)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.named_entity_memory import Entity, EntityStore


@pytest.fixture
def store(tmp_path: Path) -> EntityStore:
    """Свежий store с tmp-файлом и контролируемыми часами."""
    clock = [datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)]
    s = EntityStore(
        storage_path=tmp_path / "named_entities.json",
        now_fn=lambda: clock[0],
    )
    s._test_clock = clock  # type: ignore[attr-defined]
    return s


def _advance(store: EntityStore, seconds: int) -> None:
    clock = store._test_clock  # type: ignore[attr-defined]
    clock[0] = clock[0] + timedelta(seconds=seconds)


def test_record_creates_entity_with_defaults(store: EntityStore) -> None:
    entity = store.record_mention("Anna", "person", attributes={"city": "Madrid"})

    assert entity.name == "Anna"
    assert entity.type == "person"
    assert entity.mentions_count == 1
    assert entity.attributes == {"city": "Madrid"}
    assert "anna" in entity.aliases


def test_lookup_exact_alias_and_substring(store: EntityStore) -> None:
    store.record_mention("Krab", "project", aliases=["краб", "kraab"])

    # Точное совпадение — case-insensitive
    assert store.lookup("krab") is not None
    assert store.lookup("KRAB") is not None
    assert store.lookup("краб") is not None
    # Substring (alias в query)
    found = store.lookup("Krab Voice Gateway")
    assert found is not None
    assert found.name == "Krab"
    # Несуществующее имя
    assert store.lookup("zzz_unknown") is None


def test_aliases_for_returns_sorted_list(store: EntityStore) -> None:
    store.record_mention("Krab", "project", aliases=["kraab", "краб"])

    aliases = store.aliases_for("Krab")
    # Включает canonical lowercase + явные алиасы, отсортирован
    assert "krab" in aliases
    assert "kraab" in aliases
    assert "краб" in aliases
    assert aliases == sorted(aliases)
    # Лукап несуществующего → пустой список
    assert store.aliases_for("ghost") == []


def test_top_entities_orders_by_mentions(store: EntityStore) -> None:
    store.record_mention("Anna", "person")
    store.record_mention("Pavel", "person")
    store.record_mention("Pavel", "person")
    store.record_mention("Pavel", "person")
    store.record_mention("Madrid", "place")
    store.record_mention("Madrid", "place")

    top = store.top_entities(limit=10)
    names = [e.name for e in top]
    assert names[0] == "Pavel"  # 3 mentions
    assert names[1] == "Madrid"  # 2 mentions
    assert names[2] == "Anna"  # 1 mention

    # limit=0 → []
    assert store.top_entities(limit=0) == []


def test_persistence_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "entities.json"
    s1 = EntityStore(storage_path=path)
    s1.record_mention("Mac M4 Max", "thing", attributes={"ram_gb": 36})
    s1.record_mention("Mac M4 Max", "thing", attributes={"owner": "Pavel"})

    # Файл записан и валидный JSON
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "mac m4 max" in raw

    # Новый стор подгружает с диска
    s2 = EntityStore(storage_path=path)
    found = s2.lookup("mac m4 max")
    assert found is not None
    assert found.mentions_count == 2
    # attributes мерджились через два вызова
    assert found.attributes == {"ram_gb": 36, "owner": "Pavel"}


def test_dedup_idempotent_record(store: EntityStore) -> None:
    e1 = store.record_mention("Voice Gateway", "project")
    _advance(store, 60)
    e2 = store.record_mention("Voice Gateway", "project", attributes={"port": 8090})
    _advance(store, 30)
    e3 = store.record_mention("voice gateway", "project")  # case variation

    # Один уникальный entity
    assert len(store.all_entities()) == 1
    assert e1.mentions_count == 1
    assert e2.mentions_count == 2
    assert e3.mentions_count == 3
    # last_seen_at двигается, first_seen_at — нет
    assert e3.first_seen_at == e1.first_seen_at
    assert e3.last_seen_at != e1.last_seen_at
    # attributes сохранились
    assert e3.attributes == {"port": 8090}


def test_forget_removes_entity_and_aliases(store: EntityStore) -> None:
    store.record_mention("Anna", "person", aliases=["анна"])
    assert store.lookup("анна") is not None

    assert store.forget("Anna") is True
    assert store.lookup("Anna") is None
    assert store.lookup("анна") is None
    # повторный forget → False
    assert store.forget("Anna") is False


def test_returned_entity_is_copy_not_reference(store: EntityStore) -> None:
    store.record_mention("Krab", "project", attributes={"a": 1})
    fetched = store.lookup("Krab")
    assert fetched is not None
    fetched.attributes["a"] = 999
    fetched.aliases.add("hacked")

    # Внутреннее состояние не изменилось
    fresh = store.lookup("Krab")
    assert fresh is not None
    assert fresh.attributes == {"a": 1}
    assert "hacked" not in fresh.aliases


def test_entity_from_dict_handles_corrupt_aliases() -> None:
    # aliases не list → должно дать пустой set, но не падать
    e = Entity.from_dict({"name": "X", "aliases": "not-a-list"})
    assert e.aliases == set()
