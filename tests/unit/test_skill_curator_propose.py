# -*- coding: utf-8 -*-
"""Tests for SkillCurator Step 2 (Wave 15-C) — LLM proposer.

Покрывает:
- propose_prompt_update обращается к provider.generate(...)
- proposal сохраняется в proposals/{proposal_id}.json
- low success_rate → focus теги формируются из failure patterns
- list_proposals возвращает корректный список
- !curator propose owner-only
- CuratorState atomic load/save round-trip
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.core.skill_curator import (
    CuratorReport,
    PromptProposal,
    SkillCurator,
    _build_proposer_prompt,
    _parse_proposer_response,
)
from src.core.skill_curator_state import CuratorState


class _StubProvider:
    """Минимальный мок Gemini provider — отдаёт заданный JSON-ответ."""

    def __init__(self, response: str, *, fail: bool = False) -> None:
        self.response = response
        self.fail = fail
        self.calls: list[str] = []

    async def generate(self, prompt: str) -> str:  # noqa: D401
        self.calls.append(prompt)
        if self.fail:
            raise RuntimeError("mocked LLM failure")
        return self.response


def _make_report(*, success_rate: float = 0.7, rounds: int = 12) -> CuratorReport:
    return CuratorReport(
        team="analysts",
        rounds_analyzed=rounds,
        success_rate=success_rate,
        failure_patterns=[("timeout", 4), ("parse_error", 2)],
        successful_patterns=[("анализ", 5), ("отчёт", 3)],
        distinct_topics=6,
        recurring_failure_tags=["timeout", "parse_error"],
        window_days=7,
        generated_at="2026-05-02T20:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def test_curator_state_load_save_round_trip(tmp_path: Path) -> None:
    """CuratorState atomic save+load preserves fields."""
    path = tmp_path / "state.json"
    state = CuratorState()
    state.mark_run("traders", report_path=tmp_path / "rep.md")
    state.mark_proposal("traders", tmp_path / "prop.json")
    state.paused = True
    state.save_atomic(path)

    restored = CuratorState.load(path)
    assert restored.paused is True
    assert restored.run_count.get("traders") == 1
    assert "traders" in restored.last_run_at
    assert restored.last_proposal_paths.get("traders", "").endswith("prop.json")


def test_curator_state_load_missing_returns_empty(tmp_path: Path) -> None:
    """Missing state.json → empty CuratorState."""
    state = CuratorState.load(tmp_path / "nope.json")
    assert state.run_count == {}
    assert state.paused is False


def test_curator_state_load_corrupt_returns_empty(tmp_path: Path) -> None:
    """Corrupt JSON → empty CuratorState (no raise)."""
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    state = CuratorState.load(path)
    assert state.run_count == {}


# ---------------------------------------------------------------------------
# Proposer prompt building / response parsing
# ---------------------------------------------------------------------------


def test_build_proposer_prompt_includes_failure_context() -> None:
    report = _make_report(success_rate=0.4)
    prompt = _build_proposer_prompt("analysts", "Ты аналитик.", report)
    assert "analysts" in prompt
    assert "timeout" in prompt
    assert "Ты аналитик." in prompt
    assert "JSON" in prompt


def test_parse_proposer_response_extracts_json_with_fences() -> None:
    raw = (
        '```json\n{"proposed_prompt": "new", "rationale": "fix timeout", "focus": ["timeout"]}\n```'
    )
    parsed = _parse_proposer_response(raw, current_prompt="old")
    assert parsed is not None
    assert parsed["proposed_prompt"] == "new"
    assert parsed["focus"] == ["timeout"]


def test_parse_proposer_response_rejects_empty_proposed() -> None:
    raw = '{"proposed_prompt": "", "rationale": "x"}'
    assert _parse_proposer_response(raw, current_prompt="old") is None


def test_parse_proposer_response_rejects_garbage() -> None:
    assert _parse_proposer_response("not json at all", "old") is None
    assert _parse_proposer_response("", "old") is None


# ---------------------------------------------------------------------------
# propose_prompt_update
# ---------------------------------------------------------------------------


def test_propose_prompt_update_calls_llm(tmp_path: Path) -> None:
    """propose_prompt_update invokes provider.generate and returns PromptProposal."""
    curator = SkillCurator(base_dir=tmp_path)
    report = _make_report(success_rate=0.5)
    provider = _StubProvider(
        '{"proposed_prompt": "Ты аналитик. Особо следи за timeout — переспрашивай при tool failures.", '
        '"rationale": "Снизить timeout failures", "focus": ["timeout"]}'
    )

    proposal = asyncio.run(
        curator.propose_prompt_update(
            "analysts",
            "Ты аналитик.",
            report,
            provider=provider,
        )
    )

    assert proposal is not None
    assert isinstance(proposal, PromptProposal)
    assert proposal.team == "analysts"
    assert "timeout" in proposal.proposed_prompt
    assert provider.calls, "Provider must be invoked"
    assert proposal.status == "pending"
    assert proposal.proposed_diff, "Diff must be non-empty when prompts differ"


def test_propose_saves_to_proposals_dir(tmp_path: Path) -> None:
    """Proposal file created в proposals/{team}-{ts}.json."""
    curator = SkillCurator(base_dir=tmp_path)
    report = _make_report()
    provider = _StubProvider(
        '{"proposed_prompt": "новый промпт", "rationale": "test", "focus": []}'
    )

    proposal = asyncio.run(
        curator.propose_prompt_update("coders", "старый промпт", report, provider=provider)
    )

    assert proposal is not None
    proposals_dir = tmp_path / "proposals"
    assert proposals_dir.exists()
    files = list(proposals_dir.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["team"] == "coders"
    assert data["proposed_prompt"] == "новый промпт"
    assert data["status"] == "pending"


def test_propose_with_low_success_rate_focuses_failures(tmp_path: Path) -> None:
    """Report success_rate=0.3 + LLM без focus → fallback derives focus from failure patterns."""
    curator = SkillCurator(base_dir=tmp_path)
    report = _make_report(success_rate=0.3, rounds=15)
    # LLM не вернул focus — должен быть derived
    provider = _StubProvider(
        '{"proposed_prompt": "fixed prompt", "rationale": "low success", "focus": []}'
    )

    proposal = asyncio.run(
        curator.propose_prompt_update("analysts", "old", report, provider=provider)
    )
    assert proposal is not None
    # focus заполнен из recurring_failure_tags
    assert "timeout" in proposal.focus
    # confidence > base поскольку success_rate низкий
    assert proposal.confidence >= 0.6


def test_propose_returns_none_when_provider_unavailable(tmp_path: Path) -> None:
    """No provider + no default → returns None, no raise."""
    curator = SkillCurator(base_dir=tmp_path)
    report = _make_report()

    with patch("src.core.gemini_rerank_provider.default_provider", return_value=None):
        proposal = asyncio.run(
            curator.propose_prompt_update("analysts", "old", report, provider=None)
        )
    assert proposal is None


def test_propose_returns_none_on_llm_failure(tmp_path: Path) -> None:
    """LLM raises → caught, returns None, warning logged."""
    curator = SkillCurator(base_dir=tmp_path)
    report = _make_report()
    provider = _StubProvider("ignored", fail=True)

    proposal = asyncio.run(
        curator.propose_prompt_update("analysts", "old", report, provider=provider)
    )
    assert proposal is None


def test_proposals_list(tmp_path: Path) -> None:
    """list_proposals returns multiple proposals newest first."""
    curator = SkillCurator(base_dir=tmp_path)
    report = _make_report()
    provider = _StubProvider('{"proposed_prompt": "new", "rationale": "r", "focus": []}')

    asyncio.run(curator.propose_prompt_update("analysts", "old1", report, provider=provider))
    asyncio.run(curator.propose_prompt_update("traders", "old2", report, provider=provider))

    all_proposals = curator.list_proposals()
    assert len(all_proposals) == 2

    analysts_only = curator.list_proposals(team="analysts")
    assert len(analysts_only) == 1
    assert analysts_only[0]["team"] == "analysts"


def test_load_proposal_by_id(tmp_path: Path) -> None:
    """load_proposal returns dict by proposal_id."""
    curator = SkillCurator(base_dir=tmp_path)
    report = _make_report()
    provider = _StubProvider('{"proposed_prompt": "ok", "rationale": "r", "focus": []}')
    proposal = asyncio.run(
        curator.propose_prompt_update("creative", "x", report, provider=provider)
    )
    assert proposal is not None

    data = curator.load_proposal(proposal.proposal_id)
    assert data is not None
    assert data["proposal_id"] == proposal.proposal_id

    missing = curator.load_proposal("nonexistent-id")
    assert missing is None


def test_propose_updates_curator_state(tmp_path: Path) -> None:
    """propose_prompt_update mutates state.json (via patched CURATOR_STATE_PATH)."""
    state_path = tmp_path / "state.json"
    curator = SkillCurator(base_dir=tmp_path)
    report = _make_report()
    provider = _StubProvider('{"proposed_prompt": "ok", "rationale": "r", "focus": []}')

    with patch("src.core.skill_curator.CURATOR_STATE_PATH", state_path):
        proposal = asyncio.run(
            curator.propose_prompt_update("analysts", "old", report, provider=provider)
        )

    assert proposal is not None
    assert state_path.exists()
    state = CuratorState.load(state_path)
    assert state.run_count.get("analysts") == 1
    assert "analysts" in state.last_proposal_paths


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


def test_propose_command_owner_only() -> None:
    """Non-owner получает отказ для !curator propose."""
    from src.handlers.commands.curator_commands import handle_curator

    bot = _FakeBot()
    msg = _FakeMessage(uid=99999, text="!curator propose analysts")

    with patch("src.handlers.commands.curator_commands.is_owner_user_id", return_value=False):
        asyncio.run(handle_curator(bot, msg))

    assert bot.replies, "Expected refusal reply"
    assert "владельцу" in bot.replies[0] or "🔒" in bot.replies[0]


def test_propose_command_invokes_curator(tmp_path: Path) -> None:
    """!curator propose <team> вызывает skill_curator.propose_prompt_update."""
    from src.handlers.commands import curator_commands

    bot = _FakeBot()
    msg = _FakeMessage(text="!curator propose analysts")

    fake_proposal = PromptProposal(
        team="analysts",
        current_prompt="old",
        proposed_prompt="new",
        proposed_diff="--- current\n+++ proposed\n-old\n+new",
        rationale="testing",
        focus=["timeout"],
        confidence=0.7,
        model="gemini-3-flash-preview",
        proposal_id="analysts-2026-05-02-20-00",
        generated_at="2026-05-02T20:00:00+00:00",
    )

    async def _fake_propose(*args: Any, **kwargs: Any) -> PromptProposal:
        return fake_proposal

    fake_report = _make_report()

    with (
        patch.object(curator_commands, "is_owner_user_id", return_value=True),
        patch.object(
            curator_commands.skill_curator, "analyze_recent_rounds", return_value=fake_report
        ),
        patch.object(
            curator_commands.skill_curator, "propose_prompt_update", side_effect=_fake_propose
        ),
    ):
        asyncio.run(curator_commands.handle_curator(bot, msg))

    assert bot.replies, "Expected reply with proposal summary"
    out = bot.replies[-1]
    assert "analysts-2026-05-02-20-00" in out
    assert "diff" in out.lower()


def test_proposals_list_command_empty(tmp_path: Path) -> None:
    """!curator proposals — empty list message."""
    from src.handlers.commands import curator_commands

    bot = _FakeBot()
    msg = _FakeMessage(text="!curator proposals")

    with (
        patch.object(curator_commands, "is_owner_user_id", return_value=True),
        patch.object(curator_commands.skill_curator, "list_proposals", return_value=[]),
    ):
        asyncio.run(curator_commands.handle_curator(bot, msg))

    assert bot.replies
    assert "нет" in bot.replies[-1].lower() or "пуст" in bot.replies[-1].lower()


def test_proposals_list_command_with_entries() -> None:
    """!curator proposals — список с записями."""
    from src.handlers.commands import curator_commands

    bot = _FakeBot()
    msg = _FakeMessage(text="!curator proposals")

    entries = [
        {
            "proposal_id": "analysts-2026-05-02-20-00",
            "team": "analysts",
            "status": "pending",
            "confidence": 0.7,
        },
        {
            "proposal_id": "coders-2026-05-02-19-00",
            "team": "coders",
            "status": "pending",
            "confidence": 0.5,
        },
    ]

    with (
        patch.object(curator_commands, "is_owner_user_id", return_value=True),
        patch.object(curator_commands.skill_curator, "list_proposals", return_value=entries),
    ):
        asyncio.run(curator_commands.handle_curator(bot, msg))

    assert bot.replies
    out = bot.replies[-1]
    assert "analysts-2026-05-02-20-00" in out
    assert "coders-2026-05-02-19-00" in out


def test_show_command_unknown_id() -> None:
    from src.handlers.commands import curator_commands

    bot = _FakeBot()
    msg = _FakeMessage(text="!curator show ghost-id")

    with (
        patch.object(curator_commands, "is_owner_user_id", return_value=True),
        patch.object(curator_commands.skill_curator, "load_proposal", return_value=None),
    ):
        asyncio.run(curator_commands.handle_curator(bot, msg))

    assert bot.replies
    assert "не найден" in bot.replies[-1].lower()


def test_propose_command_no_team_argument() -> None:
    """!curator propose без team → usage hint."""
    from src.handlers.commands import curator_commands

    bot = _FakeBot()
    msg = _FakeMessage(text="!curator propose")

    with patch.object(curator_commands, "is_owner_user_id", return_value=True):
        asyncio.run(curator_commands.handle_curator(bot, msg))

    assert bot.replies
    assert "Укажи" in bot.replies[-1] or "укажи" in bot.replies[-1]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
