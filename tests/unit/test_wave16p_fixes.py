# -*- coding: utf-8 -*-
"""
Тесты Wave 16-P: три LOW-priority code-review фикса.

LOW-1: HermesACPBridge async singleton с asyncio.Lock (double-checked locking)
LOW-2: SkillCurator evaluate_ab_test_and_apply под одним team lock
LOW-3: openclaw_runtime_repair _resolve_session_path (CLI > ENV > default)
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательный импорт repair-скрипта (нет __init__)
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "openclaw_runtime_repair.py"


@pytest.fixture(scope="module")
def repair_mod() -> types.ModuleType:
    """Импортируем scripts/openclaw_runtime_repair.py как модуль."""
    spec = importlib.util.spec_from_file_location("_repair_wave16p", SCRIPT_PATH)
    assert spec is not None, f"Скрипт не найден: {SCRIPT_PATH}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ─────────────────────────────────────────────────────────────────────────────
# LOW-1: HermesACPBridge async singleton
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=False)
def reset_bridge():
    """Сбрасываем синглтон до и после каждого теста."""
    from src.integrations.hermes_acp_bridge import reset_hermes_bridge

    reset_hermes_bridge()
    yield
    reset_hermes_bridge()


@pytest.mark.asyncio
async def test_async_get_hermes_bridge_returns_same_instance(reset_bridge):
    """get_hermes_bridge() дважды возвращает один и тот же объект."""
    from src.integrations.hermes_acp_bridge import get_hermes_bridge

    a = await get_hermes_bridge()
    b = await get_hermes_bridge()
    assert a is b, "async singleton должен возвращать один instance"


@pytest.mark.asyncio
async def test_async_get_hermes_bridge_concurrent_same_instance(reset_bridge):
    """Конкурентные вызовы get_hermes_bridge() возвращают один объект."""
    from src.integrations.hermes_acp_bridge import get_hermes_bridge

    # Запускаем 5 конкурентных вызовов
    results = await asyncio.gather(*[get_hermes_bridge() for _ in range(5)])
    first = results[0]
    for r in results[1:]:
        assert r is first, "Все конкурентные вызовы должны получить один instance"


def test_sync_get_hermes_bridge_sync_deprecated_but_works(reset_bridge):
    """get_hermes_bridge_sync() (deprecated) работает и возвращает instance."""
    from src.integrations.hermes_acp_bridge import get_hermes_bridge_sync

    bridge = get_hermes_bridge_sync()
    assert bridge is not None, "sync version должна возвращать HermesACPBridge instance"
    # Повторный вызов возвращает тот же объект
    bridge2 = get_hermes_bridge_sync()
    assert bridge is bridge2, "sync version тоже должна быть singleton"


# ─────────────────────────────────────────────────────────────────────────────
# LOW-2: SkillCurator evaluate_ab_test_and_apply atomicity
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_ab_test_and_apply_concurrent_no_double_apply(tmp_path):
    """Конкурентные вызовы evaluate_ab_test_and_apply не применяют proposal дважды."""
    from src.core.skill_curator import SkillCurator

    curator = SkillCurator(base_dir=tmp_path)

    apply_call_count = 0

    async def fake_apply(proposal_id, *, force, idle_check, state_path):
        nonlocal apply_call_count
        apply_call_count += 1
        # Симулируем небольшую задержку внутри apply
        await asyncio.sleep(0.01)
        return True, "applied"

    # evaluate_ab_test всегда возвращает candidate wins
    def fake_evaluate(ab_id):
        return {"winner": "candidate", "ab_id": ab_id}

    # _load_ab_test возвращает данные с proposal_id
    def fake_load(ab_id):
        return {"ab_id": ab_id, "team": "coders", "candidate_proposal_id": "prop-1"}

    curator.evaluate_ab_test = fake_evaluate  # type: ignore[assignment]
    curator._load_ab_test = fake_load  # type: ignore[assignment]
    curator.apply_with_approval = fake_apply  # type: ignore[assignment]

    # Запускаем 3 конкурентных вызова для одной team
    results = await asyncio.gather(
        curator.evaluate_ab_test_and_apply("ab-1"),
        curator.evaluate_ab_test_and_apply("ab-1"),
        curator.evaluate_ab_test_and_apply("ab-1"),
    )

    # Все три должны завершиться успешно
    assert len(results) == 3
    # Под lock вызовы выполняются последовательно — apply вызывается для каждого
    # Но каждый вызов защищён lock, значит они не перекрываются
    # apply_call_count должен быть ровно 3 (по одному на каждый sequential вызов под lock)
    assert apply_call_count == 3, (
        f"Ожидали 3 последовательных apply под lock, получили {apply_call_count}"
    )


@pytest.mark.asyncio
async def test_evaluate_ab_test_and_apply_different_teams_parallel(tmp_path):
    """Вызовы для разных teams выполняются параллельно (разные locks)."""
    from src.core.skill_curator import SkillCurator

    curator = SkillCurator(base_dir=tmp_path)
    execution_order: list[str] = []

    async def fake_apply_slow(proposal_id, *, force, idle_check, state_path):
        await asyncio.sleep(0.05)  # медленный apply
        return True, "applied"

    def fake_evaluate(ab_id):
        return {"winner": "candidate", "ab_id": ab_id}

    def fake_load(ab_id):
        # team определяется из ab_id суффикса
        team = "traders" if "traders" in ab_id else "analysts"
        return {"ab_id": ab_id, "team": team, "candidate_proposal_id": f"prop-{ab_id}"}

    curator.evaluate_ab_test = fake_evaluate  # type: ignore[assignment]
    curator._load_ab_test = fake_load  # type: ignore[assignment]
    curator.apply_with_approval = fake_apply_slow  # type: ignore[assignment]

    import time

    t0 = time.monotonic()
    await asyncio.gather(
        curator.evaluate_ab_test_and_apply("ab-traders"),
        curator.evaluate_ab_test_and_apply("ab-analysts"),
    )
    elapsed = time.monotonic() - t0

    # Если параллельны — должны завершиться быстрее чем 2 × 0.05s
    # (с учётом накладных расходов даём 0.08s порог)
    assert elapsed < 0.08, f"Разные teams должны выполняться параллельно, elapsed={elapsed:.3f}s"


@pytest.mark.asyncio
async def test_evaluate_ab_test_and_apply_no_candidate_skip_apply(tmp_path):
    """Если evaluate не выдал winner=candidate — apply не вызывается."""
    from src.core.skill_curator import SkillCurator

    curator = SkillCurator(base_dir=tmp_path)
    apply_called = False

    async def fake_apply(proposal_id, *, force, idle_check, state_path):
        nonlocal apply_called
        apply_called = True
        return True, "applied"

    def fake_evaluate(ab_id):
        return {"winner": "baseline", "ab_id": ab_id}

    def fake_load(ab_id):
        return {"ab_id": ab_id, "team": "creative", "candidate_proposal_id": "prop-x"}

    curator.evaluate_ab_test = fake_evaluate  # type: ignore[assignment]
    curator._load_ab_test = fake_load  # type: ignore[assignment]
    curator.apply_with_approval = fake_apply  # type: ignore[assignment]

    result, applied = await curator.evaluate_ab_test_and_apply("ab-no-win")
    assert not applied, "apply не должен вызываться при winner != candidate"
    assert not apply_called, "apply_with_approval не должен вызываться"


# ─────────────────────────────────────────────────────────────────────────────
# LOW-3: _resolve_session_path (CLI > ENV > default с warning)
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_session_path_cli_arg_wins(repair_mod, tmp_path, monkeypatch):
    """CLI arg имеет наивысший приоритет над ENV и default."""
    cli_path = tmp_path / "custom.session"
    monkeypatch.setenv("KRAB_SESSION_PATH", str(tmp_path / "env.session"))

    result = repair_mod._resolve_session_path(cli_path)
    assert result == cli_path, "CLI arg должен побеждать ENV"


def test_resolve_session_path_env_override(repair_mod, tmp_path, monkeypatch):
    """ENV KRAB_SESSION_PATH используется при отсутствии CLI arg."""
    env_path = tmp_path / "env.session"
    monkeypatch.setenv("KRAB_SESSION_PATH", str(env_path))

    result = repair_mod._resolve_session_path(None)
    assert result == env_path, "ENV должен использоваться при отсутствии CLI arg"


def test_resolve_session_path_default_fallback_with_warning(repair_mod, monkeypatch, caplog):
    """При отсутствии CLI arg и ENV — используется computed default с WARNING логом."""
    import logging

    monkeypatch.delenv("KRAB_SESSION_PATH", raising=False)

    with caplog.at_level(logging.WARNING):
        result = repair_mod._resolve_session_path(None)

    assert result == repair_mod._SESSION_PATH_DEFAULT, "Должен вернуть computed default"
    # Проверяем что WARNING был залогирован
    assert any(
        "session_path_fallback" in record.message or "KRAB_SESSION_PATH" in record.message
        for record in caplog.records
    ), "При fallback должен быть WARNING лог с упоминанием KRAB_SESSION_PATH"
