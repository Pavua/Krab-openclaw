# -*- coding: utf-8 -*-
"""
src/core/skill_curator.py
~~~~~~~~~~~~~~~~~~~~~~~~~

SkillCurator — Wave 14-I (Step 1/4): dry-run analyzer (read-only).

Анализирует последние раунды команд свёрма и формирует ``CuratorReport``
без мутаций состояния и без вызовов LLM. Источники данных:

- ``swarm_memory`` — FIFO 50 раундов на команду (`SwarmRunRecord`),
  metadata содержит artifact_path / verifier score / reactions (при наличии).
- ``swarm_artifact_store`` — файлы артефактов с полем `verification`
  (``{passed, score, issues, suggestions}``).

Дизайн в `docs/architecture/SKILL_CURATOR_DESIGN.md`. Здесь намеренно реализована
только Step 1 — отчёт-анализ без LLM, A/B и mutations. Последующие шаги
(Sessions 35-37) добавят `propose_prompt_update`, `apply_with_approval`,
`rollback`, A/B framework и cron triggers.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger
from .swarm_artifact_store import swarm_artifact_store as default_artifact_store
from .swarm_memory import SwarmRunRecord
from .swarm_memory import swarm_memory as default_memory

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Storage layout (per design § Storage schema)
# ---------------------------------------------------------------------------

CURATOR_BASE_DIR = Path.home() / ".openclaw" / "krab_runtime_state" / "curator"
CURATOR_REPORTS_DIR = CURATOR_BASE_DIR / "reports"
CURATOR_STATE_PATH = CURATOR_BASE_DIR / "state.json"


def ensure_curator_dirs(base: Path | None = None) -> Path:
    """Создаёт `~/.openclaw/krab_runtime_state/curator/{reports,prompts_archive,ab_tests}`.

    Не пишет state.json (схема приведена в design doc; dry-run не мутирует
    состояние), только разворачивает директории — этого достаточно, чтобы
    последующая запись отчёта в reports/ не споткнулась.
    """

    root = base or CURATOR_BASE_DIR
    for sub in ("reports", "prompts_archive", "ab_tests"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# Failure pattern signatures — простой regex-кластеринг для error-сообщений
# ---------------------------------------------------------------------------

_FAILURE_SIGNATURES: list[tuple[str, re.Pattern[str]]] = [
    ("timeout", re.compile(r"\b(timeout|timed?\s*out|deadline\s+exceeded)\b", re.IGNORECASE)),
    (
        "provider_unavailable",
        re.compile(
            r"\b(provider[_\s-]*unavailable|503|service\s+unavailable|upstream)\b",
            re.IGNORECASE,
        ),
    ),
    ("rate_limit", re.compile(r"\b(rate[_\s-]*limit(ed)?|429|quota|flood)\b", re.IGNORECASE)),
    (
        "auth",
        re.compile(r"\b(401|403|unauthori[sz]ed|forbidden|invalid[_\s]*key)\b", re.IGNORECASE),
    ),
    ("no_data", re.compile(r"\b(no[_\s-]*data|empty|нет\s+данных|пуст)\b", re.IGNORECASE)),
    ("network", re.compile(r"\b(network|connection|dns|socket|refused)\b", re.IGNORECASE)),
    (
        "parse_error",
        re.compile(r"\b(parse|decode|json|malformed|invalid\s+(json|response))\b", re.IGNORECASE),
    ),
    (
        "tool_error",
        re.compile(r"\b(tool[_\s-]*(error|failed)|execution\s+failed)\b", re.IGNORECASE),
    ),
    (
        "model_error",
        re.compile(
            r"\b(model[_\s-]*(error|unavailable|overloaded)|inference[_\s-]*failed)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "internal_error",
        re.compile(r"\b(internal\s+error|500|traceback|exception)\b", re.IGNORECASE),
    ),
]


# Слова, исключаемые из анализа success patterns
_STOPWORDS = frozenset(
    {
        "это",
        "что",
        "как",
        "для",
        "при",
        "был",
        "была",
        "были",
        "есть",
        "или",
        "так",
        "если",
        "тоже",
        "также",
        "того",
        "тому",
        "этом",
        "этих",
        "этой",
        "только",
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "from",
        "about",
        "would",
        "could",
        "should",
        "have",
        "been",
        "will",
        "into",
        "than",
        "more",
        "less",
        "very",
        "some",
        "any",
        "all",
        "out",
        "use",
        "via",
        "per",
        "ok",
        "ok.",
    }
)


# ---------------------------------------------------------------------------
# CuratorReport
# ---------------------------------------------------------------------------


@dataclass
class CuratorReport:
    """Read-only анализ последних раундов команды.

    `proposed_prompt` / `metric_delta_estimate` / `confidence` — placeholders
    для последующих шагов (Step 2-4 не реализованы в dry-run).
    """

    team: str
    rounds_analyzed: int
    success_rate: float
    failure_patterns: list[tuple[str, int]] = field(default_factory=list)
    successful_patterns: list[tuple[str, int]] = field(default_factory=list)
    distinct_topics: int = 0
    recurring_failure_tags: list[str] = field(default_factory=list)
    window_days: int = 7
    proposed_prompt: str = ""
    metric_delta_estimate: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    generated_at: str = ""

    def to_markdown(self) -> str:
        """Форматирует отчёт в markdown-блок для Telegram / файла."""

        if self.rounds_analyzed == 0:
            return (
                f"## SkillCurator dry-run — {self.team} (last {self.window_days} days)\n"
                f"- Rounds analyzed: 0\n"
                f"- Нет данных за указанный период.\n"
            )

        success_pct = round(self.success_rate * 100, 1)
        succ_count = round(self.success_rate * self.rounds_analyzed)
        fail_top = ", ".join(f"{tag} ({cnt}×)" for tag, cnt in self.failure_patterns[:3]) or "—"
        succ_top = ", ".join(f'"{kw}" ({cnt}×)' for kw, cnt in self.successful_patterns[:3]) or "—"

        lines = [
            f"## SkillCurator dry-run — {self.team} (last {self.window_days} days)",
            f"- Rounds analyzed: {self.rounds_analyzed}",
            f"- Success rate: {success_pct}% ({succ_count}/{self.rounds_analyzed})",
            f"- Distinct topics: {self.distinct_topics}",
            f"- Top failure patterns: {fail_top}",
            f"- Top success patterns: {succ_top}",
        ]
        if self.recurring_failure_tags:
            lines.append(
                f"- Recommendation: prompt review focus на {', '.join(self.recurring_failure_tags)}"
            )
        else:
            lines.append("- Recommendation: команда работает стабильно, prompt review не требуется")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# SkillCurator
# ---------------------------------------------------------------------------


class SkillCurator:
    """Read-only куратор команд свёрма.

    Step 1/4 (Wave 14-I): только `analyze_recent_rounds`. Method'ы
    `propose_prompt_update / apply_with_approval / rollback` оставлены
    схемами — реализация в Sessions 35-37.
    """

    def __init__(
        self,
        *,
        memory: Any | None = None,
        artifact_store: Any | None = None,
        base_dir: Path | None = None,
    ) -> None:
        self._memory = memory or default_memory
        self._artifacts = artifact_store or default_artifact_store
        self._base_dir = base_dir or CURATOR_BASE_DIR

    # -- public API ----------------------------------------------------------

    def analyze_recent_rounds(self, team: str, days: int = 7) -> CuratorReport:
        """Анализирует последние `days` дней раундов команды.

        Read-only. Не пишет в state.json и не мутирует prompts.
        """

        t0 = time.monotonic()
        records = list(self._memory.get_recent(team, count=50))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        windowed = [r for r in records if _record_within(r, cutoff)]

        if not windowed:
            report = CuratorReport(
                team=team,
                rounds_analyzed=0,
                success_rate=0.0,
                window_days=days,
                generated_at=_now_iso(),
            )
            logger.info("skill_curator_dry_run_empty", team=team, days=days)
            return report

        # Артефакты для верификационных метрик. List comprehension не fail'ит на ошибках —
        # метаданные внутри SwarmRunRecord достаточно как fallback.
        try:
            artifacts = self._artifacts.list_artifacts(team=team, limit=200)
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill_curator_artifacts_unavailable", error=str(exc))
            artifacts = []

        success_count = 0
        failure_messages: list[str] = []
        success_topics: list[str] = []

        for rec in windowed:
            ok, fail_text = _classify_round(rec, artifacts)
            if ok:
                success_count += 1
                success_topics.append(rec.topic)
                # Успех тоже бывает «слабым» — оставляем result для keyword mining
                success_topics.append(rec.result_summary[:300])
            else:
                failure_messages.append(fail_text or rec.result_summary)

        failure_patterns = _cluster_failure_patterns(failure_messages)
        successful_patterns = _extract_success_keywords(success_topics)
        topics = {r.topic.strip().lower() for r in windowed if r.topic.strip()}
        recurring_tags = [tag for tag, cnt in failure_patterns if cnt >= 2][:3]

        report = CuratorReport(
            team=team,
            rounds_analyzed=len(windowed),
            success_rate=round(success_count / len(windowed), 4),
            failure_patterns=failure_patterns[:3],
            successful_patterns=successful_patterns[:3],
            distinct_topics=len(topics),
            recurring_failure_tags=recurring_tags,
            window_days=days,
            generated_at=_now_iso(),
        )

        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.info(
            "skill_curator_dry_run_done",
            team=team,
            rounds=report.rounds_analyzed,
            success_rate=report.success_rate,
            elapsed_ms=elapsed_ms,
        )
        return report

    def render_combined_markdown(self, reports: list[CuratorReport]) -> str:
        """Конкатенация нескольких отчётов в один markdown-блок."""

        return "\n\n".join(r.to_markdown() for r in reports if r is not None)

    def save_report(self, team: str, markdown: str) -> Path:
        """Сохраняет отчёт в `reports/{YYYY-MM-DD-team}.md`. Создаёт каталоги."""

        ensure_curator_dirs(self._base_dir)
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        safe_team = re.sub(r"[^a-z0-9_-]", "_", team.lower())[:20] or "all"
        path = self._base_dir / "reports" / f"{date}-{safe_team}.md"
        try:
            path.write_text(markdown, encoding="utf-8")
        except OSError as exc:
            logger.warning("skill_curator_report_save_failed", team=team, error=str(exc))
        return path

    # -- placeholders для Step 2-4 ------------------------------------------
    # NB: эти методы намеренно не реализованы — Wave 14-I Step 1/4. См. design doc.

    async def propose_prompt_update(
        self, team: str, report: CuratorReport
    ) -> str:  # pragma: no cover - placeholder
        raise NotImplementedError("Step 3 (Session 35) — LLM-проporal.")

    async def apply_with_approval(
        self, team: str, new_prompt: str, *, force: bool = False
    ) -> bool:  # pragma: no cover - placeholder
        raise NotImplementedError("Step 4 (Session 36-37) — apply with archive.")

    async def rollback(
        self, team: str, version: int = -1
    ) -> bool:  # pragma: no cover - placeholder
        raise NotImplementedError("Step 4 (Session 36-37) — rollback prompt archive.")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record_within(rec: SwarmRunRecord, cutoff: datetime) -> bool:
    """Возвращает True если `rec.created_at >= cutoff`. Tolerant к bad data."""

    raw = rec.created_at or ""
    if not raw:
        return True  # без метки берём в окно — иначе теряем недавние записи
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= cutoff


def _classify_round(rec: SwarmRunRecord, artifacts: list[dict[str, Any]]) -> tuple[bool, str]:
    """Возвращает `(success, failure_text)`.

    Приоритет:
    1. `metadata['verifier']` или `metadata['verifier_pass']`.
    2. `metadata['reaction']` (👍/👎) если есть.
    3. Совпадение `result_summary` с error-паттернами → fail.
    4. Иначе — success (heuristic positive).
    """

    md = rec.metadata or {}
    verifier = md.get("verifier") or md.get("verification") or {}
    if isinstance(verifier, dict):
        if "passed" in verifier:
            ok = bool(verifier.get("passed"))
            return ok, "" if ok else _truncate(rec.result_summary)
        if "score" in verifier:
            try:
                score = float(verifier.get("score") or 0)
                if score >= 0.6:
                    return True, ""
                if score < 0.4:
                    return False, _truncate(rec.result_summary)
            except (TypeError, ValueError):
                pass

    reaction = md.get("reaction")
    if reaction in {"👍", "ok", "positive", True}:
        return True, ""
    if reaction in {"👎", "fail", "negative", False}:
        return False, _truncate(rec.result_summary)

    # Cross-check artifact store по run_id если возможно
    run_id = rec.run_id or ""
    if run_id:
        for art in artifacts:
            path = art.get("_path", "")
            if run_id in path or art.get("topic") == rec.topic:
                v = art.get("verification") or {}
                if isinstance(v, dict) and "passed" in v:
                    ok = bool(v.get("passed"))
                    return ok, "" if ok else _truncate(rec.result_summary)
                break

    # Регекс-эвристика по тексту (последний резерв)
    summary = rec.result_summary or ""
    for tag, pat in _FAILURE_SIGNATURES:
        if pat.search(summary):
            return False, summary
    return True, ""


def _truncate(text: str, n: int = 400) -> str:
    text = (text or "").strip()
    return text[:n]


def _cluster_failure_patterns(messages: list[str]) -> list[tuple[str, int]]:
    """Кластеринг error-сообщений по `_FAILURE_SIGNATURES`. Возвращает (tag, count) sorted desc."""

    counter: Counter[str] = Counter()
    for msg in messages:
        if not msg:
            continue
        for tag, pat in _FAILURE_SIGNATURES:
            if pat.search(msg):
                counter[tag] += 1
                break
        else:
            counter["other"] += 1
    return counter.most_common()


_WORD_RE = re.compile(r"[A-Za-zА-Яа-яёЁ][A-Za-zА-Яа-яёЁ_-]{3,}")


def _extract_success_keywords(texts: list[str]) -> list[tuple[str, int]]:
    """Простой keyword counter с stopword-фильтром. Топ слов из success-текстов."""

    counter: Counter[str] = Counter()
    for text in texts:
        if not text:
            continue
        for token in _WORD_RE.findall(text.lower()):
            if token in _STOPWORDS or len(token) < 4:
                continue
            counter[token] += 1
    return counter.most_common(10)


# Singleton
skill_curator = SkillCurator()
