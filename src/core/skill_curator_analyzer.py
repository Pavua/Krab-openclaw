# -*- coding: utf-8 -*-
"""
src/core/skill_curator_analyzer.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SkillCurator Step 2 — LLM analyzer для swarm prompts.

Читает:
- реальные artifact-файлы из ``~/.openclaw/krab_runtime_state/swarm_artifacts/``
- текущие system prompts из ``swarm_team_prompts.py``

Вызывает LLM (через gemini_rerank_provider) и предлагает 3-5 улучшений промпта:
чёткость, структура, cross-team delegation, consistency форматов вывода.

Сохраняет markdown-отчёт в:
``~/.openclaw/krab_runtime_state/skill_curator_reports/<YYYY-MM-DD-HH-MM-SS>-<team>.md``

Дизайн в CLAUDE.md § SkillCurator Steps 2-4.
Step 2 только читает и предлагает — никаких мутаций состояния.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Пути хранилища
# ---------------------------------------------------------------------------

ARTIFACTS_DIR = Path.home() / ".openclaw" / "krab_runtime_state" / "swarm_artifacts"
REPORTS_DIR = Path.home() / ".openclaw" / "krab_runtime_state" / "skill_curator_reports"

# Поддерживаемые команды
KNOWN_TEAMS = ("traders", "coders", "analysts", "creative")

# Количество артефактов для сэмплирования (последних по timestamp)
ARTIFACT_SAMPLE_SIZE = 8

# Максимальная длина одного sample в промпте (символов)
SAMPLE_MAX_CHARS = 600

# Максимальная длина LLM-ответа
LLM_MAX_TOKENS = 2048

# Таймаут LLM-вызова (секунды)
LLM_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Типы данных
# ---------------------------------------------------------------------------


@dataclass
class PromptSuggestion:
    """Одна предложенная правка промпта от LLM.

    Поля:
    - ``category``  — классификатор: clarity/structure/delegation/format/other.
    - ``issue``     — описание проблемы (что именно нечётко/отсутствует).
    - ``suggestion`` — конкретная правка или добавление.
    - ``priority``  — high/medium/low.
    """

    category: str
    issue: str
    suggestion: str
    priority: str = "medium"


@dataclass
class TeamAnalysisReport:
    """Отчёт анализа одной команды.

    Поля:
    - ``team``              — имя команды.
    - ``current_prompt``    — snapshot текущего промпта.
    - ``artifacts_sampled`` — сколько артефактов было проанализировано.
    - ``suggestions``       — список предложений от LLM.
    - ``raw_llm_response``  — сырой ответ LLM (для отладки).
    - ``error``             — описание ошибки если анализ не удался.
    - ``generated_at``      — ISO timestamp генерации.
    """

    team: str
    current_prompt: str = ""
    artifacts_sampled: int = 0
    suggestions: list[PromptSuggestion] = field(default_factory=list)
    raw_llm_response: str = ""
    error: str = ""
    generated_at: str = ""

    def to_markdown(self) -> str:
        """Форматирует отчёт команды в markdown."""
        ts = self.generated_at or _now_iso()
        lines = [
            f"## SkillCurator Step 2 — {self.team}",
            f"*Сгенерировано: {ts}*",
            f"*Артефактов проанализировано: {self.artifacts_sampled}*",
            "",
        ]

        if self.error:
            lines += [f"**Ошибка анализа:** {self.error}", ""]
            return "\n".join(lines)

        if not self.suggestions:
            lines += [
                "*LLM не выявил существенных проблем. Промпт выглядит удовлетворительно.*",
                "",
            ]
            return "\n".join(lines)

        lines.append("### Предложения по улучшению промпта")
        lines.append("")

        for i, s in enumerate(self.suggestions, 1):
            priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(s.priority, "⚪")
            lines += [
                f"**{i}. [{s.category}]** {priority_emoji} {s.priority}",
                f"- **Проблема:** {s.issue}",
                f"- **Предложение:** {s.suggestion}",
                "",
            ]

        return "\n".join(lines)


@dataclass
class AnalysisReport:
    """Сводный отчёт по всем командам (результат analyze_team_prompts).

    Поля:
    - ``teams``          — map team_name → TeamAnalysisReport.
    - ``generated_at``   — ISO timestamp.
    - ``report_path``    — путь к сохранённому markdown-файлу (если был сохранён).
    """

    teams: dict[str, TeamAnalysisReport] = field(default_factory=dict)
    generated_at: str = ""
    report_path: str = ""

    def to_markdown(self) -> str:
        """Форматирует полный сводный отчёт."""
        ts = self.generated_at or _now_iso()
        sections = [
            "# SkillCurator — LLM Analyzer Report",
            f"*{ts}*",
            "",
        ]
        for team_report in self.teams.values():
            sections.append(team_report.to_markdown())
            sections.append("---")
            sections.append("")
        return "\n".join(sections)

    def all_suggestions(self) -> list[tuple[str, PromptSuggestion]]:
        """Возвращает все suggestions в виде (team, suggestion) для удобного перебора."""
        result: list[tuple[str, PromptSuggestion]] = []
        for team, report in self.teams.items():
            for s in report.suggestions:
                result.append((team, s))
        return result


# ---------------------------------------------------------------------------
# Системный промпт для LLM-анализатора
# ---------------------------------------------------------------------------

_ANALYZER_SYSTEM = """\
Ты — SkillCurator, компонент self-improvement системы для мультиагентного свёрма.
Анализируешь системный промпт команды и реальные примеры её работы.
Твоя задача — найти 3-5 конкретных проблем и предложить улучшения.

Категории проблем:
- clarity: нечёткие инструкции, двусмысленные требования
- structure: отсутствие явной структуры вывода, порядка шагов
- delegation: нет явных подсказок когда/как делегировать другим командам
- format: непоследовательность форматирования ответов в примерах
- other: прочие проблемы

Правила:
1. Будь конкретным — цитируй части промпта которые улучшаешь
2. Предлагай точечные добавления, не переписывай промпт целиком
3. Оценивай приоритет: high (явная проблема в примерах), medium (улучшение), low (косметика)
4. Отвечай СТРОГО валидным JSON-массивом без markdown-обёртки

Формат ответа — JSON-массив объектов:
[
  {
    "category": "clarity|structure|delegation|format|other",
    "issue": "краткое описание проблемы",
    "suggestion": "конкретное предложение текста для добавления/изменения",
    "priority": "high|medium|low"
  }
]
"""


def _build_analyzer_prompt(
    team: str,
    current_prompt: str,
    samples: list[dict[str, Any]],
) -> str:
    """Формирует полный prompt для LLM-анализатора.

    Args:
        team:           имя команды.
        current_prompt: текущий system prompt команды.
        samples:        список artifact-записей (до ARTIFACT_SAMPLE_SIZE).

    Returns:
        Полный текст промпта для LLM.
    """
    # Сокращаем примеры до SAMPLE_MAX_CHARS каждый
    sample_blocks: list[str] = []
    for i, art in enumerate(samples[:ARTIFACT_SAMPLE_SIZE], 1):
        topic = (art.get("topic") or "—")[:100]
        result = (art.get("result") or "")[:SAMPLE_MAX_CHARS]
        ts_iso = art.get("timestamp_iso") or ""
        verification = art.get("verification")
        v_note = ""
        if isinstance(verification, dict):
            passed = verification.get("passed")
            score = verification.get("score")
            if passed is not None:
                v_note = f" [verified={'✓' if passed else '✗'}"
                if score is not None:
                    v_note += f", score={score}"
                v_note += "]"
        sample_blocks.append(
            f"### Пример {i} ({ts_iso}){v_note}\n**Топик:** {topic}\n**Результат:**\n{result}"
        )

    samples_text = "\n\n".join(sample_blocks) if sample_blocks else "(нет артефактов)"

    return (
        f"{_ANALYZER_SYSTEM}\n\n"
        f"---\n\n"
        f"**Команда:** {team}\n\n"
        f"**Текущий системный промпт:**\n```\n{current_prompt}\n```\n\n"
        f"**Реальные примеры работы команды ({len(samples)} samples):**\n\n"
        f"{samples_text}\n\n"
        f"---\n\n"
        f"Верни JSON-массив с 3-5 конкретными предложениями по улучшению промпта команды {team}."
    )


# ---------------------------------------------------------------------------
# Парсинг ответа LLM
# ---------------------------------------------------------------------------

_JSON_ARR_RE = re.compile(r"\[.*\]", re.DOTALL)


def _parse_analyzer_response(raw: str) -> list[PromptSuggestion]:
    """Извлекает список PromptSuggestion из сырого ответа LLM.

    Tolerant к code fences, trailing text, partial JSON.
    При ошибках — возвращает [].
    """
    if not raw:
        return []

    text = raw.strip()
    # Убираем code fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)

    # Ищем JSON-массив
    match = _JSON_ARR_RE.search(text)
    if not match:
        logger.warning("skill_curator_analyzer_no_json_array", raw_len=len(raw))
        return []

    try:
        data = json.loads(match.group(0))
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("skill_curator_analyzer_json_parse_failed", error=str(exc))
        return []

    if not isinstance(data, list):
        return []

    suggestions: list[PromptSuggestion] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "other").strip()[:30]
        issue = str(item.get("issue") or "").strip()[:500]
        suggestion = str(item.get("suggestion") or "").strip()[:1000]
        priority = str(item.get("priority") or "medium").strip().lower()

        if priority not in ("high", "medium", "low"):
            priority = "medium"
        if not issue or not suggestion:
            continue

        suggestions.append(
            PromptSuggestion(
                category=category,
                issue=issue,
                suggestion=suggestion,
                priority=priority,
            )
        )

    # Ограничиваем до 5 предложений
    return suggestions[:5]


# ---------------------------------------------------------------------------
# Загрузка артефактов из файловой системы
# ---------------------------------------------------------------------------


def _load_team_artifacts(
    team: str,
    artifacts_dir: Path | None = None,
    *,
    limit: int = ARTIFACT_SAMPLE_SIZE,
) -> list[dict[str, Any]]:
    """Читает последние `limit` artifact-файлов для команды.

    Файлы имеют вид ``{team}_{timestamp}.json``.
    Сортируется по timestamp (часть имени файла) desc.

    Returns:
        Список словарей (parsed JSON). Пустой список если нет файлов.
    """
    base = artifacts_dir or ARTIFACTS_DIR
    if not base.exists():
        return []

    # Паттерн: {team}_{digits}.json
    prefix = f"{team.lower()}_"
    candidates = [
        fp for fp in base.iterdir() if fp.name.startswith(prefix) and fp.suffix == ".json"
    ]
    # Сортируем по timestamp в имени файла (desc)
    candidates.sort(key=lambda fp: fp.stem.split("_")[-1], reverse=True)

    results: list[dict[str, Any]] = []
    for fp in candidates[:limit]:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                results.append(data)
        except (OSError, ValueError) as exc:
            logger.debug("skill_curator_analyzer_artifact_read_error", path=str(fp), error=str(exc))

    return results


# ---------------------------------------------------------------------------
# Основная функция
# ---------------------------------------------------------------------------


async def analyze_team_prompts(
    team: str | None = None,
    *,
    artifacts_dir: Path | None = None,
    reports_dir: Path | None = None,
    provider: Any | None = None,
    model: str = "gemini-3-flash-preview",
    save_report: bool = True,
) -> AnalysisReport:
    """Анализирует prompts команды(й) через LLM и возвращает AnalysisReport.

    Step 2 — только читает и предлагает, никаких мутаций.

    Args:
        team:         имя команды или None для анализа всех 4 команд.
        artifacts_dir: override пути к артефактам (для тестов).
        reports_dir:   override пути к reports (для тестов).
        provider:      mock LLM провайдер (для тестов). Если None — lazy-init Gemini.
        model:         модель для LLM-вызова.
        save_report:   сохранять ли markdown-отчёт на диск.

    Returns:
        AnalysisReport с reports по каждой команде.
    """
    # Lazy import избегает circular deps в тестах
    from .swarm_team_prompts import get_team_system_prompt  # noqa: PLC0415

    teams_to_analyze = [team] if team else list(KNOWN_TEAMS)
    result = AnalysisReport(generated_at=_now_iso())

    # Resolve LLM provider
    resolved_provider = provider
    if resolved_provider is None:
        resolved_provider = _resolve_provider(model=model)

    for t in teams_to_analyze:
        t_lower = t.lower()
        report = await _analyze_single_team(
            team=t_lower,
            get_prompt_fn=lambda _t=t_lower: get_team_system_prompt(_t),
            artifacts_dir=artifacts_dir,
            provider=resolved_provider,
            model=model,
        )
        result.teams[t_lower] = report

    if save_report:
        _save_report(result, reports_dir=reports_dir)

    return result


async def _analyze_single_team(
    team: str,
    get_prompt_fn: Any,
    artifacts_dir: Path | None,
    provider: Any | None,
    model: str,
) -> TeamAnalysisReport:
    """Анализирует одну команду. Возвращает TeamAnalysisReport."""
    t0 = time.monotonic()
    current_prompt = get_prompt_fn()
    samples = _load_team_artifacts(team, artifacts_dir)

    report = TeamAnalysisReport(
        team=team,
        current_prompt=current_prompt,
        artifacts_sampled=len(samples),
        generated_at=_now_iso(),
    )

    if provider is None:
        report.error = "LLM провайдер недоступен (нет API-ключа или импорт не удался)"
        logger.warning("skill_curator_analyzer_no_provider", team=team)
        return report

    prompt_text = _build_analyzer_prompt(team, current_prompt, samples)

    # LLM-вызов best-effort
    raw_text = ""
    try:
        import asyncio  # noqa: PLC0415

        raw_text = await asyncio.wait_for(provider.generate(prompt_text), timeout=LLM_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        report.error = f"LLM-вызов завершился ошибкой: {exc}"
        logger.warning("skill_curator_analyzer_llm_error", team=team, error=str(exc))
        return report

    report.raw_llm_response = raw_text
    suggestions = _parse_analyzer_response(raw_text)
    report.suggestions = suggestions

    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    logger.info(
        "skill_curator_analyzer_done",
        team=team,
        artifacts_sampled=len(samples),
        suggestions=len(suggestions),
        elapsed_ms=elapsed_ms,
    )
    return report


# ---------------------------------------------------------------------------
# Сохранение отчёта
# ---------------------------------------------------------------------------


def _save_report(report: AnalysisReport, *, reports_dir: Path | None = None) -> Path:
    """Сохраняет markdown-отчёт в ``skill_curator_reports/<ts>-all.md``."""
    base = reports_dir or REPORTS_DIR
    base.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M-%S")
    teams_slug = "-".join(sorted(report.teams.keys()))[:30] or "all"
    filename = f"{ts}-{teams_slug}.md"
    path = base / filename

    markdown = report.to_markdown()
    try:
        path.write_text(markdown, encoding="utf-8")
        report.report_path = str(path)
        logger.info("skill_curator_analyzer_report_saved", path=str(path))
    except OSError as exc:
        logger.warning("skill_curator_analyzer_report_save_failed", error=str(exc))

    return path


# ---------------------------------------------------------------------------
# Resolve LLM provider (lazy, best-effort)
# ---------------------------------------------------------------------------


def _resolve_provider(*, model: str = "gemini-3-flash-preview") -> Any | None:
    """Инициализирует GeminiRerankProvider. Возвращает None при ошибке."""
    try:
        from .gemini_rerank_provider import default_provider as _default  # noqa: PLC0415

        return _default(model=model, timeout=LLM_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        logger.warning("skill_curator_analyzer_provider_init_failed", error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
