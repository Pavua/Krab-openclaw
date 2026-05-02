# -*- coding: utf-8 -*-
"""Tests for SkillCurator dry-run analyzer (Wave 14-I, Step 1/4)."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.skill_curator import (
    CuratorReport,
    SkillCurator,
    _classify_round,
    _cluster_failure_patterns,
    _extract_success_keywords,
    ensure_curator_dirs,
)
from src.core.swarm_memory import SwarmRunRecord


def _make_record(
    *,
    team: str = "analysts",
    topic: str = "test topic",
    result: str = "ok result",
    metadata: dict | None = None,
    created_at: str | None = None,
) -> SwarmRunRecord:
    return SwarmRunRecord(
        run_id=f"{team}_{int(time.time() * 1000)}",
        team=team,
        topic=topic,
        result_summary=result,
        delegations=[],
        created_at=created_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        duration_sec=1.0,
        metadata=metadata or {},
    )


def _fake_memory(records: list[SwarmRunRecord]) -> MagicMock:
    m = MagicMock()
    m.get_recent.return_value = records
    return m


def _fake_artifacts() -> MagicMock:
    m = MagicMock()
    m.list_artifacts.return_value = []
    return m


# ---------------------------------------------------------------------------


def test_analyze_recent_rounds_success_rate(tmp_path: Path) -> None:
    """50 rounds — 35 ok, 15 fail → success_rate ≈ 0.7."""

    records: list[SwarmRunRecord] = []
    for i in range(35):
        records.append(
            _make_record(result="great analysis", metadata={"verifier": {"passed": True}})
        )
    for i in range(15):
        records.append(
            _make_record(result="timeout exceeded", metadata={"verifier": {"passed": False}})
        )

    curator = SkillCurator(
        memory=_fake_memory(records), artifact_store=_fake_artifacts(), base_dir=tmp_path
    )
    report = curator.analyze_recent_rounds("analysts", days=7)

    assert report.rounds_analyzed == 50
    assert report.success_rate == pytest.approx(0.7, abs=0.001)
    assert report.team == "analysts"


def test_analyze_finds_failure_patterns(tmp_path: Path) -> None:
    """Repeated regex matches → top-3 failure patterns."""

    records: list[SwarmRunRecord] = []
    # 5 timeouts, 3 provider_unavailable, 2 no_data, 1 mixed
    for _ in range(5):
        records.append(_make_record(result="timeout exceeded after 30s"))
    for _ in range(3):
        records.append(_make_record(result="provider_unavailable: 503"))
    for _ in range(2):
        records.append(_make_record(result="no_data returned from API"))
    records.append(_make_record(result="ok success run", metadata={"verifier": {"passed": True}}))

    curator = SkillCurator(
        memory=_fake_memory(records), artifact_store=_fake_artifacts(), base_dir=tmp_path
    )
    report = curator.analyze_recent_rounds("analysts", days=7)

    tags = [t for t, _ in report.failure_patterns]
    assert "timeout" in tags
    assert "provider_unavailable" in tags
    assert "no_data" in tags
    # timeout is most common
    assert report.failure_patterns[0][0] == "timeout"
    assert report.failure_patterns[0][1] == 5


def test_analyze_finds_success_patterns(tmp_path: Path) -> None:
    """Common keywords across success rounds → top-3 successful_patterns."""

    records: list[SwarmRunRecord] = []
    for _ in range(5):
        records.append(
            _make_record(
                topic="calculator yfinance research",
                result="result mentioning calculator yfinance research keyword",
                metadata={"verifier": {"passed": True}},
            )
        )

    curator = SkillCurator(
        memory=_fake_memory(records), artifact_store=_fake_artifacts(), base_dir=tmp_path
    )
    report = curator.analyze_recent_rounds("analysts", days=7)

    keywords = {kw for kw, _ in report.successful_patterns}
    # at least one of the planted keywords should surface
    assert keywords & {"calculator", "yfinance", "research", "keyword"}


def test_dry_run_no_mutations(tmp_path: Path) -> None:
    """Running dry-run does NOT modify state.json or prompts archive."""

    state_path = tmp_path / "state.json"
    archive_dir = tmp_path / "prompts_archive"
    # pre-populate with marker
    ensure_curator_dirs(tmp_path)
    state_path.write_text('{"sentinel": true}', encoding="utf-8")
    sample_prompt = archive_dir / "marker.md"
    sample_prompt.write_text("hello", encoding="utf-8")

    records = [_make_record(metadata={"verifier": {"passed": True}}) for _ in range(3)]
    curator = SkillCurator(
        memory=_fake_memory(records), artifact_store=_fake_artifacts(), base_dir=tmp_path
    )

    curator.analyze_recent_rounds("analysts", days=7)

    # state.json untouched
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"sentinel": True}
    # prompt archive untouched
    assert sample_prompt.read_text(encoding="utf-8") == "hello"


def test_classify_round_uses_verifier_score() -> None:
    rec_ok = _make_record(metadata={"verifier": {"score": 0.85}})
    rec_fail = _make_record(metadata={"verifier": {"score": 0.2}})
    ok, _ = _classify_round(rec_ok, [])
    bad, _ = _classify_round(rec_fail, [])
    assert ok is True
    assert bad is False


def test_cluster_failure_patterns_buckets_correctly() -> None:
    msgs = [
        "request timeout after 30s",
        "deadline exceeded for upstream",
        "rate limit 429",
        "rate_limit hit",
        "weird thing happened",
    ]
    counts = dict(_cluster_failure_patterns(msgs))
    assert counts["timeout"] == 2
    assert counts["rate_limit"] == 2
    assert counts["other"] == 1


def test_extract_success_keywords_filters_stopwords() -> None:
    texts = ["this calculator is great", "calculator finished the run", "research run completed"]
    kws = dict(_extract_success_keywords(texts))
    assert "calculator" in kws
    # stopwords filtered
    assert "this" not in kws
    assert "the" not in kws


def test_curator_report_to_markdown_empty() -> None:
    rep = CuratorReport(team="analysts", rounds_analyzed=0, success_rate=0.0, window_days=7)
    md = rep.to_markdown()
    assert "Rounds analyzed: 0" in md
    assert "Нет данных" in md


def test_curator_report_to_markdown_full() -> None:
    rep = CuratorReport(
        team="analysts",
        rounds_analyzed=35,
        success_rate=0.7142,
        failure_patterns=[("timeout", 5), ("provider_unavailable", 3), ("no_data", 2)],
        successful_patterns=[("calculator", 5), ("yfinance", 4), ("research", 3)],
        distinct_topics=12,
        recurring_failure_tags=["timeout", "provider_unavailable"],
        window_days=7,
    )
    md = rep.to_markdown()
    assert "SkillCurator dry-run — analysts" in md
    assert "71.4%" in md
    assert "timeout (5×)" in md
    assert '"calculator" (5×)' in md
    assert "Recommendation" in md


# ---------------------------------------------------------------------------
# Command-handler tests
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

    def _get_command_args(self, message):  # noqa: D401, ANN001
        # mimic stripping `!curator ` prefix
        text = getattr(message, "text", "") or ""
        parts = text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""

    async def _safe_reply_or_send_new(self, message, text):  # noqa: ANN001
        self.replies.append(text)
        message.replies.append(text)
        return None


def test_curator_command_owner_only() -> None:
    """Non-owner gets refusal."""

    from src.handlers.commands.curator_commands import handle_curator

    bot = _FakeBot()
    msg = _FakeMessage(uid=99999999, text="!curator dry-run analysts")

    with patch("src.handlers.commands.curator_commands.is_owner_user_id", return_value=False):
        asyncio.run(handle_curator(bot, msg))

    assert bot.replies, "Expected refusal reply"
    assert "владельцу" in bot.replies[0] or "🔒" in bot.replies[0]


def test_curator_help_shows_usage() -> None:
    """!curator help returns usage hint."""

    from src.handlers.commands.curator_commands import handle_curator

    bot = _FakeBot()
    msg = _FakeMessage(text="!curator help")

    with patch("src.handlers.commands.curator_commands.is_owner_user_id", return_value=True):
        asyncio.run(handle_curator(bot, msg))

    assert bot.replies
    assert "SkillCurator" in bot.replies[0]
    assert "dry-run" in bot.replies[0]


def test_curator_dry_run_runs_analyzer(tmp_path: Path) -> None:
    """!curator dry-run analysts → analyzer invoked, markdown returned."""

    from src.handlers.commands.curator_commands import handle_curator

    bot = _FakeBot()
    msg = _FakeMessage(text="!curator dry-run analysts")

    fake_report = CuratorReport(
        team="analysts", rounds_analyzed=10, success_rate=0.8, window_days=7
    )

    with (
        patch("src.handlers.commands.curator_commands.is_owner_user_id", return_value=True),
        patch("src.handlers.commands.curator_commands.skill_curator") as fake_curator,
    ):
        fake_curator.analyze_recent_rounds.return_value = fake_report
        fake_curator.render_combined_markdown.return_value = fake_report.to_markdown()
        fake_curator.save_report.return_value = tmp_path / "report.md"

        asyncio.run(handle_curator(bot, msg))

    assert bot.replies
    assert "SkillCurator dry-run — analysts" in bot.replies[0]
    fake_curator.analyze_recent_rounds.assert_called_once_with("analysts", days=7)


def test_curator_dry_run_unknown_team_rejected(tmp_path: Path) -> None:
    from src.handlers.commands.curator_commands import handle_curator

    bot = _FakeBot()
    msg = _FakeMessage(text="!curator dry-run nosuchteam")

    with patch("src.handlers.commands.curator_commands.is_owner_user_id", return_value=True):
        asyncio.run(handle_curator(bot, msg))

    assert bot.replies
    assert "не найдена" in bot.replies[0]


def test_analyze_performance_under_500ms(tmp_path: Path) -> None:
    """Analyzer should complete in <500ms на 50 rounds."""

    records = [
        _make_record(
            result="timeout" if i % 3 == 0 else "ok response",
            metadata={"verifier": {"passed": i % 3 != 0}},
        )
        for i in range(50)
    ]

    curator = SkillCurator(
        memory=_fake_memory(records), artifact_store=_fake_artifacts(), base_dir=tmp_path
    )
    t0 = time.monotonic()
    report = curator.analyze_recent_rounds("analysts", days=7)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert report.rounds_analyzed == 50
    assert elapsed_ms < 500, f"Analyzer took {elapsed_ms:.1f}ms (expected <500)"
