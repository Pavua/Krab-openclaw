# -*- coding: utf-8 -*-
"""Тесты VoiceFingerprintRegistry (Idea 36)."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.core.voice_fingerprint import (
    DEFAULT_MATCH_THRESHOLD,
    VoiceFingerprintRegistry,
    _cosine_similarity,
)


@pytest.fixture
def registry(tmp_path: Path) -> VoiceFingerprintRegistry:
    storage = tmp_path / "voice_fingerprints.json"
    fixed_now = datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc)
    return VoiceFingerprintRegistry(
        storage_path=storage,
        now_fn=lambda: fixed_now,
    )


def test_register_speaker_with_and_without_embedding(registry: VoiceFingerprintRegistry) -> None:
    """Регистрация работает и с embedding, и без."""
    snap1 = registry.register_speaker(123, "Pablito", voice_embedding=[1.0, 0.0, 0.0])
    snap2 = registry.register_speaker("456", "Anonymous")

    assert snap1["user_id"] == "123"
    assert snap1["name"] == "Pablito"
    assert snap1["embedding"] == [1.0, 0.0, 0.0]
    assert snap1["embedding_dim"] == 3
    assert snap2["user_id"] == "456"
    assert snap2["embedding"] is None
    assert snap2["embedding_dim"] is None

    speakers = registry.list_known_speakers()
    assert len(speakers) == 2
    by_id = {s["user_id"]: s for s in speakers}
    assert by_id["123"]["has_embedding"] is True
    assert by_id["456"]["has_embedding"] is False
    # сырой embedding в list-выдаче не светим
    assert "embedding" not in by_id["123"]


def test_lookup_via_list_and_forget(registry: VoiceFingerprintRegistry) -> None:
    """list_known_speakers возвращает копии; forget_speaker удаляет."""
    registry.register_speaker(1, "Alice", voice_embedding=[0.5, 0.5, 0.0])
    registry.register_speaker(2, "Bob")

    speakers = registry.list_known_speakers()
    assert {s["user_id"] for s in speakers} == {"1", "2"}

    # caller mutation не должна ломать internal state
    speakers[0]["name"] = "MUTATED"
    assert registry.list_known_speakers()[0]["name"] != "MUTATED" or \
        registry.list_known_speakers()[1]["name"] != "MUTATED"

    assert registry.forget_speaker(1) is True
    assert registry.forget_speaker(1) is False  # уже нет
    assert registry.forget_speaker(999) is False  # не было

    remaining = registry.list_known_speakers()
    assert len(remaining) == 1
    assert remaining[0]["user_id"] == "2"


def test_cosine_match_picks_closest_speaker(registry: VoiceFingerprintRegistry) -> None:
    """identify() выбирает спикера с максимальной cosine similarity."""
    # три ортогональных embedding'а — простая модель "разных голосов"
    registry.register_speaker(1, "Alice", voice_embedding=[1.0, 0.0, 0.0])
    registry.register_speaker(2, "Bob", voice_embedding=[0.0, 1.0, 0.0])
    registry.register_speaker(3, "Carol", voice_embedding=[0.0, 0.0, 1.0])
    # известный без отпечатка — должен игнорироваться при identify
    registry.register_speaker(4, "NoFingerprint")

    # probe близок к Alice (cos ≈ 0.98)
    user_id, conf = registry.identify([0.95, 0.1, 0.05])
    assert user_id == "1"
    assert conf > 0.9

    # probe близок к Bob
    user_id, conf = registry.identify([0.0, 1.0, 0.0])
    assert user_id == "2"
    assert math.isclose(conf, 1.0, rel_tol=1e-6)

    # probe ровно посередине Alice/Bob — cos ≈ 0.707, ниже default threshold 0.75
    user_id, conf = registry.identify([1.0, 1.0, 0.0])
    assert user_id is None
    assert math.isclose(conf, 1 / math.sqrt(2), rel_tol=1e-6)


def test_threshold_gates_low_confidence_match(registry: VoiceFingerprintRegistry) -> None:
    """Низкий cosine → (None, score); явный low threshold пропускает матч."""
    registry.register_speaker(1, "Alice", voice_embedding=[1.0, 0.0])

    # cos ≈ 0.6 — ниже default 0.75
    probe = [0.6, 0.8]
    user_id, conf = registry.identify(probe)
    assert user_id is None
    assert math.isclose(conf, 0.6, rel_tol=1e-6)

    # с явным более мягким threshold — пропускает
    user_id, conf = registry.identify(probe, threshold=0.5)
    assert user_id == "1"

    # Пустой embedding — None, 0.0 без exception
    assert registry.identify([]) == (None, 0.0)

    # Mismatched dim — cosine 0.0, не падает
    user_id, conf = registry.identify([1.0, 0.0, 0.0, 0.0])
    assert user_id is None
    assert conf == 0.0


def test_persistence_round_trip(tmp_path: Path) -> None:
    """register → новый registry на том же файле возвращает запись."""
    storage = tmp_path / "voice_fingerprints.json"
    fixed_now = datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc)

    reg1 = VoiceFingerprintRegistry(storage_path=storage, now_fn=lambda: fixed_now)
    reg1.register_speaker(42, "Pablito", voice_embedding=[0.1, 0.2, 0.3, 0.4])
    reg1.register_speaker(43, "Bob")

    # файл реально записан и валидный JSON
    raw = json.loads(storage.read_text(encoding="utf-8"))
    assert "42" in raw
    assert raw["42"]["name"] == "Pablito"
    assert raw["42"]["embedding"] == [0.1, 0.2, 0.3, 0.4]
    assert raw["43"]["embedding"] is None

    # новый инстанс — данные подтянулись с диска
    reg2 = VoiceFingerprintRegistry(storage_path=storage, now_fn=lambda: fixed_now)
    speakers = reg2.list_known_speakers()
    assert {s["user_id"] for s in speakers} == {"42", "43"}

    # identify через перезагруженный реестр — сравнение работает
    user_id, conf = reg2.identify([0.1, 0.2, 0.3, 0.4], threshold=0.99)
    assert user_id == "42"
    assert math.isclose(conf, 1.0, rel_tol=1e-6)

    # forget также persist'ит
    reg2.forget_speaker(43)
    raw = json.loads(storage.read_text(encoding="utf-8"))
    assert "43" not in raw
    assert "42" in raw


def test_register_validation_and_update(registry: VoiceFingerprintRegistry) -> None:
    """Пустой user_id/name → ValueError; повторный register обновляет имя без потери embedding."""
    with pytest.raises(ValueError):
        registry.register_speaker("", "Name")
    with pytest.raises(ValueError):
        registry.register_speaker(1, "   ")
    with pytest.raises(ValueError):
        registry.register_speaker(1, "Name", voice_embedding=[])

    registry.register_speaker(7, "Old", voice_embedding=[1.0, 0.0])
    # обновляем имя без передачи embedding — отпечаток сохраняется
    registry.register_speaker(7, "New")
    speakers = registry.list_known_speakers()
    assert speakers[0]["name"] == "New"
    assert speakers[0]["has_embedding"] is True

    # default threshold должен быть разумным значением
    assert 0.5 < DEFAULT_MATCH_THRESHOLD < 1.0


def test_cosine_helper_edge_cases() -> None:
    """Прямой sanity-check вспомогательной cosine-функции."""
    assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    # размерности не совпадают
    assert _cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0
    # нулевой вектор
    assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
    # пустые
    assert _cosine_similarity([], []) == 0.0
