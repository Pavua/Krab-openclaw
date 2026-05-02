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

import difflib
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger
from .skill_curator_state import CURATOR_STATE_PATH, CuratorState
from .swarm_artifact_store import swarm_artifact_store as default_artifact_store
from .swarm_memory import SwarmRunRecord
from .swarm_memory import swarm_memory as default_memory

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Storage layout (per design § Storage schema)
# ---------------------------------------------------------------------------

CURATOR_BASE_DIR = Path.home() / ".openclaw" / "krab_runtime_state" / "curator"
CURATOR_REPORTS_DIR = CURATOR_BASE_DIR / "reports"
CURATOR_PROPOSALS_DIR = CURATOR_BASE_DIR / "proposals"


def ensure_curator_dirs(base: Path | None = None) -> Path:
    """Создаёт `~/.openclaw/krab_runtime_state/curator/{reports,prompts_archive,ab_tests,proposals}`.

    Не пишет state.json (atomic write через `CuratorState.save_atomic`), только
    разворачивает директории — этого достаточно, чтобы последующая запись
    отчёта/proposal не споткнулась.
    """

    root = base or CURATOR_BASE_DIR
    for sub in ("reports", "prompts_archive", "ab_tests", "proposals"):
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
# PromptProposal — Step 2 dataclass (LLM-предложение изменения промпта)
# ---------------------------------------------------------------------------


@dataclass
class PromptProposal:
    """LLM-сгенерированное предложение апдейта team prompt.

    Хранится в `~/.openclaw/.../curator/proposals/{team}-{ts}.json`
    и маркируется как **pending** до явного approve (Step 3).

    Поля:
    - ``team``           — имя команды (traders/coders/analysts/creative).
    - ``current_prompt`` — snapshot текущего системного промпта.
    - ``proposed_prompt`` — полный новый текст промпта.
    - ``proposed_diff``  — unified diff (current → proposed).
    - ``rationale``      — короткое обоснование от LLM.
    - ``focus``          — массив тегов (`failure_pattern`, `success_pattern`).
    - ``confidence``     — 0..1, эвристика на success_rate + наличие diff.
    - ``model``          — fully-qualified model id (e.g. gemini-3-flash-preview).
    - ``status``         — pending|approved|rejected|applied.
    - ``proposal_id``    — `{team}-{YYYY-MM-DD-HH-MM}` для CLI ссылок.
    """

    team: str
    current_prompt: str = ""
    proposed_prompt: str = ""
    proposed_diff: str = ""
    rationale: str = ""
    focus: list[str] = field(default_factory=list)
    confidence: float = 0.0
    model: str = ""
    status: str = "pending"
    proposal_id: str = ""
    generated_at: str = ""
    report_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "team": self.team,
            "current_prompt": self.current_prompt,
            "proposed_prompt": self.proposed_prompt,
            "proposed_diff": self.proposed_diff,
            "rationale": self.rationale,
            "focus": list(self.focus),
            "confidence": self.confidence,
            "model": self.model,
            "status": self.status,
            "proposal_id": self.proposal_id,
            "generated_at": self.generated_at,
            "report_summary": dict(self.report_summary),
        }


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

    # -- Step 2/4 (Wave 15-C): LLM proposer ---------------------------------

    async def propose_prompt_update(
        self,
        team: str,
        current_prompt: str,
        report: CuratorReport,
        *,
        provider: Any | None = None,
        model: str = "gemini-3-flash-preview",
    ) -> "PromptProposal | None":
        """Аux Gemini-3-flash предлагает diff текущего промпта.

        Best-effort: если provider недоступен / LLM-вызов падает → `None` +
        warning в лог (никаких raise наружу). Сохраняет proposal в
        ``proposals/{team}-{YYYY-MM-DD-HH-MM}.json`` и обновляет CuratorState.

        Args:
            team: имя команды.
            current_prompt: текущий системный промпт команды.
            report: результат `analyze_recent_rounds`.
            provider: тестовый override (mock LLM). Если ``None`` — берётся
                ``gemini_rerank_provider.default_provider()``.
            model: fully-qualified model id.

        Returns:
            ``PromptProposal`` (status=``pending``) или ``None`` если LLM-вызов
            не удался / API key отсутствует.
        """

        ensure_curator_dirs(self._base_dir)

        # 1. Resolve provider (lazy import избегает circular deps на тестах)
        if provider is None:
            try:
                from .gemini_rerank_provider import default_provider as _default

                provider = _default(model=model, timeout=15.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("curator_provider_resolve_failed", error=str(exc))
                provider = None

        if provider is None:
            logger.warning("curator_propose_skipped_no_provider", team=team)
            return None

        prompt_text = _build_proposer_prompt(team, current_prompt, report)

        # 2. Вызов LLM (best-effort)
        raw_text = ""
        try:
            raw_text = await provider.generate(prompt_text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("curator_propose_llm_failed", team=team, error=str(exc))
            return None

        parsed = _parse_proposer_response(raw_text, current_prompt)
        if not parsed:
            logger.warning("curator_propose_parse_failed", team=team, raw_len=len(raw_text or ""))
            return None

        proposed_prompt = parsed.get("proposed_prompt") or current_prompt
        proposed_diff = _unified_diff(current_prompt, proposed_prompt)
        rationale = parsed.get("rationale", "").strip()
        focus = parsed.get("focus") or _derive_focus(report)

        # Confidence — простая эвристика: low success_rate → выше уверенность
        # что нужно менять, но cap по наличию diff.
        confidence = _estimate_confidence(report, proposed_diff)

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M")
        proposal_id = f"{_safe_team(team)}-{ts}"

        proposal = PromptProposal(
            team=team,
            current_prompt=current_prompt,
            proposed_prompt=proposed_prompt,
            proposed_diff=proposed_diff,
            rationale=rationale,
            focus=list(focus)[:5],
            confidence=confidence,
            model=model,
            status="pending",
            proposal_id=proposal_id,
            generated_at=_now_iso(),
            report_summary={
                "rounds_analyzed": report.rounds_analyzed,
                "success_rate": report.success_rate,
                "failure_patterns": list(report.failure_patterns),
                "successful_patterns": list(report.successful_patterns),
            },
        )

        # 3. Persist proposal JSON + state
        proposal_path = self._save_proposal(proposal)
        try:
            state = CuratorState.load(CURATOR_STATE_PATH)
            state.mark_run(team)
            state.mark_proposal(team, proposal_path)
            state.save_atomic(CURATOR_STATE_PATH)
        except Exception as exc:  # noqa: BLE001
            logger.warning("curator_state_update_failed", team=team, error=str(exc))

        logger.info(
            "curator_proposal_saved",
            team=team,
            proposal_id=proposal_id,
            confidence=confidence,
            diff_lines=proposed_diff.count("\n"),
        )
        return proposal

    def _save_proposal(self, proposal: "PromptProposal") -> Path:
        """Сохраняет proposal JSON в ``proposals/{proposal_id}.json``."""

        ensure_curator_dirs(self._base_dir)
        path = self._base_dir / "proposals" / f"{proposal.proposal_id}.json"
        try:
            path.write_text(
                json.dumps(proposal.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                "curator_proposal_write_failed",
                team=proposal.team,
                error=str(exc),
            )
        return path

    def list_proposals(self, team: str | None = None) -> list[dict[str, Any]]:
        """Возвращает список pending/historical proposals (newest first)."""

        proposals_dir = self._base_dir / "proposals"
        if not proposals_dir.exists():
            return []
        entries: list[dict[str, Any]] = []
        for fp in sorted(proposals_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if team and (data.get("team") or "").lower() != team.lower():
                continue
            data["_path"] = str(fp)
            entries.append(data)
        return entries

    def load_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        """Читает proposal по id (без расширения). None если не найден."""

        path = self._base_dir / "proposals" / f"{proposal_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        data["_path"] = str(path)
        return data

    # -- placeholders для Step 3-4 ------------------------------------------

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


# ---------------------------------------------------------------------------
# Step 2 helpers — proposer prompt building + response parsing
# ---------------------------------------------------------------------------


_PROPOSER_SYSTEM = (
    "Ты — auxiliary skill curator для Telegram userbot Krab. "
    "Анализируешь работу команды свёрма и предлагаешь точечный апдейт системного промпта. "
    "Минимально-инвазивные правки: добавь 1-3 предложения, корректирующие конкретные failure patterns. "
    "Не переписывай промпт целиком. Не удаляй существующую специализацию команды. "
    "Отвечай строго JSON-объектом с ключами: proposed_prompt (string), rationale (string), focus (array of short strings)."
)


def _build_proposer_prompt(team: str, current_prompt: str, report: "CuratorReport") -> str:
    """Формирует prompt для aux-Gemini, включая report context."""

    fail_lines = (
        "\n".join(f"- {tag}: {cnt}×" for tag, cnt in (report.failure_patterns or []))
        or "(нет повторяющихся failure patterns)"
    )
    succ_lines = (
        "\n".join(f"- {kw}: {cnt}×" for kw, cnt in (report.successful_patterns or []))
        or "(нет ярких success patterns)"
    )
    success_pct = round((report.success_rate or 0.0) * 100, 1)

    context = (
        f"Команда: {team}\n"
        f"Окно анализа: {report.window_days} дней, {report.rounds_analyzed} раундов\n"
        f"Success rate: {success_pct}%\n"
        f"Distinct topics: {report.distinct_topics}\n\n"
        f"Failure patterns:\n{fail_lines}\n\n"
        f"Successful patterns:\n{succ_lines}\n\n"
        f"Recurring failure tags: {', '.join(report.recurring_failure_tags) or '—'}\n\n"
        f"Текущий системный промпт:\n```\n{current_prompt}\n```\n\n"
        "Задача: предложи минимальный апдейт системного промпта, чтобы снизить "
        "повторяющиеся ошибки и закрепить успешные паттерны.\n"
        "Верни строго JSON: "
        '{"proposed_prompt": "...", "rationale": "...", "focus": ["...", "..."]}'
    )
    return f"{_PROPOSER_SYSTEM}\n\n{context}"


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_proposer_response(raw: str, current_prompt: str) -> dict[str, Any] | None:
    """Извлекает JSON из ответа LLM. Tolerant к code fences и trailing text."""

    if not raw:
        return None
    text = raw.strip()
    # Удаляем code fences ```json ... ```
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    # Берём первый JSON-блок если LLM добавил пояснения
    match = _JSON_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    proposed = (data.get("proposed_prompt") or "").strip()
    if not proposed:
        return None
    # Sanity: proposal не должен быть длиннее текущего × 4 (anti-hallucination)
    if len(proposed) > max(len(current_prompt) * 4, 4000):
        proposed = proposed[: max(len(current_prompt) * 4, 4000)]
    focus = data.get("focus") or []
    if not isinstance(focus, list):
        focus = [str(focus)]
    focus = [str(x).strip()[:60] for x in focus if str(x).strip()]
    return {
        "proposed_prompt": proposed,
        "rationale": str(data.get("rationale") or "").strip()[:1000],
        "focus": focus,
    }


def _unified_diff(before: str, after: str) -> str:
    """Возвращает unified diff (current → proposed) одной строкой."""

    before_lines = (before or "").splitlines(keepends=True)
    after_lines = (after or "").splitlines(keepends=True)
    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile="current",
        tofile="proposed",
        lineterm="",
        n=2,
    )
    return "\n".join(diff)


def _derive_focus(report: "CuratorReport") -> list[str]:
    """Fallback focus tags если LLM их не вернул — строим из report."""

    focus = list(report.recurring_failure_tags or [])
    if not focus and report.failure_patterns:
        focus = [tag for tag, _ in report.failure_patterns[:2]]
    return focus[:3]


def _estimate_confidence(report: "CuratorReport", diff: str) -> float:
    """Эвристика confidence для proposal.

    - Низкий success_rate + есть diff → уверенность выше (нужен апдейт).
    - Diff пустой → 0.0 (нечего apply).
    - Малый sample (< 5 раундов) → cap 0.5.
    """

    if not diff.strip():
        return 0.0
    if report.rounds_analyzed < 5:
        base = 0.4
    else:
        base = 0.6 + (1.0 - max(0.0, min(1.0, report.success_rate))) * 0.3
    return round(min(0.95, base), 3)


def _safe_team(team: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "_", (team or "").lower())[:20] or "team"


# Singleton
skill_curator = SkillCurator()
