"""Тесты Feature K — Thread Coherence Detector."""

from __future__ import annotations

import numpy as np
import pytest

from src.core.thread_coherence import (
    DEFAULT_THRESHOLD,
    ThreadCoherenceDetector,
    ThreadCoherenceResult,
    ThreadMessage,
)

# ---------- Fake embedder: tag-based вектора ----------
#
# Идея: каждое сообщение имеет "тему" в виде префикса, например "[A] hello".
# Векторы конструируем так, чтобы сообщения одной темы были коллинеарны,
# разных тем — ортогональны. Это даёт детерминированный cosine.

_AXES: dict[str, np.ndarray] = {}


def _axis(tag: str) -> np.ndarray:
    if tag not in _AXES:
        idx = len(_AXES)
        v = np.zeros(8, dtype=np.float32)
        v[idx % 8] = 1.0
        _AXES[tag] = v
    return _AXES[tag]


def fake_embedder(text: str) -> np.ndarray:
    """Достаём тему из префикса вида '[X] ...'. Без префикса — нулевая ось."""
    text = text or ""
    if text.startswith("[") and "]" in text:
        tag = text[1 : text.index("]")]
    else:
        tag = "_default"
    return _axis(tag).copy()


@pytest.fixture
def detector() -> ThreadCoherenceDetector:
    return ThreadCoherenceDetector(
        min_messages=5,
        threshold=0.4,
        embedder=fake_embedder,
        enabled=True,
        cache_size=64,
    )


def _msg(text: str, author: int = 1) -> ThreadMessage:
    return ThreadMessage(text=text, author_id=author)


def test_high_coherence_stays_high(detector: ThreadCoherenceDetector) -> None:
    """Все сообщения одной темы → score == 1.0."""
    thread = [_msg(f"[A] msg{i}", author=i % 2 + 1) for i in range(5)]
    current = _msg("[A] continuing the thread", author=2)
    result = detector.score_thread_coherence(thread, current)
    assert not result.skipped
    assert result.score == pytest.approx(1.0, abs=1e-6)
    assert result.anchor_similarity == pytest.approx(1.0, abs=1e-6)
    assert result.window_similarity == pytest.approx(1.0, abs=1e-6)
    assert detector.should_break_context(result) is False


def test_low_coherence_detected(detector: ThreadCoherenceDetector) -> None:
    """Drift: первые сообщения тема A, current — тема B (ортогонально)."""
    thread = [_msg(f"[A] foo{i}", author=i % 2 + 1) for i in range(5)]
    current = _msg("[B] совсем про другое", author=2)
    result = detector.score_thread_coherence(thread, current)
    assert not result.skipped
    assert result.score == pytest.approx(0.0, abs=1e-6)
    assert detector.should_break_context(result) is True


def test_short_thread_skipped(detector: ThreadCoherenceDetector) -> None:
    """Тред < min_messages → skipped, не флагается."""
    thread = [_msg("[A] a", author=1), _msg("[A] b", author=2)]
    current = _msg("[Z] полностью другое", author=1)
    result = detector.score_thread_coherence(thread, current)
    assert result.skipped is True
    assert result.skip_reason == "thread_too_short"
    assert detector.should_break_context(result) is False


def test_monologue_skipped(detector: ThreadCoherenceDetector) -> None:
    """Все сообщения от одного автора — skip независимо от длины."""
    thread = [_msg(f"[A] m{i}", author=42) for i in range(6)]
    current = _msg("[Z] off-topic", author=42)
    result = detector.score_thread_coherence(thread, current)
    assert result.skipped is True
    assert result.skip_reason == "monologue"
    assert detector.should_break_context(result) is False


def test_embedding_cache_hit(detector: ThreadCoherenceDetector) -> None:
    """Повторное сообщение должно идти из cache, увеличивать cache_hits."""
    thread = [_msg("[A] same text", author=i % 2 + 1) for i in range(5)]
    current = _msg("[A] same text", author=2)  # тот же текст что и в треде
    detector.reset_cache()
    detector.score_thread_coherence(thread, current)
    # Уникальных текстов 1, всего embed-вызовов 6 (anchor + window×3 + current,
    # но window пересекается с anchor если len<=window_size, плюс current дубль).
    assert detector.cache_misses == 1
    assert detector.cache_hits >= 1


def test_threshold_respected(detector: ThreadCoherenceDetector) -> None:
    """Custom threshold переопределяет дефолт."""
    thread = [_msg(f"[A] x{i}", author=i % 2 + 1) for i in range(5)]
    current = _msg("[B] drift", author=2)
    result = detector.score_thread_coherence(thread, current)
    # score == 0.0, должно быть ниже любого положительного порога.
    assert detector.should_break_context(result, threshold=0.1) is True
    # Но если threshold=0.0 — не break.
    assert detector.should_break_context(result, threshold=0.0) is False
    # Default threshold тоже срабатывает.
    assert detector.should_break_context(result, threshold=DEFAULT_THRESHOLD) is True


def test_explicit_switch_suppresses_break(detector: ThreadCoherenceDetector) -> None:
    """Маркер 'кстати' помечает explicit_switch и подавляет break."""
    thread = [_msg(f"[A] x{i}", author=i % 2 + 1) for i in range(5)]
    current = _msg("[B] кстати, совсем другая тема", author=2)
    result = detector.score_thread_coherence(thread, current)
    assert result.explicit_switch is True
    assert result.score == pytest.approx(0.0, abs=1e-6)
    # should_break_context подавляется при explicit switch.
    assert detector.should_break_context(result) is False


def test_format_break_notice() -> None:
    detector = ThreadCoherenceDetector(embedder=fake_embedder, enabled=True)
    notice = detector.format_break_notice("Обсуждали миграцию на OrbStack", "а сколько весит луна?")
    assert "OrbStack" in notice
    assert isinstance(notice, str)


def test_disabled_returns_skipped(detector: ThreadCoherenceDetector) -> None:
    detector.enabled = False
    thread = [_msg(f"[A] x{i}", author=i % 2 + 1) for i in range(5)]
    current = _msg("[B] drift", author=2)
    result = detector.score_thread_coherence(thread, current)
    assert result.skipped is True
    assert result.skip_reason == "detection_disabled"
    assert isinstance(result, ThreadCoherenceResult)
