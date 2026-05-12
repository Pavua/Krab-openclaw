# -*- coding: utf-8 -*-
"""
Тесты для src/core/routing_policy.py (Wave 60-A).

Покрытие:
- policy matrix loaded (все task_types имеют valid backend)
- owner_dm → всегда cloud
- simple_lookup → local когда LM Studio up
- simple_lookup → cloud fallback когда LM Studio down
- force_cloud_env → всегда cloud (overrides matrix)
- has_photo → всегда cloud
- sensitive content → _SENSITIVE_DEFAULT_BACKEND
- runtime override (!routing local/cloud <task>)
- structured event log (structlog)
- routing decisions persisted to JSONL
- routing decisions readable
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.routing_policy import (
    _VALID_BACKENDS,
    ROUTING_POLICY,
    RouteDecision,
    RoutingPolicy,
    _append_decision,
    clear_task_override,
    get_overrides,
    get_routing_policy,
    read_recent_decisions,
    reset_lm_health_cache,
    set_task_override,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_policy(
    lm_up: bool = True,
    owner_ids: Optional[frozenset[int]] = None,
) -> RoutingPolicy:
    """Создаёт RoutingPolicy с замоканным LM Studio health probe."""
    reset_lm_health_cache()
    p = RoutingPolicy(
        lm_studio_url="http://fake-lmstudio:1234",
        owner_chat_ids=owner_ids or frozenset({12345}),
    )
    return p


async def _decide(
    policy: RoutingPolicy,
    task_type: str = "simple_lookup",
    message_text: str = "",
    chat_id: int = 0,
    has_photo: bool = False,
    force_cloud_env: bool = False,
    lm_up: bool = True,
) -> RouteDecision:
    with patch(
        "src.core.routing_policy._probe_lm_studio",
        new_callable=AsyncMock,
        return_value=lm_up,
    ):
        return await policy.decide_route(
            task_type=task_type,
            message_text=message_text,
            chat_id=chat_id,
            has_photo=has_photo,
            force_cloud_env=force_cloud_env,
        )


# ---------------------------------------------------------------------------
# Тест 1: policy matrix loaded
# ---------------------------------------------------------------------------


def test_policy_matrix_loaded() -> None:
    """Все task_types в матрице имеют допустимый backend."""
    assert len(ROUTING_POLICY) >= 10, "Матрица должна содержать хотя бы 10 записей"
    for task, backend in ROUTING_POLICY.items():
        assert backend in _VALID_BACKENDS, (
            f"task_type '{task}' имеет недопустимый backend '{backend}'. "
            f"Допустимые: {_VALID_BACKENDS}"
        )


def test_policy_matrix_contains_key_tasks() -> None:
    """Ключевые task_types присутствуют в матрице."""
    required = {"owner_dm", "vision_analysis", "default_chat", "code_generation"}
    for task in required:
        assert task in ROUTING_POLICY, f"Ключевой task '{task}' отсутствует в матрице"


# ---------------------------------------------------------------------------
# Тест 2: owner_dm → всегда cloud
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_dm_always_cloud() -> None:
    """owner DM → cloud независимо от LM Studio status."""
    policy = _make_policy(owner_ids=frozenset({99999}))
    # LM Studio доступен, но owner → cloud
    decision = await _decide(policy, task_type="default_chat", chat_id=99999, lm_up=True)
    assert decision.backend == "cloud"
    assert "owner" in decision.reason


@pytest.mark.asyncio
async def test_owner_dm_cloud_even_lm_down() -> None:
    """owner DM → cloud даже если LM Studio недоступен."""
    policy = _make_policy(owner_ids=frozenset({99999}))
    decision = await _decide(policy, task_type="default_chat", chat_id=99999, lm_up=False)
    assert decision.backend == "cloud"


# ---------------------------------------------------------------------------
# Тест 3: simple_lookup → local когда LM Studio доступен
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_lookup_routes_local_when_lm_available() -> None:
    """simple_lookup → local если LM Studio up и нет force_cloud."""
    policy = _make_policy()
    decision = await _decide(policy, task_type="simple_lookup", lm_up=True)
    assert decision.backend == "local"
    assert "policy_matrix" in decision.reason


# ---------------------------------------------------------------------------
# Тест 4: simple_lookup → cloud fallback когда LM Studio недоступен
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_lookup_falls_to_cloud_when_lm_down() -> None:
    """simple_lookup → cloud fallback если LM Studio недоступен."""
    policy = _make_policy()
    decision = await _decide(policy, task_type="simple_lookup", lm_up=False)
    assert decision.backend == "cloud"
    assert "lm_studio_unavailable" in decision.reason


# ---------------------------------------------------------------------------
# Тест 5: force_cloud_env → всегда cloud
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_cloud_env_overrides_policy() -> None:
    """FORCE_CLOUD env → cloud для любого task_type."""
    policy = _make_policy()
    for task in ["simple_lookup", "casual_chat_low_priority", "translation_short"]:
        decision = await _decide(policy, task_type=task, force_cloud_env=True, lm_up=True)
        assert decision.backend == "cloud", f"task={task} должен быть cloud при force_cloud_env"
        assert "FORCE_CLOUD" in decision.reason


# ---------------------------------------------------------------------------
# Тест 6: has_photo → всегда cloud
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_photo_always_cloud() -> None:
    """has_photo=True → cloud даже для simple_lookup с LM Studio up."""
    policy = _make_policy()
    decision = await _decide(policy, task_type="simple_lookup", has_photo=True, lm_up=True)
    assert decision.backend == "cloud"
    assert "vision" in decision.reason or "photo" in decision.reason


# ---------------------------------------------------------------------------
# Тест 7: structured event log (structlog)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decide_route_emits_structured_event() -> None:
    """decide_route логирует routing_decision через structlog."""
    policy = _make_policy()
    log_calls = []

    original_info = None

    def capture_info(event: str, **kwargs) -> None:
        log_calls.append({"event": event, **kwargs})

    with patch("src.core.routing_policy.logger") as mock_logger:
        mock_logger.info.side_effect = capture_info
        await _decide(policy, task_type="simple_lookup", lm_up=True)

    # Проверяем, что info был вызван хотя бы раз
    assert mock_logger.info.called
    # Проверяем, что routing_decision был залогирован
    calls = [c for c in mock_logger.info.call_args_list if c[0][0] == "routing_decision"]
    assert len(calls) >= 1, "routing_decision event не найден в structlog вызовах"


# ---------------------------------------------------------------------------
# Тест 8: runtime override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_routing_override_forces_backend() -> None:
    """set_task_override → решение использует override backend."""
    policy = _make_policy()
    try:
        # default_chat = "auto", ставим override на "cloud"
        set_task_override("default_chat", "cloud")
        decision = await _decide(policy, task_type="default_chat", lm_up=True)
        assert decision.backend == "cloud"
        assert "runtime_override" in decision.reason
    finally:
        clear_task_override("default_chat")


@pytest.mark.asyncio
async def test_routing_override_local_forces_local() -> None:
    """!routing local owner_dm → local даже для owner_dm (override имеет приоритет)."""
    # NOTE: override применяется ПОСЛЕ owner_dm проверки, поэтому owner_dm будет cloud.
    # Тестируем для task_type без owner detection.
    policy = _make_policy()
    try:
        set_task_override("code_generation", "local")
        # code_generation normally cloud, now overridden to local
        decision = await _decide(policy, task_type="code_generation", lm_up=True)
        assert decision.backend == "local"
        assert "runtime_override" in decision.reason
    finally:
        clear_task_override("code_generation")


def test_set_task_override_invalid_backend_raises() -> None:
    """set_task_override с invalid backend должен raised ValueError."""
    with pytest.raises(ValueError, match="Invalid backend"):
        set_task_override("simple_lookup", "invalid_backend")


# ---------------------------------------------------------------------------
# Тест 9: sensitive content gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sensitive_content_triggers_privacy_gate() -> None:
    """Сообщение с password/token/api_key → privacy gate backend."""
    from src.core.routing_policy import _SENSITIVE_DEFAULT_BACKEND

    policy = _make_policy()
    sensitive_texts = [
        "my password is abc123",
        "here is the API key: sk-12345",
        "token: Bearer eyJhbGci",
        "ssh-rsa AAAAB3Nza...",
    ]
    for text in sensitive_texts:
        decision = await _decide(policy, task_type="default_chat", message_text=text, lm_up=True)
        assert decision.backend == _SENSITIVE_DEFAULT_BACKEND, (
            f"sensitive text '{text[:30]}...' → expected {_SENSITIVE_DEFAULT_BACKEND}, "
            f"got {decision.backend}"
        )
        assert "sensitive" in decision.reason or "privacy" in decision.reason


# ---------------------------------------------------------------------------
# Тест 10: decisions log persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decisions_persisted_to_jsonl(tmp_path: Path) -> None:
    """Решения записываются в JSONL файл."""
    log_file = tmp_path / "routing_decisions.jsonl"
    policy = _make_policy()

    with patch("src.core.routing_policy._DECISIONS_LOG", log_file):
        await _decide(policy, task_type="simple_lookup", lm_up=True)
        await _decide(policy, task_type="code_generation", lm_up=True)

    assert log_file.exists(), "Файл решений не создан"
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert "task_type" in first
    assert "backend" in first
    assert "reason" in first
    assert "ts" in first


@pytest.mark.asyncio
async def test_decisions_log_capped_at_200(tmp_path: Path) -> None:
    """Лог обрезается до последних 200 записей."""
    log_file = tmp_path / "routing_decisions.jsonl"
    # Записываем 210 записей напрямую
    with patch("src.core.routing_policy._DECISIONS_LOG", log_file):
        for i in range(210):
            _append_decision({"ts": float(i), "task_type": f"task_{i}", "backend": "cloud", "reason": "test"})
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 200


@pytest.mark.asyncio
async def test_read_recent_decisions_returns_entries(tmp_path: Path) -> None:
    """read_recent_decisions возвращает последние N записей."""
    log_file = tmp_path / "routing_decisions.jsonl"
    with patch("src.core.routing_policy._DECISIONS_LOG", log_file):
        for i in range(10):
            _append_decision({"ts": float(i), "task_type": f"t{i}", "backend": "local", "reason": "x"})
        result = read_recent_decisions(5)
    assert len(result) <= 5
    assert all("task_type" in r for r in result)


# ---------------------------------------------------------------------------
# Тест 11: singleton
# ---------------------------------------------------------------------------


def test_get_routing_policy_returns_singleton() -> None:
    """get_routing_policy() возвращает один и тот же экземпляр."""
    p1 = get_routing_policy()
    p2 = get_routing_policy()
    assert p1 is p2


# ---------------------------------------------------------------------------
# Тест 12: RouteDecision is NamedTuple
# ---------------------------------------------------------------------------


def test_route_decision_namedtuple() -> None:
    """RouteDecision — NamedTuple с правильными полями."""
    d = RouteDecision(backend="local", model_hint=None, reason="test")
    assert d.backend == "local"
    assert d.model_hint is None
    assert d.reason == "test"
    assert d[0] == "local"


# ---------------------------------------------------------------------------
# Тест 13: overrides thread-safety
# ---------------------------------------------------------------------------


def test_set_get_clear_overrides_thread_safety() -> None:
    """set/clear overrides работают корректно в многопоточном окружении."""
    errors = []

    def worker(task: str, backend: str) -> None:
        try:
            set_task_override(task, backend)
            ovr = get_overrides()
            assert ovr.get(task) == backend
            clear_task_override(task)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    threads = [
        threading.Thread(target=worker, args=(f"task_{i}", "cloud"))
        for i in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread safety errors: {errors}"
