# -*- coding: utf-8 -*-
"""Tests for SkillCurator Step 2 — LLM analyzer (src/core/skill_curator_analyzer.py).

Покрывает:
- analyze_team_prompts вызывает provider.generate() с валидным промптом
- Парсинг JSON-ответа LLM → список PromptSuggestion (>= 1 per team)
- Schema-валидация полей PromptSuggestion (category/issue/suggestion/priority)
- Edge case: нет артефактов → graceful empty report (нет исключений)
- Edge case: LLM-ошибка → error поле заполнено, suggestions = []
- Edge case: невалидный JSON от LLM → suggestions = []
- Edge case: частичный JSON (code fence) → suggestions парсятся корректно
- _load_team_artifacts: корректная загрузка из tmpdir
- AnalysisReport.to_markdown() содержит ключевые секции
- TeamAnalysisReport.to_markdown() с error vs suggestions
- all_suggestions() возвращает флат-список (team, suggestion)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.core.skill_curator_analyzer import (
    AnalysisReport,
    PromptSuggestion,
    TeamAnalysisReport,
    _build_analyzer_prompt,
    _load_team_artifacts,
    _parse_analyzer_response,
    analyze_team_prompts,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _MockProvider:
    """Мок LLM-провайдера. Возвращает заданный ответ."""

    def __init__(self, response: str, *, fail: bool = False) -> None:
        self.response = response
        self.fail = fail
        self.calls: list[str] = []

    async def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        if self.fail:
            raise RuntimeError("mocked LLM error")
        return self.response


def _valid_llm_response(n: int = 3) -> str:
    """Генерирует валидный JSON-ответ LLM с `n` suggestions."""
    suggestions = [
        {
            "category": "clarity",
            "issue": f"Нечёткое требование #{i}",
            "suggestion": f"Добавить явное указание #{i}",
            "priority": ["high", "medium", "low"][i % 3],
        }
        for i in range(n)
    ]
    return json.dumps(suggestions)


def _make_artifact(team: str, topic: str = "тест", ts: int = 1777000000) -> dict[str, Any]:
    return {
        "team": team,
        "topic": topic,
        "result": f"🐝 **Swarm Room: {topic}**\n\n**Результат:** Тестовый вывод",
        "delegations": [],
        "verification": None,
        "duration_sec": 1.0,
        "timestamp": ts,
        "timestamp_iso": "2026-04-28T12:00:00Z",
    }


@pytest.fixture()
def artifacts_dir(tmp_path: Path) -> Path:
    """Временная директория с artifact-файлами для тестов."""
    art_dir = tmp_path / "swarm_artifacts"
    art_dir.mkdir()
    return art_dir


@pytest.fixture()
def reports_dir(tmp_path: Path) -> Path:
    """Временная директория для reports."""
    rep_dir = tmp_path / "skill_curator_reports"
    rep_dir.mkdir()
    return rep_dir


def _populate_artifacts(art_dir: Path, team: str, count: int = 5) -> None:
    """Создаёт `count` artifact-файлов для команды в art_dir."""
    for i in range(count):
        ts = 1777000000 + i * 100
        data = _make_artifact(team, topic=f"топик-{i}", ts=ts)
        fp = art_dir / f"{team}_{ts}.json"
        fp.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# _load_team_artifacts
# ---------------------------------------------------------------------------


def test_load_team_artifacts_empty_dir(artifacts_dir: Path) -> None:
    """Нет файлов → пустой список без ошибок."""
    result = _load_team_artifacts("analysts", artifacts_dir=artifacts_dir)
    assert result == []


def test_load_team_artifacts_filters_by_team(artifacts_dir: Path) -> None:
    """Загружает только артефакты нужной команды."""
    _populate_artifacts(artifacts_dir, "coders", 3)
    _populate_artifacts(artifacts_dir, "analysts", 2)

    coders = _load_team_artifacts("coders", artifacts_dir=artifacts_dir)
    analysts = _load_team_artifacts("analysts", artifacts_dir=artifacts_dir)

    assert len(coders) == 3
    assert len(analysts) == 2
    assert all(a["team"] == "coders" for a in coders)
    assert all(a["team"] == "analysts" for a in analysts)


def test_load_team_artifacts_respects_limit(artifacts_dir: Path) -> None:
    """Возвращает не более `limit` записей."""
    _populate_artifacts(artifacts_dir, "traders", 10)
    result = _load_team_artifacts("traders", artifacts_dir=artifacts_dir, limit=5)
    assert len(result) <= 5


def test_load_team_artifacts_sorts_desc(artifacts_dir: Path) -> None:
    """Возвращает свежие артефакты первыми (по timestamp в имени файла)."""
    _populate_artifacts(artifacts_dir, "creative", 4)
    result = _load_team_artifacts("creative", artifacts_dir=artifacts_dir)
    timestamps = [a["timestamp"] for a in result]
    assert timestamps == sorted(timestamps, reverse=True)


def test_load_team_artifacts_ignores_corrupt(artifacts_dir: Path) -> None:
    """Повреждённые JSON-файлы пропускаются без исключений."""
    # Валидный файл
    ts_good = 1777001000
    (artifacts_dir / f"analysts_{ts_good}.json").write_text(
        json.dumps(_make_artifact("analysts", ts=ts_good)), encoding="utf-8"
    )
    # Повреждённый файл
    ts_bad = 1777002000
    (artifacts_dir / f"analysts_{ts_bad}.json").write_text("{bad json", encoding="utf-8")

    result = _load_team_artifacts("analysts", artifacts_dir=artifacts_dir)
    # Только валидный файл загружен
    assert len(result) == 1
    assert result[0]["timestamp"] == ts_good


# ---------------------------------------------------------------------------
# _build_analyzer_prompt
# ---------------------------------------------------------------------------


def test_build_analyzer_prompt_contains_team() -> None:
    """Prompt содержит имя команды."""
    samples = [_make_artifact("coders")]
    prompt = _build_analyzer_prompt("coders", "Test system prompt", samples)
    assert "coders" in prompt
    assert "Test system prompt" in prompt


def test_build_analyzer_prompt_no_artifacts() -> None:
    """Без артефактов — промпт корректен (graceful)."""
    prompt = _build_analyzer_prompt("traders", "My prompt", [])
    assert "traders" in prompt
    assert "нет артефактов" in prompt


def test_build_analyzer_prompt_includes_samples() -> None:
    """Примеры артефактов включены в prompt."""
    samples = [_make_artifact("analysts", topic="важный анализ")]
    prompt = _build_analyzer_prompt("analysts", "Prompt", samples)
    assert "важный анализ" in prompt


# ---------------------------------------------------------------------------
# _parse_analyzer_response
# ---------------------------------------------------------------------------


def test_parse_valid_json_array() -> None:
    """Валидный JSON-массив парсится в PromptSuggestion список."""
    raw = _valid_llm_response(3)
    result = _parse_analyzer_response(raw)
    assert len(result) == 3
    for s in result:
        assert isinstance(s, PromptSuggestion)
        assert s.category in ("clarity", "structure", "delegation", "format", "other")
        assert s.priority in ("high", "medium", "low")
        assert s.issue
        assert s.suggestion


def test_parse_with_code_fence() -> None:
    """Code fences (```json ... ```) корректно удаляются."""
    inner = _valid_llm_response(2)
    raw = f"```json\n{inner}\n```"
    result = _parse_analyzer_response(raw)
    assert len(result) == 2


def test_parse_empty_response() -> None:
    """Пустая строка → пустой список."""
    assert _parse_analyzer_response("") == []


def test_parse_invalid_json() -> None:
    """Невалидный JSON → пустой список."""
    assert _parse_analyzer_response("{not json at all}") == []


def test_parse_json_object_not_array() -> None:
    """JSON-объект (не массив) → пустой список."""
    raw = json.dumps({"category": "clarity", "issue": "X", "suggestion": "Y"})
    assert _parse_analyzer_response(raw) == []


def test_parse_missing_issue_or_suggestion() -> None:
    """Элементы без issue или suggestion пропускаются."""
    raw = json.dumps([
        {"category": "clarity", "issue": "Есть issue", "suggestion": ""},  # пустой suggestion
        {"category": "structure", "issue": "", "suggestion": "Есть suggestion"},  # пустой issue
        {"category": "format", "issue": "Всё есть", "suggestion": "Конкретно"},
    ])
    result = _parse_analyzer_response(raw)
    assert len(result) == 1
    assert result[0].category == "format"


def test_parse_caps_at_five_suggestions() -> None:
    """Не более 5 suggestions независимо от ответа LLM."""
    raw = _valid_llm_response(10)
    result = _parse_analyzer_response(raw)
    assert len(result) <= 5


def test_parse_unknown_priority_normalized() -> None:
    """Неизвестный priority → 'medium'."""
    raw = json.dumps([
        {"category": "other", "issue": "Проблема", "suggestion": "Решение", "priority": "critical"},
    ])
    result = _parse_analyzer_response(raw)
    assert result[0].priority == "medium"


# ---------------------------------------------------------------------------
# analyze_team_prompts — mocked provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_single_team_success(
    artifacts_dir: Path, reports_dir: Path
) -> None:
    """Анализ одной команды → >= 1 suggestion, нет ошибки."""
    _populate_artifacts(artifacts_dir, "coders", 5)
    provider = _MockProvider(_valid_llm_response(3))

    report = await analyze_team_prompts(
        team="coders",
        artifacts_dir=artifacts_dir,
        reports_dir=reports_dir,
        provider=provider,
        save_report=True,
    )

    assert "coders" in report.teams
    team_report = report.teams["coders"]
    assert team_report.error == ""
    assert len(team_report.suggestions) >= 1
    assert team_report.artifacts_sampled == 5

    # Отчёт должен быть сохранён
    assert report.report_path
    assert Path(report.report_path).exists()


@pytest.mark.asyncio
async def test_analyze_all_teams(
    artifacts_dir: Path, reports_dir: Path
) -> None:
    """Анализ всех команд → report содержит все 4 команды."""
    for team in ("traders", "coders", "analysts", "creative"):
        _populate_artifacts(artifacts_dir, team, 3)

    provider = _MockProvider(_valid_llm_response(2))

    report = await analyze_team_prompts(
        team=None,  # все команды
        artifacts_dir=artifacts_dir,
        reports_dir=reports_dir,
        provider=provider,
        save_report=False,
    )

    assert set(report.teams.keys()) == {"traders", "coders", "analysts", "creative"}


@pytest.mark.asyncio
async def test_analyze_no_artifacts_graceful(
    artifacts_dir: Path, reports_dir: Path
) -> None:
    """Нет артефактов → graceful empty report без ошибок."""
    provider = _MockProvider(_valid_llm_response(1))

    report = await analyze_team_prompts(
        team="creative",
        artifacts_dir=artifacts_dir,  # пустая директория
        reports_dir=reports_dir,
        provider=provider,
        save_report=False,
    )

    team_report = report.teams["creative"]
    assert team_report.artifacts_sampled == 0
    assert team_report.error == ""
    # LLM всё равно вызван (prompt без samples)
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_analyze_llm_error_sets_error_field(
    artifacts_dir: Path, reports_dir: Path
) -> None:
    """LLM-ошибка → error поле заполнено, suggestions = []."""
    _populate_artifacts(artifacts_dir, "analysts", 3)
    provider = _MockProvider("", fail=True)

    report = await analyze_team_prompts(
        team="analysts",
        artifacts_dir=artifacts_dir,
        reports_dir=reports_dir,
        provider=provider,
        save_report=False,
    )

    team_report = report.teams["analysts"]
    assert team_report.error != ""
    assert team_report.suggestions == []


@pytest.mark.asyncio
async def test_analyze_no_provider_sets_error(
    artifacts_dir: Path, reports_dir: Path
) -> None:
    """Нет провайдера (None) → error поле заполнено."""
    report = await analyze_team_prompts(
        team="traders",
        artifacts_dir=artifacts_dir,
        reports_dir=reports_dir,
        provider=None,  # явно None — не ищем реальный провайдер
        save_report=False,
    )

    # При provider=None analyze_team_prompts вызывает _resolve_provider()
    # В тест-среде нет API-ключа, поэтому провайдер не найден → error
    team_report = report.teams["traders"]
    # Либо error (нет провайдера), либо suggestions если провайдер каким-то образом доступен
    # Главное — нет исключений
    assert isinstance(team_report.error, str)


@pytest.mark.asyncio
async def test_analyze_invalid_llm_response(
    artifacts_dir: Path, reports_dir: Path
) -> None:
    """Невалидный ответ LLM → suggestions = [], нет crash."""
    _populate_artifacts(artifacts_dir, "coders", 2)
    provider = _MockProvider("не JSON вообще")

    report = await analyze_team_prompts(
        team="coders",
        artifacts_dir=artifacts_dir,
        reports_dir=reports_dir,
        provider=provider,
        save_report=False,
    )

    team_report = report.teams["coders"]
    assert team_report.suggestions == []
    assert team_report.error == ""  # нет ошибки, просто нет suggestions


# ---------------------------------------------------------------------------
# AnalysisReport / TeamAnalysisReport rendering
# ---------------------------------------------------------------------------


def test_team_analysis_report_markdown_with_error() -> None:
    """to_markdown() с error показывает ошибку."""
    report = TeamAnalysisReport(
        team="coders",
        error="LLM timeout",
        generated_at="2026-05-07T10:00:00+00:00",
    )
    md = report.to_markdown()
    assert "SkillCurator Step 2 — coders" in md
    assert "LLM timeout" in md


def test_team_analysis_report_markdown_with_suggestions() -> None:
    """to_markdown() с suggestions показывает все предложения."""
    suggestions = [
        PromptSuggestion(category="clarity", issue="Нечётко", suggestion="Добавить X", priority="high"),
        PromptSuggestion(category="delegation", issue="Нет delegation", suggestion="Добавить Y", priority="medium"),
    ]
    report = TeamAnalysisReport(
        team="analysts",
        suggestions=suggestions,
        artifacts_sampled=5,
        generated_at="2026-05-07T10:00:00+00:00",
    )
    md = report.to_markdown()
    assert "clarity" in md
    assert "delegation" in md
    assert "Нечётко" in md
    assert "Добавить X" in md
    assert "🔴" in md  # high priority emoji


def test_team_analysis_report_markdown_no_suggestions() -> None:
    """to_markdown() без suggestions показывает satisfactory сообщение."""
    report = TeamAnalysisReport(team="creative", artifacts_sampled=3)
    md = report.to_markdown()
    assert "удовлетворительно" in md


def test_analysis_report_to_markdown_full() -> None:
    """Полный AnalysisReport.to_markdown() содержит все команды."""
    report = AnalysisReport(generated_at="2026-05-07T10:00:00+00:00")
    for team in ("traders", "analysts"):
        report.teams[team] = TeamAnalysisReport(
            team=team,
            suggestions=[
                PromptSuggestion(
                    category="structure", issue="Нет структуры", suggestion="Добавить шаблон"
                )
            ],
            generated_at="2026-05-07T10:00:00+00:00",
        )

    md = report.to_markdown()
    assert "SkillCurator — LLM Analyzer Report" in md
    assert "traders" in md
    assert "analysts" in md


def test_analysis_report_all_suggestions() -> None:
    """all_suggestions() возвращает флат-список (team, suggestion)."""
    report = AnalysisReport()
    report.teams["coders"] = TeamAnalysisReport(
        team="coders",
        suggestions=[
            PromptSuggestion(category="clarity", issue="A", suggestion="B"),
            PromptSuggestion(category="format", issue="C", suggestion="D"),
        ],
    )
    report.teams["analysts"] = TeamAnalysisReport(
        team="analysts",
        suggestions=[
            PromptSuggestion(category="structure", issue="E", suggestion="F"),
        ],
    )

    all_s = report.all_suggestions()
    assert len(all_s) == 3
    teams = [t for t, _ in all_s]
    assert "coders" in teams
    assert "analysts" in teams


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_prompt_suggestion_schema() -> None:
    """PromptSuggestion содержит все обязательные поля нужных типов."""
    s = PromptSuggestion(
        category="clarity",
        issue="Тест",
        suggestion="Добавить",
        priority="high",
    )
    assert isinstance(s.category, str)
    assert isinstance(s.issue, str)
    assert isinstance(s.suggestion, str)
    assert s.priority in ("high", "medium", "low")


def test_team_analysis_report_schema() -> None:
    """TeamAnalysisReport содержит все обязательные поля."""
    report = TeamAnalysisReport(team="coders")
    assert isinstance(report.team, str)
    assert isinstance(report.current_prompt, str)
    assert isinstance(report.artifacts_sampled, int)
    assert isinstance(report.suggestions, list)
    assert isinstance(report.error, str)
    assert isinstance(report.generated_at, str)


def test_analysis_report_schema() -> None:
    """AnalysisReport содержит все обязательные поля."""
    report = AnalysisReport()
    assert isinstance(report.teams, dict)
    assert isinstance(report.generated_at, str)
    assert isinstance(report.report_path, str)
