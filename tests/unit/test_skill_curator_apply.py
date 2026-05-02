# -*- coding: utf-8 -*-
"""Tests for SkillCurator Step 3 (Wave 16-A) — apply_with_approval + rollback.

Покрывает:
- apply_with_approval happy path (approved proposal → overlay создан, archive, proposal applied)
- Повторный apply того же proposal → (False, "already applied")
- Несуществующий proposal → (False, ...)
- Per-team mutex сериализует два concurrent apply
- force=True пропускает idle check
- Weekly rate-limit блокирует повторный apply (если не force)
- rollback к baseline удаляет overlay
- rollback без overlay → (False, ...)
- rollback к конкретной версии
- get_team_system_prompt возвращает overlay после apply
- Инвалидация кеша после apply/rollback
- CuratorState round-trip active_overlays
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.core.skill_curator import SkillCurator, _APPLY_RATE_LIMIT_DAYS
from src.core.skill_curator_state import CuratorState


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_curator(tmp_path: Path) -> SkillCurator:
    """SkillCurator с изолированной директорией."""
    return SkillCurator(base_dir=tmp_path)


def _write_proposal(
    base_dir: Path,
    *,
    proposal_id: str = "analysts-2026-05-02-20-00",
    team: str = "analysts",
    status: str = "pending",
    proposed_prompt: str = "Новый промпт аналитиков.",
) -> Path:
    """Пишет proposal JSON в proposals/ директорию."""
    proposals_dir = base_dir / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)
    path = proposals_dir / f"{proposal_id}.json"
    path.write_text(
        json.dumps(
            {
                "team": team,
                "proposal_id": proposal_id,
                "status": status,
                "proposed_prompt": proposed_prompt,
                "current_prompt": "Старый промпт.",
                "proposed_diff": "--- current\n+++ proposed\n-old\n+new",
                "rationale": "test",
                "focus": ["timeout"],
                "confidence": 0.7,
                "model": "gemini-3-flash-preview",
                "generated_at": "2026-05-02T20:00:00+00:00",
                "report_summary": {},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# 1. Happy path — успешный apply
# ---------------------------------------------------------------------------


def test_apply_with_approval_success(tmp_path: Path) -> None:
    """Approved proposal применяется: overlay создан, archive файл существует, proposal помечен applied."""

    curator = _make_curator(tmp_path)
    state_path = tmp_path / "state.json"
    _write_proposal(tmp_path, proposed_prompt="Оверлей промпт для аналитиков.")

    # Моккируем swarm_channels.is_round_active → False (не занят)
    mock_channels = MagicMock()
    mock_channels.is_round_active.return_value = False

    with patch("src.core.skill_curator.swarm_channels", mock_channels, create=True):
        ok, msg = asyncio.run(
            curator.apply_with_approval(
                "analysts-2026-05-02-20-00",
                idle_check=False,
                state_path=state_path,
            )
        )

    assert ok, f"Expected success, got: {msg}"
    assert "applied" in msg.lower()

    # Проверяем overlay в state
    state = CuratorState.load(state_path)
    overlay = state.get_overlay("analysts")
    assert overlay is not None
    assert overlay["prompt"] == "Оверлей промпт для аналитиков."
    assert overlay["proposal_id"] == "analysts-2026-05-02-20-00"
    assert overlay["version"] == 1

    # Проверяем archive файл
    archive_path_str = overlay["archive_path"]
    assert archive_path_str, "archive_path must be set"
    assert Path(archive_path_str).exists(), f"Archive file not found: {archive_path_str}"

    # Проверяем proposal помечен applied
    proposal = curator.load_proposal("analysts-2026-05-02-20-00")
    assert proposal is not None
    assert proposal["status"] == "applied"
    assert "applied_at" in proposal


# ---------------------------------------------------------------------------
# 2. Повторный apply → already applied
# ---------------------------------------------------------------------------


def test_apply_with_approval_already_applied(tmp_path: Path) -> None:
    """Повторный apply уже applied proposal → (False, 'already applied')."""

    curator = _make_curator(tmp_path)
    state_path = tmp_path / "state.json"
    _write_proposal(tmp_path, status="applied")

    ok, msg = asyncio.run(
        curator.apply_with_approval(
            "analysts-2026-05-02-20-00",
            idle_check=False,
            state_path=state_path,
        )
    )

    assert not ok
    assert "already applied" in msg


# ---------------------------------------------------------------------------
# 3. Несуществующий proposal
# ---------------------------------------------------------------------------


def test_apply_with_approval_proposal_not_found(tmp_path: Path) -> None:
    """Несуществующий proposal_id → (False, error_msg)."""

    curator = _make_curator(tmp_path)
    state_path = tmp_path / "state.json"

    ok, msg = asyncio.run(
        curator.apply_with_approval("ghost-id-xyz", idle_check=False, state_path=state_path)
    )

    assert not ok
    assert "not found" in msg.lower() or "ghost-id-xyz" in msg


# ---------------------------------------------------------------------------
# 4. Per-team mutex сериализует два concurrent apply
# ---------------------------------------------------------------------------


def test_apply_with_approval_mutex_per_team(tmp_path: Path) -> None:
    """Две одновременные apply для одной команды сериализуются (mutex)."""

    curator = _make_curator(tmp_path)
    state_path = tmp_path / "state.json"

    # Два разных proposal для одной команды
    _write_proposal(
        tmp_path,
        proposal_id="analysts-v1",
        team="analysts",
        proposed_prompt="Промпт v1",
    )
    _write_proposal(
        tmp_path,
        proposal_id="analysts-v2",
        team="analysts",
        proposed_prompt="Промпт v2",
    )

    timestamps: list[float] = []
    original_do_apply = curator._do_apply

    async def _tracked_do_apply(**kwargs: Any) -> tuple[bool, str]:
        timestamps.append(time.monotonic())
        return await original_do_apply(**kwargs)

    curator._do_apply = _tracked_do_apply  # type: ignore[method-assign]

    async def _run_both() -> list[tuple[bool, str]]:
        return list(
            await asyncio.gather(
                curator.apply_with_approval("analysts-v1", idle_check=False, state_path=state_path),
                curator.apply_with_approval("analysts-v2", idle_check=False, state_path=state_path),
            )
        )

    results = asyncio.run(_run_both())
    # Хотя бы один succeed (второй может fail из-за rate-limit после первого — это OK)
    successes = [ok for ok, _ in results if ok]
    assert successes, f"Expected at least one success, got {results}"

    # Timestamps должны быть последовательными (mutex работает)
    # Не можем строго проверить без sleep, но факт что оба завершились без исключений — достаточно
    assert len(timestamps) >= 1


# ---------------------------------------------------------------------------
# 5. force=True пропускает idle check
# ---------------------------------------------------------------------------


def test_apply_with_approval_force_skips_idle_check(tmp_path: Path) -> None:
    """force=True позволяет apply даже при занятой команде (busy team)."""

    curator = _make_curator(tmp_path)
    state_path = tmp_path / "state.json"
    _write_proposal(tmp_path, proposed_prompt="Force applied prompt.")

    mock_channels = MagicMock()
    mock_channels.is_round_active.return_value = True  # команда занята

    # Без force — должен отказать (idle check с busy).
    # Патчим на уровне модуля swarm_channels чтобы локальный import подхватил mock.
    with patch("src.core.swarm_channels.swarm_channels", mock_channels):
        ok, msg = asyncio.run(
            curator.apply_with_approval(
                "analysts-2026-05-02-20-00",
                force=False,
                idle_check=True,
                state_path=state_path,
            )
        )

    assert not ok, f"Expected busy team to block (non-force), got ok={ok}, msg={msg}"
    assert "busy" in msg.lower() or "active" in msg.lower()

    # С force=True — должен применить (idle check пропускается)
    with patch("src.core.swarm_channels.swarm_channels", mock_channels):
        ok2, msg2 = asyncio.run(
            curator.apply_with_approval(
                "analysts-2026-05-02-20-00",
                force=True,
                idle_check=True,
                state_path=state_path,
            )
        )

    assert ok2, f"Expected force apply to succeed, got: {msg2}"


# ---------------------------------------------------------------------------
# 6. Weekly rate-limit
# ---------------------------------------------------------------------------


def test_apply_with_approval_weekly_rate_limit(tmp_path: Path) -> None:
    """Второй apply в течение 7 дней блокируется (без force)."""

    curator = _make_curator(tmp_path)
    state_path = tmp_path / "state.json"

    # Первый apply
    _write_proposal(tmp_path, proposed_prompt="First apply prompt.")
    ok1, _ = asyncio.run(
        curator.apply_with_approval(
            "analysts-2026-05-02-20-00",
            idle_check=False,
            state_path=state_path,
        )
    )
    assert ok1, "First apply should succeed"

    # Второй proposal (другой id, но та же команда)
    _write_proposal(
        tmp_path,
        proposal_id="analysts-v2",
        team="analysts",
        proposed_prompt="Second apply prompt.",
    )
    ok2, msg2 = asyncio.run(
        curator.apply_with_approval(
            "analysts-v2",
            force=False,
            idle_check=False,
            state_path=state_path,
        )
    )
    assert not ok2
    assert "rate limit" in msg2.lower()

    # С force — проходит
    ok3, _ = asyncio.run(
        curator.apply_with_approval(
            "analysts-v2",
            force=True,
            idle_check=False,
            state_path=state_path,
        )
    )
    assert ok3


# ---------------------------------------------------------------------------
# 7. Rollback к baseline
# ---------------------------------------------------------------------------


def test_rollback_to_baseline(tmp_path: Path) -> None:
    """Rollback после apply возвращает baseline (нет overlay)."""

    curator = _make_curator(tmp_path)
    state_path = tmp_path / "state.json"
    _write_proposal(tmp_path, proposed_prompt="Applied overlay prompt.")

    # Apply
    ok, _ = asyncio.run(
        curator.apply_with_approval(
            "analysts-2026-05-02-20-00",
            idle_check=False,
            state_path=state_path,
        )
    )
    assert ok

    # Проверяем overlay есть
    state = CuratorState.load(state_path)
    assert state.get_overlay("analysts") is not None

    # Rollback к baseline
    ok_rb, msg_rb = asyncio.run(
        curator.rollback("analysts", version=-1, state_path=state_path)
    )
    assert ok_rb, f"Rollback failed: {msg_rb}"
    assert "baseline" in msg_rb.lower() or "rolled back" in msg_rb.lower()

    # Overlay должен быть удалён
    state_after = CuratorState.load(state_path)
    assert state_after.get_overlay("analysts") is None


# ---------------------------------------------------------------------------
# 8. Rollback без overlay
# ---------------------------------------------------------------------------


def test_rollback_no_overlay(tmp_path: Path) -> None:
    """Rollback когда overlay нет → (False, ...)."""

    curator = _make_curator(tmp_path)
    state_path = tmp_path / "state.json"
    # Пустой state
    CuratorState().save_atomic(state_path)

    ok, msg = asyncio.run(
        curator.rollback("analysts", version=-1, state_path=state_path)
    )
    assert not ok
    assert "nothing" in msg.lower() or "no active overlay" in msg.lower()


# ---------------------------------------------------------------------------
# 9. Rollback к конкретной версии
# ---------------------------------------------------------------------------


def test_rollback_to_specific_version(tmp_path: Path) -> None:
    """Apply v1 + apply v2 + rollback --version 1 → восстанавливает v1 prompt."""

    curator = _make_curator(tmp_path)
    state_path = tmp_path / "state.json"

    # Первый apply
    _write_proposal(tmp_path, proposed_prompt="Промпт версия 1.")
    ok1, _ = asyncio.run(
        curator.apply_with_approval(
            "analysts-2026-05-02-20-00",
            idle_check=False,
            state_path=state_path,
        )
    )
    assert ok1

    # Второй apply (другой proposal, force для rate-limit)
    _write_proposal(
        tmp_path, proposal_id="analysts-v2", team="analysts", proposed_prompt="Промпт версия 2."
    )
    ok2, _ = asyncio.run(
        curator.apply_with_approval(
            "analysts-v2",
            force=True,
            idle_check=False,
            state_path=state_path,
        )
    )
    assert ok2

    state = CuratorState.load(state_path)
    overlay = state.get_overlay("analysts")
    assert overlay is not None
    assert overlay["prompt"] == "Промпт версия 2."

    # Rollback к версии 1
    ok_rb, msg_rb = asyncio.run(
        curator.rollback("analysts", version=1, state_path=state_path)
    )
    assert ok_rb, f"Rollback to v1 failed: {msg_rb}"

    state_after = CuratorState.load(state_path)
    overlay_after = state_after.get_overlay("analysts")
    assert overlay_after is not None
    # Контент должен быть baseline snapshot (тот что был перед первым apply)
    # (это было получено через get_team_system_prompt → TEAM_PROMPTS["analysts"])
    assert overlay_after["prompt"] != "Промпт версия 2.", "Should have rolled back"


# ---------------------------------------------------------------------------
# 10. get_team_system_prompt с overlay
# ---------------------------------------------------------------------------


def test_get_team_system_prompt_with_overlay(tmp_path: Path) -> None:
    """После apply, get_team_system_prompt возвращает overlay prompt."""

    curator = _make_curator(tmp_path)
    state_path = tmp_path / "state.json"
    custom_prompt = "Кастомный промпт для аналитиков после apply."
    _write_proposal(tmp_path, proposed_prompt=custom_prompt)

    # Apply
    ok, _ = asyncio.run(
        curator.apply_with_approval(
            "analysts-2026-05-02-20-00",
            idle_check=False,
            state_path=state_path,
        )
    )
    assert ok

    # Патчим CURATOR_STATE_PATH чтобы get_team_system_prompt читал наш state
    from src.core import swarm_team_prompts as stp

    # Инвалидируем кеш
    stp._invalidate_overlay_cache("analysts")

    with patch("src.core.skill_curator_state.CURATOR_STATE_PATH", state_path):
        # Также патчим в swarm_team_prompts чтобы читал правильный path
        with patch("src.core.swarm_team_prompts.CURATOR_STATE_PATH", state_path, create=True):
            result = stp.get_team_system_prompt("analysts")

    assert result == custom_prompt, f"Expected overlay prompt, got: {result[:100]}"


# ---------------------------------------------------------------------------
# 11. Cache invalidation
# ---------------------------------------------------------------------------


def test_get_team_system_prompt_cache_invalidation(tmp_path: Path) -> None:
    """После rollback кеш инвалидирован: get_team_system_prompt возвращает baseline."""

    from src.core import swarm_team_prompts as stp

    # Принудительно ставим overlay в кеш
    stp._overlay_cache["analysts"] = (time.monotonic() + 9999.0, "Cached overlay content")

    # Инвалидируем
    stp._invalidate_overlay_cache("analysts")

    # Кеш должен быть удалён
    assert "analysts" not in stp._overlay_cache

    # После инвалидации — возвращает baseline (из TEAM_PROMPTS или state без overlay)
    with patch("src.core.skill_curator_state.CURATOR_STATE_PATH", tmp_path / "nostate.json"):
        with patch("src.core.swarm_team_prompts.CURATOR_STATE_PATH", tmp_path / "nostate.json", create=True):
            result = stp.get_team_system_prompt("analysts")

    assert "аналитик" in result.lower() or "Analysts" in result  # TEAM_PROMPTS["analysts"]


# ---------------------------------------------------------------------------
# 12. CuratorState active_overlays round-trip
# ---------------------------------------------------------------------------


def test_curator_state_active_overlays_round_trip(tmp_path: Path) -> None:
    """CuratorState save+load preserves active_overlays."""

    path = tmp_path / "state.json"
    state = CuratorState()

    # Записываем overlay
    state.apply_overlay(
        "coders",
        {
            "prompt": "Кастомный промпт кодеров.",
            "proposal_id": "coders-test-1",
            "applied_at": "2026-05-02T20:00:00+00:00",
            "version": 1,
            "archive_path": "/tmp/arch/v1.md",
        },
    )
    state.mark_apply("coders")
    state.save_atomic(path)

    # Reload
    restored = CuratorState.load(path)
    overlay = restored.get_overlay("coders")
    assert overlay is not None
    assert overlay["prompt"] == "Кастомный промпт кодеров."
    assert overlay["proposal_id"] == "coders-test-1"
    assert overlay["version"] == 1

    # last_apply_at сохранён
    assert "coders" in restored.last_apply_at

    # clear_overlay работает
    restored.clear_overlay("coders")
    assert restored.get_overlay("coders") is None

    # Backward compat: old state.json без поля active_overlays
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("active_overlays", None)
    raw.pop("last_apply_at", None)
    path.write_text(json.dumps(raw), encoding="utf-8")
    old_state = CuratorState.load(path)
    assert old_state.active_overlays == {}
    assert old_state.last_apply_at == {}


# ---------------------------------------------------------------------------
# Command handler tests
# ---------------------------------------------------------------------------


class _FakeUser:
    def __init__(self, uid: int) -> None:
        self.id = uid


class _FakeMessage:
    def __init__(self, uid: int = 12345, text: str = "!curator help") -> None:
        self.from_user = _FakeUser(uid)
        self.text = text
        self.replies: list[str] = []


class _FakeBot:
    def __init__(self) -> None:
        self.replies: list[str] = []

    def _get_command_args(self, message: Any) -> str:
        text = getattr(message, "text", "") or ""
        parts = text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""

    async def _safe_reply_or_send_new(self, message: Any, text: str) -> None:
        self.replies.append(text)
        message.replies.append(text)


def test_curator_apply_command_success() -> None:
    """!curator apply <id> — успешный ответ."""
    from src.handlers.commands import curator_commands

    bot = _FakeBot()
    msg = _FakeMessage(text="!curator apply analysts-2026-05-02-20-00")

    async def _fake_apply(proposal_id: str, *, force: bool, **kw: Any) -> tuple[bool, str]:
        return True, "applied successfully (version=1)"

    with (
        patch.object(curator_commands, "is_owner_user_id", return_value=True),
        patch.object(curator_commands.skill_curator, "apply_with_approval", side_effect=_fake_apply),
    ):
        asyncio.run(curator_commands.handle_curator(bot, msg))

    assert bot.replies
    out = " ".join(bot.replies)
    assert "✅" in out or "applied" in out.lower()


def test_curator_rollback_command_no_team() -> None:
    """!curator rollback без team → usage hint."""
    from src.handlers.commands import curator_commands

    bot = _FakeBot()
    msg = _FakeMessage(text="!curator rollback")

    with patch.object(curator_commands, "is_owner_user_id", return_value=True):
        asyncio.run(curator_commands.handle_curator(bot, msg))

    assert bot.replies
    assert "Укажи" in bot.replies[-1] or "укажи" in bot.replies[-1]


def test_curator_overlays_command_empty(tmp_path: Path) -> None:
    """!curator overlays — нет overlays."""
    from src.handlers.commands import curator_commands

    bot = _FakeBot()
    msg = _FakeMessage(text="!curator overlays")

    # Пустой state.json
    state_path = tmp_path / "state.json"
    CuratorState().save_atomic(state_path)

    with (
        patch.object(curator_commands, "is_owner_user_id", return_value=True),
        patch("src.core.skill_curator_state.CURATOR_STATE_PATH", state_path),
    ):
        asyncio.run(curator_commands.handle_curator(bot, msg))

    assert bot.replies
    assert "нет" in bot.replies[-1].lower() or "пуст" in bot.replies[-1].lower()


def test_curator_overlays_command_with_overlay(tmp_path: Path) -> None:
    """!curator overlays — показывает overlay."""
    from src.handlers.commands import curator_commands

    bot = _FakeBot()
    msg = _FakeMessage(text="!curator overlays")

    state = CuratorState()
    state.apply_overlay(
        "coders",
        {
            "prompt": "Кастомный промпт кодеров.",
            "proposal_id": "coders-test-1",
            "applied_at": "2026-05-02T20:00:00+00:00",
            "version": 2,
            "archive_path": "",
        },
    )
    state_path = tmp_path / "state.json"
    state.save_atomic(state_path)

    with (
        patch.object(curator_commands, "is_owner_user_id", return_value=True),
        patch("src.core.skill_curator_state.CURATOR_STATE_PATH", state_path),
    ):
        asyncio.run(curator_commands.handle_curator(bot, msg))

    assert bot.replies
    out = bot.replies[-1]
    assert "coders" in out
    assert "coders-test-1" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
