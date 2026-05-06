# -*- coding: utf-8 -*-
"""
tests/unit/test_skill_curator_daily_loop.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Wave 44-A: тесты для SkillCurator automated daily loop.

6 тестов:
1. run_daily ENV disabled → no-op (возвращает 0, файлы не создаются)
2. run_daily ENV enabled → создаёт daily JSON для всех 4 команд
3. run_daily analyze fail одной команды → graceful (остальные 3 продолжают)
4. run_weekly_propose не понедельник → skip (возвращает 0)
5. run_weekly_propose AUTO_PROPOSE disabled → skip (возвращает 0)
6. 7 daily analyses с recurring pattern → trigger auto-propose
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_analysis(
    team: str,
    success_rate: float = 0.8,
    failure_patterns: list | None = None,
) -> dict:
    """Создаёт минимальный daily analysis dict."""
    return {
        "team": team,
        "date": "2026-05-06",
        "rounds_analyzed": 10,
        "success_rate": success_rate,
        "failure_patterns": failure_patterns or [],
        "successful_patterns": [],
        "distinct_topics": 3,
        "recurring_failure_tags": [],
        "window_days": 7,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _make_report_mock(team: str, success_rate: float = 0.8) -> MagicMock:
    """Создаёт mock CuratorReport."""
    report = MagicMock()
    report.team = team
    report.rounds_analyzed = 10
    report.success_rate = success_rate
    report.failure_patterns = []
    report.successful_patterns = []
    report.distinct_topics = 3
    report.recurring_failure_tags = []
    report.window_days = 7
    report.generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return report


# ---------------------------------------------------------------------------
# Test 1: run_daily ENV disabled → no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_daily_disabled_returns_zero(tmp_path: Path) -> None:
    """Если DAILY_ENABLED=False → run_daily() возвращает 0, файлы не создаются."""
    import scripts.skill_curator_daily_loop as loop_module

    # Переопределяем DAILY_DIR на tmp
    original_daily_dir = loop_module.DAILY_DIR
    loop_module.DAILY_DIR = tmp_path / "daily"

    try:
        # Патчим ENV gate
        with patch.object(loop_module, "DAILY_ENABLED", False):
            result = await loop_module.run_daily()

        assert result == 0
        # Файлы не должны быть созданы
        assert not (tmp_path / "daily").exists() or not any((tmp_path / "daily").rglob("*.json"))
    finally:
        loop_module.DAILY_DIR = original_daily_dir


# ---------------------------------------------------------------------------
# Test 2: run_daily ENV enabled → создаёт daily files для всех 4 команд
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_daily_creates_files_for_all_teams(tmp_path: Path) -> None:
    """run_daily() создаёт JSON-файл для каждой из 4 команд."""
    import scripts.skill_curator_daily_loop as loop_module

    daily_dir = tmp_path / "daily"
    original_daily_dir = loop_module.DAILY_DIR
    loop_module.DAILY_DIR = daily_dir

    # Mock curator.analyze_recent_rounds для всех команд
    mock_curator = MagicMock()
    mock_curator.analyze_recent_rounds.side_effect = lambda team, days=7: _make_report_mock(team)

    try:
        with (
            patch.object(loop_module, "DAILY_ENABLED", True),
            patch(
                "scripts.skill_curator_daily_loop.sys",
            ),
        ):
            # Патчим импорт skill_curator внутри функции
            with patch.dict(
                "sys.modules",
                {
                    "src.core.skill_curator": MagicMock(
                        SkillCurator=MagicMock,
                        skill_curator=mock_curator,
                    )
                },
            ):
                result = await loop_module.run_daily()

    finally:
        loop_module.DAILY_DIR = original_daily_dir

    assert result == 4

    # Проверяем что созданы файлы для всех 4 команд
    for team in ["traders", "coders", "analysts", "creative"]:
        team_files = list((daily_dir / team).glob("*.json"))
        assert len(team_files) == 1, f"Expected 1 file for team={team}, got {len(team_files)}"
        data = json.loads(team_files[0].read_text())
        assert data["team"] == team
        assert "success_rate" in data
        assert "rounds_analyzed" in data


# ---------------------------------------------------------------------------
# Test 3: run_daily — analyze fail одной команды → graceful (остальные OK)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_daily_partial_failure_is_graceful(tmp_path: Path) -> None:
    """Если analyze одной команды падает с исключением → остальные 3 продолжают работу."""
    import scripts.skill_curator_daily_loop as loop_module

    daily_dir = tmp_path / "daily"
    original_daily_dir = loop_module.DAILY_DIR
    loop_module.DAILY_DIR = daily_dir

    def _analyze_side_effect(team: str, days: int = 7) -> MagicMock:
        if team == "coders":
            raise RuntimeError("simulated failure for coders")
        return _make_report_mock(team)

    mock_curator = MagicMock()
    mock_curator.analyze_recent_rounds.side_effect = _analyze_side_effect

    try:
        with patch.object(loop_module, "DAILY_ENABLED", True):
            with patch.dict(
                "sys.modules",
                {
                    "src.core.skill_curator": MagicMock(
                        SkillCurator=MagicMock,
                        skill_curator=mock_curator,
                    )
                },
            ):
                result = await loop_module.run_daily()
    finally:
        loop_module.DAILY_DIR = original_daily_dir

    # 3 успешных (traders, analysts, creative), coders упал
    assert result == 3

    # Проверяем что для upbитых команд файлы есть, для coders — нет
    for team in ["traders", "analysts", "creative"]:
        assert (daily_dir / team).exists()
        assert len(list((daily_dir / team).glob("*.json"))) == 1

    # coders — нет файлов
    assert not (daily_dir / "coders").exists() or len(
        list((daily_dir / "coders").glob("*.json"))
    ) == 0


# ---------------------------------------------------------------------------
# Test 4: run_weekly_propose не понедельник → skip (возвращает 0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_weekly_propose_not_monday_skips(tmp_path: Path) -> None:
    """run_weekly_propose() возвращает 0 если сегодня не понедельник."""
    import scripts.skill_curator_daily_loop as loop_module

    with (
        patch.object(loop_module, "WEEKLY_AUTO_PROPOSE", True),
        patch.object(loop_module, "_is_monday", return_value=False),
    ):
        result = await loop_module.run_weekly_propose()

    assert result == 0


# ---------------------------------------------------------------------------
# Test 5: run_weekly_propose AUTO_PROPOSE disabled → skip (возвращает 0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_weekly_propose_disabled_skips() -> None:
    """run_weekly_propose() возвращает 0 если WEEKLY_AUTO_PROPOSE=False."""
    import scripts.skill_curator_daily_loop as loop_module

    with patch.object(loop_module, "WEEKLY_AUTO_PROPOSE", False):
        result = await loop_module.run_weekly_propose()

    assert result == 0


# ---------------------------------------------------------------------------
# Test 6: 7 daily analyses с recurring pattern → trigger auto-propose
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_weekly_propose_recurring_pattern_triggers_propose(tmp_path: Path) -> None:
    """Если 5+ из 7 daily analyses показывают один failure pattern → вызывается propose."""
    import scripts.skill_curator_daily_loop as loop_module

    daily_dir = tmp_path / "daily"
    proposals_dir = tmp_path / "proposals"
    original_daily_dir = loop_module.DAILY_DIR
    original_proposals_dir = loop_module.PROPOSALS_DIR
    loop_module.DAILY_DIR = daily_dir
    loop_module.PROPOSALS_DIR = proposals_dir

    # Создаём 7 daily JSON-файлов для команды traders с recurring pattern "timeout"
    (daily_dir / "traders").mkdir(parents=True)
    for i in range(7):
        day_str = f"2026-04-{30 - i:02d}"
        analysis = _make_analysis(
            team="traders",
            success_rate=0.4,
            failure_patterns=[["timeout", 3], ["other", 1]],  # timeout recurring
        )
        (daily_dir / "traders" / f"{day_str}.json").write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # Mock proposal object
    mock_proposal = MagicMock()
    mock_proposal.proposal_id = "traders-2026-05-06-03-30"
    mock_proposal.to_dict.return_value = {
        "team": "traders",
        "proposal_id": "traders-2026-05-06-03-30",
        "status": "pending",
        "proposed_prompt": "updated prompt text",
        "rationale": "fix timeout patterns",
        "focus": ["timeout"],
        "confidence": 0.75,
        "model": "gemini-3-flash-preview",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report_summary": {},
    }

    # Mock curator и swarm_team_prompts
    mock_curator = MagicMock()
    mock_curator.propose_prompt_update = AsyncMock(return_value=mock_proposal)
    mock_curator.list_proposals.return_value = []

    try:
        with (
            patch.object(loop_module, "WEEKLY_AUTO_PROPOSE", True),
            patch.object(loop_module, "_is_monday", return_value=True),
            patch.dict(
                "sys.modules",
                {
                    "src.core.skill_curator": MagicMock(
                        SkillCurator=MagicMock,
                        skill_curator=mock_curator,
                        CuratorReport=MagicMock(
                            side_effect=lambda **kw: MagicMock(**kw)
                        ),
                    ),
                    "src.core.swarm_team_prompts": MagicMock(
                        get_team_system_prompt=MagicMock(return_value="current prompt text")
                    ),
                },
            ),
        ):
            result = await loop_module.run_weekly_propose()
    finally:
        loop_module.DAILY_DIR = original_daily_dir
        loop_module.PROPOSALS_DIR = original_proposals_dir

    # traders должен был получить auto-proposal
    assert result >= 1

    # propose_prompt_update должен был быть вызван для traders
    mock_curator.propose_prompt_update.assert_called()
    call_kwargs = mock_curator.propose_prompt_update.call_args
    assert call_kwargs[1]["team"] == "traders" or call_kwargs[0][0] == "traders"

    # Auto-proposal файл сохранён
    proposals_dir.mkdir(parents=True, exist_ok=True)
    saved_proposals = list(proposals_dir.glob("traders_auto_*.json"))
    assert len(saved_proposals) >= 1
    data = json.loads(saved_proposals[0].read_text())
    assert data.get("auto_proposed") is True
    assert data.get("recurring_tag") == "timeout"
